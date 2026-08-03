"""Review-fix regression tests (2026-08-03, codex/review-fixes).

Covers the P1/P2 review findings:
- cache validation: unrecognized statuses and empty extraction lists must not
  be treated as valid (cacheable) results; known benign statuses stay valid.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import database
from app import main
from app import model_client
from app.database import (
    connect,
    create_analysis_job,
    get_analysis_job,
    import_novel,
    list_chapters,
    update_analysis_job,
    upsert_extracted_fact,
)
from app.jobs import conflicts
from app.jobs import qa
from app.jobs import runner
from app.jobs import settings_extraction
from app.text_processing import ChapterDraft, sha256_text


def test_invalid_output_reason_rejects_unrecognized_status():
    """P1: unknown statuses must not pass the cacheability check."""
    reason = main._invalid_output_reason(
        {"status": "weird_unknown", "characters": [{"name": "A"}]},
        "character_extraction",
    )
    assert reason is not None
    assert "unrecognized status" in reason


def test_invalid_output_reason_accepts_known_benign_statuses():
    """P1: statuses the pipeline itself writes stay cacheable."""
    for status in ("ok", "local_fallback", "partial"):
        output = {"status": status, "characters": [{"name": "A"}]}
        assert main._invalid_output_reason(output, "character_extraction") is None, status


def test_invalid_output_reason_rejects_empty_character_extraction():
    """P1: an empty character list is never a valid extraction result."""
    reason = main._invalid_output_reason({"status": "ok", "characters": []}, "character_extraction")
    assert reason is not None
    assert "empty" in reason


def test_invalid_output_reason_rejects_empty_relationship_extraction():
    """P1: an empty relationship list is never a valid extraction result."""
    reason = main._invalid_output_reason(
        {"status": "ok", "relationships": []}, "relationship_extraction"
    )
    assert reason is not None
    assert "empty" in reason


def test_invalid_output_reason_accepts_empty_conflict_detection():
    """P1: "no conflicts found" is a legitimate conflict-detection outcome."""
    assert main._invalid_output_reason({"status": "ok", "conflicts": []}, "conflict_detection") is None


def test_invalid_output_reason_accepts_sparse_settings_and_events():
    """P1: settings/events may legitimately be empty for a given batch."""
    assert main._invalid_output_reason({"status": "ok", "settings": []}, "setting_extraction") is None
    assert main._invalid_output_reason({"status": "ok", "events": []}, "event_extraction") is None


def test_invalid_output_reason_rejects_unknown_status_even_with_valid_list():
    """P1: a valid list does not redeem an unrecognized status."""
    reason = main._invalid_output_reason(
        {"status": "empty", "characters": [{"name": "A"}]},
        "character_extraction",
    )
    assert reason is not None


def test_qa_full_scan_gated_by_quote_intent(tmp_path: Path, monkeypatch):
    """P1: 无引文意图词的普通提问只在候选章节内检索（不触发全书行级扫描），
    带"这句话/出自"等意图词的问题仍强制全文扫描。"""
    captured: list[int] = []

    async def fake_model_call(**kwargs):
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "qa_gate.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(main.app)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    chapters = [
        ChapterDraft(order=order, title=f"第{order}章", content=f"普通铺垫内容 {order}。")
        for order in range(1, 15)
    ]
    chapters[11] = ChapterDraft(order=12, title="第十二章", content="孙悟空大闹天宫，打翻八卦炉。")
    with main.db() as conn:
        imported = import_novel(
            conn,
            title="QaGate",
            source_filename="qg.txt",
            encoding="utf-8",
            text_hash=sha256_text("QaGate fixture"),
            chapters=chapters,
        )
        rows = list_chapters(conn, imported["id"])
        target_id = next(int(r["id"]) for r in rows if int(r["chapter_order"]) == 12)
        upsert_extracted_fact(
            conn,
            novel_id=imported["id"],
            fact_type="event",
            content="大闹天宫: 孙悟空打上天庭",
            entities=["孙悟空", "大闹天宫"],
            chapter_id=target_id,
            source_quote="孙悟空大闹天宫，打翻八卦炉。",
            confidence="high",
            status="pending_review",
            evidence=[{"chapter_title": "第十二章", "source_quote": "孙悟空大闹天宫，打翻八卦炉。"}],
        )
        total = len(list_chapters(conn, imported["id"]))

    real_retrieve = qa._retrieve_evidence

    def spy(conn, chapter_rows, question, limit=6):
        captured.append(len(chapter_rows))
        return real_retrieve(conn, chapter_rows, question, limit)

    monkeypatch.setattr(qa, "_retrieve_evidence", spy)

    # 普通提问：长串存在但无引文意图词 → 只在候选章节内模糊匹配。
    first = client.post(
        f"/novels/{imported['id']}/qa",
        json={"model": "gpt-test", "question": "大闹天宫是发生在什么时候的事情，主人公是谁？"},
    )
    assert first.status_code == 200
    assert captured and captured[0] < total, f"ordinary question must not full-scan: {captured}"

    # 引文意图问题：意图词 + 引文候选 → 强制全文行级扫描。
    second = client.post(
        f"/novels/{imported['id']}/qa",
        json={"model": "gpt-test", "question": "这句话孙悟空大闹天宫出自哪里"},
    )
    assert second.status_code == 200
    assert len(captured) >= 2 and captured[1] == total, f"quote question must full-scan: {captured}"


def test_cancelled_job_run_is_skipped(tmp_path: Path, monkeypatch):
    """P2: 取消后的 job 不再执行（守卫返回 skipped，状态保持 cancelled）。"""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "guard.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(main.app)
    with main.db() as conn:
        job = create_analysis_job(conn, task_type="chapter_summary", request={"model": "gpt-test"})
        job_id = int(job["id"])
        update_analysis_job(conn, job_id, status="cancelled")
    response = client.post(f"/analysis-jobs/{job_id}/run")
    assert response.status_code == 200
    data = response.json()
    assert data["skipped"] is True
    assert data["status"] == "cancelled"
    with main.db() as conn:
        assert get_analysis_job(conn, job_id)["status"] == "cancelled"


def test_run_analysis_job_unexpected_error_marks_job_failed(tmp_path: Path, monkeypatch):
    """P2: 未预期的执行异常把 job 标记为 failed，而不是永久卡在 queued/running。"""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "guard_err.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(main.app)

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "_run_analysis_job", boom)
    with main.db() as conn:
        job = create_analysis_job(conn, task_type="chapter_summary", request={})
        job_id = int(job["id"])
    response = client.post(f"/analysis-jobs/{job_id}/run")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    deadline = time.monotonic() + 10
    while True:
        with main.db() as conn:
            failed = get_analysis_job(conn, job_id)
        if failed["status"] in ("failed", "completed"):
            break
        assert time.monotonic() < deadline, failed["status"]
        time.sleep(0.05)
    assert failed["status"] == "failed"
    assert "boom" in str(failed.get("error") or "")


def test_run_job_in_background_marks_failed_and_logs(tmp_path: Path, monkeypatch, caplog):
    """P3: 后台任务异常时 job 标 failed 且原始错误落日志。"""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "bg.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with main.db() as conn:
        job = create_analysis_job(conn, task_type="chapter_summary", request={})
        job_id = int(job["id"])

    def boom(*_args, **_kwargs):
        raise RuntimeError("bg-boom")

    monkeypatch.setattr(main, "_run_analysis_job", boom)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        asyncio.run(main._run_job_in_background(job_id))
    with main.db() as conn:
        failed = get_analysis_job(conn, job_id)
    assert failed["status"] == "failed"
    assert "bg-boom" in str(failed.get("error") or "")
    assert any("analysis job" in record.getMessage() for record in caplog.records)
    assert "bg-boom" in caplog.text


def test_run_job_in_background_logs_when_marking_failed_also_fails(
    tmp_path: Path, monkeypatch, caplog
):
    """P3: 标记 failed 时二次开库失败也留日志, 原始异常不丢失。"""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "bg2.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with main.db() as conn:
        job = create_analysis_job(conn, task_type="chapter_summary", request={})
        job_id = int(job["id"])

    def boom(*_args, **_kwargs):
        raise RuntimeError("bg-boom")

    monkeypatch.setattr(main, "_run_analysis_job", boom)
    real_connect = main.connect
    calls = {"n": 0}

    def flaky_connect(path):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("second-open-fail")
        return real_connect(path)

    monkeypatch.setattr(main, "connect", flaky_connect)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        asyncio.run(main._run_job_in_background(job_id))
    messages = [record.getMessage() for record in caplog.records]
    assert any("original error: bg-boom" in message for message in messages)
    assert "second-open-fail" in caplog.text


def test_conflict_candidate_titles_are_chinese():
    """P2: 物品/能力与剧情逻辑冲突候选标题为中文。"""
    item_facts = [
        {
            "id": 1, "fact_type": "setting_fact", "content": "聚灵丹: 3颗",
            "entities": ["聚灵丹"], "extra": {"chapter_order": 1},
            "evidence": [{"chapter_order": 1, "source_quote": "3颗"}],
        },
        {
            "id": 2, "fact_type": "setting_fact", "content": "聚灵丹: 5颗",
            "entities": ["聚灵丹"], "extra": {"chapter_order": 2},
            "evidence": [{"chapter_order": 2, "source_quote": "5颗"}],
        },
    ]
    item_out = conflicts._detect_item_ability_conflicts(item_facts)
    assert item_out and item_out[0]["title"].startswith("物品/能力冲突：")
    assert item_out[0]["type"] == "item_ability"

    death = {
        "id": 3, "fact_type": "event", "content": "李青死亡",
        "entities": ["李青"], "extra": {"chapter_order": 1},
        "evidence": [{"chapter_order": 1, "source_quote": "李青死亡"}],
    }
    revive = {
        "id": 4, "fact_type": "event", "content": "李青复活",
        "entities": ["李青"], "extra": {"chapter_order": 2},
        "evidence": [{"chapter_order": 2, "source_quote": "李青复活"}],
    }
    plot_out = conflicts._detect_plot_logic_conflicts([death, revive])
    assert plot_out and plot_out[0]["title"].startswith("剧情逻辑冲突：")
    assert plot_out[0]["type"] == "plot_logic"


def test_prompt_version_guard():
    """P3: prompt/schema 版本护栏——cache key 内嵌版本号而非 prompt 内容，
    修改 prompt/schema 必须 bump 版本常量，否则新 prompt 会静默命中旧缓存。"""
    from app.cache import APP_VERSION, DEFAULT_PROMPT_VERSION, DEFAULT_SCHEMA_VERSION, cache_key

    assert re.fullmatch(r"novel-ai-system-v\d+", DEFAULT_PROMPT_VERSION), DEFAULT_PROMPT_VERSION
    assert re.fullmatch(r"mvp-json-v\d+", DEFAULT_SCHEMA_VERSION), DEFAULT_SCHEMA_VERSION
    assert APP_VERSION and re.fullmatch(r"mvp-[\d.]+", APP_VERSION)
    key = cache_key(model="m", task_type="t", input_hash_value="h")
    parts = key.split(":")
    assert len(parts) == 6, parts
    assert parts[0] == APP_VERSION
    assert parts[1] == DEFAULT_PROMPT_VERSION
    assert parts[2] == "m"
    assert parts[3] == "t"
    assert parts[4] == DEFAULT_SCHEMA_VERSION
    assert parts[5] == "h"


def test_whole_book_cached_rerun_stats_are_accurate(tmp_path: Path, monkeypatch):
    """P2: 全书章节摘要全缓存重跑统计真实（cache_hits=章数、来源 remote_model）。"""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "wb_stats.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    conn = connect(tmp_path / "wb_stats.sqlite3")
    chapters = [
        ChapterDraft(order=n, title=f"第{n}章", content=f"Hero {n} walked into town.")
        for n in range(1, 9)
    ]
    novel = import_novel(
        conn,
        title="Stats",
        source_filename="stats.txt",
        encoding="utf-8",
        text_hash=sha256_text("stats fixture"),
        chapters=chapters,
    )
    database.set_setting(conn, "api_key", "sk-test")
    calls = {"n": 0}

    def summary_ok(**kwargs):
        calls["n"] += 1
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "short_summary": "Hero walks.",
            "key_events": ["walk"],
            "characters": ["Hero"],
        }

    async def fake_model_call(**kwargs):
        return summary_ok(**kwargs)

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    job1 = create_analysis_job(
        conn, task_type="whole_book_analysis", novel_id=novel["id"], request={"model": "gpt-test"}
    )
    first = asyncio.run(
        runner._run_whole_book_analysis_job(conn, novel["id"], "gpt-test", False, int(job1["id"]))
    )
    assert first["status"] == "ok"
    assert calls["n"] == 8
    job2 = create_analysis_job(
        conn, task_type="whole_book_analysis", novel_id=novel["id"], request={"model": "gpt-test"}
    )
    second = asyncio.run(
        runner._run_whole_book_analysis_job(conn, novel["id"], "gpt-test", False, int(job2["id"]))
    )
    rows = conn.execute("SELECT output_json FROM model_cache WHERE task_type = 'chapter_summary'").fetchall()
    assert len(rows) == 8
    for row in rows:
        output = json.loads(row["output_json"])
        assert "provenance" not in output
        assert "cache_hit" not in output
        assert "job_id" not in output
        assert output["_cache_metadata"]["provider_call_succeeded"] is True
    conn.close()
    assert calls["n"] == 8  # 未新增模型调用
    assert second["cache_hits"] == 8
    assert second["source"] == "remote_model"
    assert second["status"] == "ok"
    assert second["provider_call_attempted"] is False


def test_character_rerun_all_cached_source_is_remote_model(tmp_path: Path, monkeypatch):
    """P2: combined 缓存缺失但批次全缓存时来源为 remote_model 而非 local_fallback。"""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "ch_src.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(main.app)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {
                    "name": "Hero",
                    "aliases": [],
                    "evidence": [{"chapter_title": "Chapter 1", "source_quote": "Hero appeared."}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"第{n}章\nHero {n} walked into town." for n in range(1, 13))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Chars"}, files={"file": ("c.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    novel_id = int(imported["id"])
    first = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"})
    assert first.status_code == 200
    # 模拟 combined 写入前中断: 批次缓存保留, combined 缓存缺失
    with main.db() as conn:
        conn.execute("DELETE FROM model_cache WHERE task_type = 'character_extraction_combined'")
    second = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"})
    data = second.json()
    assert data["cache_hit"] is True
    assert data["source"] == "remote_model"
    assert data["status"] == "ok"


def test_setting_extraction_cached_rerun_source_is_remote_model(tmp_path: Path, monkeypatch):
    """P2: settings 全缓存重跑来源为 remote_model（修复前误标 local_fallback）。"""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "st_src.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    conn = connect(tmp_path / "st_src.sqlite3")
    chapters = [
        ChapterDraft(order=n, title=f"第{n}章", content=f"Hero {n} walked into town.")
        for n in range(1, 7)
    ]
    novel = import_novel(
        conn,
        title="Settings",
        source_filename="st.txt",
        encoding="utf-8",
        text_hash=sha256_text("settings fixture"),
        chapters=chapters,
    )
    database.set_setting(conn, "api_key", "sk-test")

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "settings": [
                {
                    "type": "world_rule",
                    "content": "Rule X",
                    "entities": ["World"],
                    "source_quote": "Quoted.",
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    job1 = create_analysis_job(
        conn, task_type="setting_extraction", novel_id=novel["id"], request={"model": "gpt-test"}
    )
    first = asyncio.run(
        settings_extraction._run_setting_extraction_job(conn, novel["id"], "gpt-test", False, int(job1["id"]))
    )
    assert first["status"] == "ok"
    job2 = create_analysis_job(
        conn, task_type="setting_extraction", novel_id=novel["id"], request={"model": "gpt-test"}
    )
    second = asyncio.run(
        settings_extraction._run_setting_extraction_job(conn, novel["id"], "gpt-test", False, int(job2["id"]))
    )
    rows = conn.execute("SELECT output_json FROM model_cache WHERE task_type = 'setting_extraction'").fetchall()
    assert rows
    for row in rows:
        output = json.loads(row["output_json"])
        assert "provenance" not in output
        assert "cache_hit" not in output
        assert output["_cache_metadata"]["provider_call_succeeded"] is True
    conn.close()
    assert second["source"] == "remote_model"
    assert second["status"] == "ok"
def test_conflict_judgment_payload_is_bounded():
    """P2: 冲突判定请求体有上界——候选数与引文长度都受限。"""
    candidates = []
    for i in range(70):
        candidates.append(
            {
                "type": "world_rule",
                "title": f"Rule {i}",
                "severity": "medium",
                "entities": ["World"],
                "earlier_evidence": [{"chapter_title": "第1章", "source_quote": "长" * 500}],
                "later_evidence": [{"chapter_title": "第2章", "source_quote": "短"}],
                "possible_explanation": "",
                "explanation_evidence": [],
                "model_judgment": "",
                "confidence": "low",
            }
        )
    payload = conflicts._conflict_judgment_payload(candidates)
    import json as _json

    body = _json.loads(payload.rsplit("\n\n", 1)[1])
    assert len(body["candidates"]) == conflicts._MAX_CONFLICT_CANDIDATES == 60
    assert body["candidate_count"] == 70
    assert body["truncated"] is True
    quote = body["candidates"][0]["earlier_evidence"][0]["source_quote"]
    assert len(quote) == conflicts._MAX_CONFLICT_QUOTE_CHARS == 200
    assert body["candidates"][0]["later_evidence"][0]["source_quote"] == "短"
    # 上界自检: 请求体（含固定 schema 前缀）远低于数十万字级
    assert len(payload) < 60000

