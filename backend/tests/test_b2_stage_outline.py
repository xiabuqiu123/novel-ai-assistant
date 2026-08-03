"""B2: whole-book stage outline (book_stage_outline).

A new independent task_type that groups the book into coarse plot stages, each
answering: chapter range / location / characters / event / resolution / outcome.
Independent cache key (does not touch book_outline). Failed/invalid results are
never cached; an invalid model output is retried once.
"""
import asyncio
from pathlib import Path

from app.cache import cache_key, input_hash
from app.database import (
    connect,
    get_cache,
    get_chapter,
    get_chunks_for_chapter,
    import_novel,
    list_chapters,
    put_cache,
    create_analysis_job,
    get_analysis_job,
)
from app.text_processing import sha256_text, split_chapters

from app import main
from app import secrets
from app import model_client
from app import database
from app.jobs import outlines


MODEL = "gpt-test"


def _import_two_chapter_novel(tmp_path: Path):
    conn = connect(tmp_path / "b2.sqlite3")
    text = "第一章 初入江湖\n少年醒来，掌柜提醒他不要靠近北山。\n第二章 风波起\n少年带着玉牌离开小镇，遇到山匪。"
    chapters = split_chapters(text)
    novel = import_novel(
        conn,
        title="B2粗纲测试",
        source_filename="b2.txt",
        encoding="utf-8",
        text_hash=sha256_text(text),
        chapters=chapters,
        chunk_size=20,
    )
    return conn, novel


