"""Whole-book analysis F4-style concurrency tests (2026-08-03, codex/major-fixes).

The one-click whole-book analysis now runs chapter summaries in three phases
per wave: serial cache probes -> concurrent model calls only -> serial
validation/cache writes/progress. These tests verify real overlap, that failed
chapters are never cached, and that cancellation is observed between waves.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import database
from app import main
from app import model_client
from app.database import connect, create_analysis_job, get_analysis_job, import_novel, update_analysis_job
from app.jobs import runner
from app.text_processing import ChapterDraft, sha256_text


def _client_with_temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "wb.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return TestClient(main.app)


def _summary_ok(**kwargs):
    return {
        "status": "ok",
        "task_type": kwargs["task_type"],
        "short_summary": "Hero walks.",
        "key_events": ["walk"],
        "characters": ["Hero"],
    }


def _import_novel(client, title: str, chapters: int) -> int:
    text = "\n\n".join(f"第{n}章\nHero {n} walked into town." for n in range(1, chapters + 1))
    imported = client.post(
        "/novels/import-txt",
        data={"title": title},
        files={"file": ("wb.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    return int(imported["id"])


def test_whole_book_chapter_summaries_run_concurrently(tmp_path: Path, monkeypatch):
    """25 章 x 0.2s 慢调用：并发（默认 4 路）下明显快于串行，且模型调用真实重叠。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    state = {"active": 0, "max_active": 0}

    async def fake_model_call(**kwargs):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.2)
        state["active"] -= 1
        return _summary_ok(**kwargs)

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    novel_id = _import_novel(client, "并发全书", 25)

    started = time.monotonic()
    response = client.post(f"/novels/{novel_id}/analyze-all/start", json={"model": "gpt-test"})
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    job = client.get(f"/analysis-jobs/{response.json()['job_id']}").json()
    assert job["status"] == "completed"
    assert state["max_active"] >= 2, "chapter summary model calls did not overlap"
    # 串行需要 25 x 0.2s = 5s；并发 4 路理论约 1.4s。
    assert elapsed < 2.8, f"concurrent run too slow: {elapsed:.2f}s"


def test_whole_book_failed_chapter_is_not_cached(tmp_path: Path, monkeypatch):
    """模型调用失败的章节绝不写入 model_cache；其余章节正常缓存。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    fail_next = {"order": 2}

    async def fake_model_call(**kwargs):
        if f"第{fail_next['order']}章" in kwargs["user_payload"]:
            fail_next["order"] = -1
            raise RuntimeError("boom")
        return _summary_ok(**kwargs)

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    novel_id = _import_novel(client, "失败不缓存", 4)

    response = client.post(f"/novels/{novel_id}/analyze-all/start", json={"model": "gpt-test"})
    job = client.get(f"/analysis-jobs/{response.json()['job_id']}").json()
    assert job["status"] == "completed"  # partial: 3 ok + 1 fallback

    with main.db() as conn:
        rows = conn.execute(
            "SELECT cache_key FROM model_cache WHERE task_type = 'chapter_summary'"
        ).fetchall()
    assert len(rows) == 3, f"failed chapter must not be cached, got {len(rows)} rows"


def test_whole_book_cancel_between_waves_stops_early(tmp_path: Path, monkeypatch):
    """65 章（> 每波 50）：取消发生在第一波运行中，第二波开始前停止。"""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "wb_cancel.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    TestClient(main.app)  # 确保应用初始化（路由/DB 准备）

    async def fake_model_call(**kwargs):
        await asyncio.sleep(0.05)
        return _summary_ok(**kwargs)

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)

    conn = connect(tmp_path / "wb_cancel.sqlite3")
    chapters = [
        ChapterDraft(order=n, title=f"第{n}章", content=f"Hero {n} walked into town.")
        for n in range(1, 66)
    ]
    novel = import_novel(
        conn,
        title="取消全书",
        source_filename="wbc.txt",
        encoding="utf-8",
        text_hash=sha256_text("取消全书 fixture"),
        chapters=chapters,
    )
    database.set_setting(conn, "api_key", "sk-test")
    job = create_analysis_job(
        conn, task_type="whole_book_analysis", novel_id=novel["id"], request={"model": "gpt-test"}
    )
    job_id = int(job["id"])

    async def scenario():
        task = asyncio.create_task(
            runner._run_whole_book_analysis_job(conn, novel["id"], "gpt-test", False, job_id)
        )
        await asyncio.sleep(0.35)  # 第一波（50 章，0.05s/章，4 路并发）仍在运行
        update_analysis_job(conn, job_id, status="cancelled")
        return await task

    result = asyncio.run(scenario())
    conn.close()

    assert result["status"] == "cancelled"
    assert result["chapters_processed"] < 65, (
        f"cancelled run must not process all chapters: {result['chapters_processed']}"
    )
    assert result["chapters_processed"] >= 40, "first wave should have made progress"
    with main.db() as conn:
        assert get_analysis_job(conn, job_id)["status"] == "cancelled"