def _client_with_temp_db(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "b2_api.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return TestClient(main.app)


# ---- payload ----

def test_stage_outline_payload_includes_stage_instruction_and_summary(tmp_path: Path):
    conn, novel = _import_two_chapter_novel(tmp_path)
    rows = list_chapters(conn, novel["id"])
    payload = main._book_stage_outline_flat_payload(conn, rows, MODEL)

    assert "book_stage_outline" in payload
    # coarse stage instruction (not a single synopsis, not per-chapter)
    assert "剧情阶段分块" in payload
    # summary-driven input (B1 style), not title+chars only
    assert "summary:" in payload
    assert "source:local_snippet_fallback" in payload
    assert "掌柜" in payload
    conn.close()


def test_stage_outline_payload_prefers_cached_chapter_summary(tmp_path: Path):
    conn, novel = _import_two_chapter_novel(tmp_path)
    rows = list_chapters(conn, novel["id"])

    ch1 = get_chapter(conn, int(rows[0]["id"]))
    chunks = get_chunks_for_chapter(conn, int(rows[0]["id"]))
    payload_cs = main._chapter_summary_payload(ch1, chunks)
    h = input_hash("chapter_summary", payload_cs)
    key = cache_key(model=MODEL, task_type="chapter_summary", input_hash_value=h)
    put_cache(
        conn,
        key=key,
        model=MODEL,
        task_type="chapter_summary",
        input_hash_value=h,
        output={
            "status": "ok",
            "task_type": "chapter_summary",
            "parsed_json": {
                "short_summary": "缓存章节摘要：少年在客栈醒来",
                "key_events": ["醒来", "掌柜提醒"],
            },
            "short_summary": "缓存章节摘要：少年在客栈醒来",
            "key_events": ["醒来", "掌柜提醒"],
        },
    )

    payload = main._book_stage_outline_flat_payload(conn, rows, MODEL)
    assert "缓存章节摘要" in payload
    assert "source:chapter_summary_cache" in payload
    assert "source:local_snippet_fallback" in payload  # chapter 2 still local
    conn.close()


# ---- validation ----

def test_invalid_output_reason_rejects_empty_stages():
    assert main._invalid_output_reason({"status": "ok", "stages": []}, "book_stage_outline") is not None
    assert main._invalid_output_reason({"status": "ok"}, "book_stage_outline") is not None


def test_invalid_output_reason_rejects_stage_missing_two_of_three():
    out = {
        "status": "ok",
        "stages": [
            {"stage_index": 1, "title": "A", "chapter_start": 1, "chapter_end": 1,
             "event": "事件", "resolution": "", "outcome": ""}
        ],
    }
    reason = main._invalid_output_reason(out, "book_stage_outline")
    assert reason is not None
    assert "two of event/resolution/outcome" in reason


def test_invalid_output_reason_rejects_bad_chapter_range():
    out = {
        "status": "ok",
        "stages": [
            {"stage_index": 1, "title": "A", "chapter_start": 2, "chapter_end": 1,
             "event": "e", "resolution": "r", "outcome": ""}
        ],
    }
    reason = main._invalid_output_reason(out, "book_stage_outline")
    assert reason is not None
    assert "invalid chapter range" in reason


def test_invalid_output_reason_accepts_valid_stage_outline():
    out = {
        "status": "ok",
        "stages": [
            {"stage_index": 1, "title": "A", "chapter_start": 1, "chapter_end": 2,
             "event": "e", "resolution": "r", "outcome": ""}
        ],
    }
    assert main._invalid_output_reason(out, "book_stage_outline") is None


def test_stage_outline_bounds_ok_respects_max_order():
    out = {"stages": [{"chapter_start": 1, "chapter_end": 2}]}
    assert main._stage_outline_bounds_ok(out, 2) is True
    assert main._stage_outline_bounds_ok(out, 1) is False  # end beyond max_order
    assert main._stage_outline_bounds_ok({"stages": [{"chapter_start": 1, "chapter_end": 0}]}, 2) is False


# ---- normalize ----

def test_normalize_model_output_promotes_stages():
    out = {
        "status": "ok",
        "task_type": "book_stage_outline",
        "parsed_json": {
            "stages": [
                {"stage_index": 1, "title": " A ", "chapter_start": "1", "chapter_end": 2,
                 "location": "x", "characters": [" b ", "c"], "event": "e", "resolution": "r", "outcome": "o"}
            ],
            "evidence": [{"chapter_order": 1, "source_quote": "q"}],
        },
    }
    result = main._normalize_model_output(out, "book_stage_outline")
    assert result["stages"][0]["title"] == "A"
    assert result["stages"][0]["characters"] == ["b", "c"]
    assert result["stages"][0]["chapter_start"] == 1
    assert result["evidence"] == [{"chapter_order": 1, "source_quote": "q"}]


def test_normalize_model_output_parse_error_without_stages():
    out = {"status": "ok", "task_type": "book_stage_outline", "parsed_json": {"summary": "no stages"}}
    result = main._normalize_model_output(out, "book_stage_outline")
    assert result["status"] == "parse_error"
    assert "stages" not in result["model_error"].lower() or "stages" in result["model_error"]


# ---- cache key independence ----

def test_stage_outline_cache_key_independent_of_book_outline():
    payload = "shared-input"
    k_stage = cache_key(model=MODEL, task_type="book_stage_outline", input_hash_value=input_hash("book_stage_outline", payload))
    k_outline = cache_key(model=MODEL, task_type="book_outline", input_hash_value=input_hash("book_outline", payload))
    assert k_stage != k_outline
    assert "book_stage_outline" in k_stage
    assert "book_outline" in k_outline


# ---- endpoint (no api key -> local fallback + cache) ----

def test_stage_outline_endpoint_local_fallback_then_cache_hit(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Stage Outline"},
        files={"file": ("stage.txt", b"Li Qing entered town. Wang warned him.", "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/stage-outline", json={"model": "gpt-test"}).json()
    assert first["cache_key"]
    assert first["provenance"]["task_type"] == "book_stage_outline"
    assert isinstance(first["stages"], list) and first["stages"]
    assert first["cache_hit"] is False

    second = client.post(f"/novels/{imported['id']}/stage-outline", json={"model": "gpt-test"}).json()
    assert second["cache_hit"] is True
    assert second["cache_key"] == first["cache_key"]
    assert second["stages"] == first["stages"]

    cleared = client.delete(f"/novels/{imported['id']}/cache?task_type=book_stage_outline")
    assert cleared.status_code == 200
    assert cleared.json()["task_type"] == "book_stage_outline"
    assert cleared.json()["cleared"] is True

    refreshed = client.post(f"/novels/{imported['id']}/stage-outline", json={"model": "gpt-test"}).json()
    assert refreshed["cache_hit"] is False
    assert refreshed["cache_key"] == first["cache_key"]


def test_start_stage_outline_returns_job_and_result(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "第一章 初入江湖\n少年醒来，掌柜提醒他。"
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Async Stage Outline"},
        files={"file": ("async_stage.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    start = client.post(f"/novels/{imported['id']}/stage-outline/start", json={"model": "gpt-test"})
    assert start.status_code == 200
    data = start.json()
    assert data["job_id"] > 0
    assert data["status"] in {"queued", "running"}
    assert data["duplicated"] is False
    assert data["effective_model"] == "gpt-test"

    result = client.get(f"/analysis-jobs/{data['job_id']}/result")
    assert result.status_code == 200
    payload = result.json()
    assert payload["status"] == "completed"
    assert payload["result"]["task_type"] == "book_stage_outline"
    assert payload["result"]["stages"]
    assert payload["provenance"]["task_type"] == "book_stage_outline"


def test_start_stage_outline_deduplicates_active_job(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Dedup Stage"},
        files={"file": ("dedup_stage.txt", b"Li Qing entered town.", "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/stage-outline/start", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported['id']}/stage-outline/start", json={"model": "gpt-test"})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["job_id"] > 0 and second.json()["job_id"] > 0


# ---- retry once ----

def test_stage_outline_retries_once_then_succeeds(tmp_path: Path, monkeypatch):
    conn, novel = _import_two_chapter_novel(tmp_path)
    job = create_analysis_job(
        conn,
        task_type="book_stage_outline",
        novel_id=novel["id"],
        request={"effective_model": MODEL},
    )

    calls = {"n": 0}

    async def fake_call(*, task_type, user_payload, model, api_key, base_url):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "status": "ok",
                "task_type": task_type,
                "parsed_json": {
                    "stages": [
                        {"stage_index": 1, "title": "A", "chapter_start": 1, "chapter_end": 1,
                         "event": "事件", "resolution": "", "outcome": ""}
                    ]
                },
            }
        return {
            "status": "ok",
            "task_type": task_type,
            "parsed_json": {
                "stages": [
                    {"stage_index": 1, "title": "A", "chapter_start": 1, "chapter_end": 1,
                     "event": "事件", "resolution": "解决", "outcome": "结果"}
                ]
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_call)
    monkeypatch.setattr(
        database, "get_setting",
        lambda c, key, default="": {"api_key": "k", "base_url": "u", "model": MODEL}.get(key, default),
    )
    monkeypatch.setattr(secrets, "decrypt_secret", lambda value: value)

    result = asyncio.run(main._run_book_stage_outline_job(conn, novel["id"], MODEL, True, int(job["id"])))

    assert calls["n"] == 2  # invalid once, then retried once and accepted
    assert result["stages"]
    assert result["stages"][0]["resolution"] == "解决"
    updated = get_analysis_job(conn, int(job["id"]))
    assert str(updated["status"]) == "completed"
    # valid output is cached
    cached = get_cache(conn, str(result["cache_key"]))
    assert cached is not None and cached["stages"][0]["resolution"] == "解决"
    conn.close()


def test_stage_outline_gives_up_after_retry_still_invalid(tmp_path: Path, monkeypatch):
    conn, novel = _import_two_chapter_novel(tmp_path)
    job = create_analysis_job(
        conn,
        task_type="book_stage_outline",
        novel_id=novel["id"],
        request={"effective_model": MODEL},
    )

    calls = {"n": 0}

    async def fake_call(*, task_type, user_payload, model, api_key, base_url):
        calls["n"] += 1
        return {
            "status": "ok",
            "task_type": task_type,
            "parsed_json": {
                "stages": [
                    {"stage_index": 1, "title": "A", "chapter_start": 1, "chapter_end": 1,
                     "event": "事件", "resolution": "", "outcome": ""}
                ]
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_call)
    monkeypatch.setattr(
        database, "get_setting",
        lambda c, key, default="": {"api_key": "k", "base_url": "u", "model": MODEL}.get(key, default),
    )
    monkeypatch.setattr(secrets, "decrypt_secret", lambda value: value)

    result = asyncio.run(main._run_book_stage_outline_job(conn, novel["id"], MODEL, True, int(job["id"])))

    assert calls["n"] == 2  # initial + one retry, then give up
    assert result["provenance"]["model_error"]  # surfaced as failure
    assert str(get_analysis_job(conn, int(job["id"]))["status"]) == "failed"
    # invalid outputs were never cached
    assert get_cache(conn, str(result["cache_key"])) is None
    conn.close()


# ---- >200-chapter layered path ----

def _import_three_chapter_novel(tmp_path: Path):
    conn = connect(tmp_path / "b2_layered.sqlite3")
    text = (
        "第一章 初入江湖\n少年醒来，掌柜提醒他。\n"
        "第二章 风波起\n少年带着玉牌离开小镇。\n"
        "第三章 遇敌\n少年遇到山匪，出手相助。"
    )
    chapters = split_chapters(text)
    novel = import_novel(
        conn,
        title="B2分层测试",
        source_filename="b2_layered.txt",
        encoding="utf-8",
        text_hash=sha256_text(text),
        chapters=chapters,
        chunk_size=20,
    )
    return conn, novel


def test_stage_outline_layered_path_uses_arc_summaries(tmp_path: Path, monkeypatch):
    """>BOOK_OUTLINE_ARC_SIZE chapters go through the arc_summary pipeline."""
    monkeypatch.setattr(outlines, "BOOK_OUTLINE_ARC_SIZE", 2)
    conn, novel = _import_three_chapter_novel(tmp_path)
    job = create_analysis_job(
        conn,
        task_type="book_stage_outline",
        novel_id=novel["id"],
        request={"effective_model": MODEL},
    )

    calls: list[dict] = []

    async def fake_call(*, task_type, user_payload, model, api_key, base_url):
        calls.append({"task_type": task_type, "payload": user_payload})
        if task_type == "arc_summary":
            return {
                "status": "ok",
                "task_type": task_type,
                "parsed_json": {
                    "arc": {
                        "title": "弧摘要",
                        "summary": "这是一段弧的摘要内容",
                        "key_events": [],
                        "characters": [],
                    }
                },
            }
        # book_stage_outline
        return {
            "status": "ok",
            "task_type": task_type,
            "parsed_json": {
                "stages": [
                    {
                        "stage_index": 1, "title": "全书阶段",
                        "chapter_start": 1, "chapter_end": 3,
                        "location": "花果山", "characters": ["孙悟空"],
                        "event": "大闹天宫", "resolution": "与天庭交战", "outcome": "被压五行山",
                    }
                ],
                "evidence": [{"chapter_order": 1, "source_quote": "原文证据"}],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_call)
    monkeypatch.setattr(
        database, "get_setting",
        lambda c, key, default="": {"api_key": "k", "base_url": "u", "model": MODEL}.get(key, default),
    )
    monkeypatch.setattr(secrets, "decrypt_secret", lambda value: value)

    result = asyncio.run(main._run_book_stage_outline_job(conn, novel["id"], MODEL, True, int(job["id"])))

    # 2 arc_summary calls (arc0=ch1-2, arc1=ch3) + 1 stage_outline call
    task_calls = [c["task_type"] for c in calls]
    assert task_calls.count("arc_summary") == 2
    assert task_calls.count("book_stage_outline") == 1

    # The stage_outline payload was built from arc summaries, not flat chapter summaries
    stage_payload = calls[-1]["payload"]
    assert "arc_summaries:" in stage_payload
    assert "这是一段弧的摘要内容" in stage_payload

    # Result carries stages, evidence, and the gathered arcs
    assert result["stages"]
    assert result["stages"][0]["outcome"] == "被压五行山"
    assert result["evidence"] == [{"chapter_order": 1, "source_quote": "原文证据"}]
    assert isinstance(result.get("arcs"), list) and len(result["arcs"]) == 2
    assert result["arcs"][0]["chapter_start"] == 1 and result["arcs"][0]["chapter_end"] == 2
    assert result["arcs"][1]["chapter_start"] == 3 and result["arcs"][1]["chapter_end"] == 3

    # Job completed and output cached
    assert str(get_analysis_job(conn, int(job["id"]))["status"]) == "completed"
    assert get_cache(conn, str(result["cache_key"])) is not None
    conn.close()


def test_stage_outline_layered_path_arc_failure_fails_job(tmp_path: Path, monkeypatch):
    """If an arc summary cannot be extracted, the stage-outline job fails."""
    monkeypatch.setattr(outlines, "BOOK_OUTLINE_ARC_SIZE", 2)
    conn, novel = _import_three_chapter_novel(tmp_path)
    job = create_analysis_job(
        conn,
        task_type="book_stage_outline",
        novel_id=novel["id"],
        request={"effective_model": MODEL},
    )

    calls: list[dict] = []

    async def fake_call(*, task_type, user_payload, model, api_key, base_url):
        calls.append({"task_type": task_type})
        if task_type == "arc_summary":
            return {"status": "ok", "task_type": task_type, "parsed_json": {"summary": ""}}
        return {"status": "ok", "task_type": task_type, "parsed_json": {"stages": []}}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_call)
    monkeypatch.setattr(
        database, "get_setting",
        lambda c, key, default="": {"api_key": "k", "base_url": "u", "model": MODEL}.get(key, default),
    )
    monkeypatch.setattr(secrets, "decrypt_secret", lambda value: value)

    result = asyncio.run(main._run_book_stage_outline_job(conn, novel["id"], MODEL, True, int(job["id"])))

    assert result["provenance"]["model_error"]
    assert str(get_analysis_job(conn, int(job["id"]))["status"]) == "failed"
    # arc_summary was attempted, but book_stage_outline was never reached because
    # arc extraction failed first.
    assert any(c["task_type"] == "arc_summary" for c in calls)
    assert not any(c["task_type"] == "book_stage_outline" for c in calls)
    conn.close()
