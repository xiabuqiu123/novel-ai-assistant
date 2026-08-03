"""F4: extraction speed - batch size defaults 5->10 and concurrent model calls.

Concurrency only covers model calls: cache probes, validation, cache writes,
merging and progress stay serial on one sqlite connection. Tests use slow
mocked model calls to observe real overlap.
"""
import asyncio
import json
import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app import model_client


def _client_with_temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "f4.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return TestClient(main.app)


def _import_novel(client, title: str, chapters: int) -> int:
    text = "\n\n".join(f"第{n}章\nHero {n} walked into town." for n in range(1, chapters + 1))
    imported = client.post(
        "/novels/import-txt",
        data={"title": title},
        files={"file": ("f4.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    return int(imported["id"])


def test_batch_size_defaults_are_ten():
    assert main.CHARACTER_EXTRACTION_BATCH_SIZE == 10
    assert main.RELATIONSHIP_EXTRACTION_BATCH_SIZE == 10
    assert main.SETTING_EXTRACTION_BATCH_SIZE == 10
    assert main.EVENT_EXTRACTION_BATCH_SIZE == 10


def test_cacheable_output_strips_provenance_keys():
    """P3-4: 批次结果写缓存前归一化为 _cached_model_task 同构行。"""
    from app.jobs.cache import _cacheable_output
    from app.provenance import with_model_provenance

    wrapped = with_model_provenance(
        {"status": "ok", "task_type": "character_extraction", "characters": []},
        task_type="character_extraction",
        model_used="gpt-test",
        cache_hit=False,
        input_hash_value="h",
        cache_key_value="k",
        job_id=None,
        source="remote_model",
        provider_call_attempted=True,
        provider_call_succeeded=True,
    )
    row = _cacheable_output(wrapped)
    for key in ("provenance", "cache_hit", "cache_key", "job_id", "source"):
        assert key not in row, key
    assert row["_cache_metadata"] == {
        "source": "remote_model",
        "provider_call_attempted": True,
        "provider_call_succeeded": True,
        "model_error": None,
    }
    assert row["status"] == "ok"


def test_batch_cache_rows_use_canonical_shape(tmp_path: Path, monkeypatch):
    """P3-4: 批次抽取落库的缓存行无顶层 provenance 键，元数据只在 _cache_metadata。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {
                    "name": "Hero",
                    "aliases": [],
                    "evidence": [{"chapter_title": "c", "source_quote": "appeared."}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    novel_id = _import_novel(client, "缓存形态", 5)
    response = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert response["status"] == "ok"

    from app.database import connect

    conn = connect(tmp_path / "f4.sqlite3")
    rows = conn.execute(
        "SELECT output_json FROM model_cache WHERE task_type = 'character_extraction'"
    ).fetchall()
    conn.close()
    assert rows, "no batch cache rows"
    for row in rows:
        output = json.loads(row["output_json"])
        for key in ("provenance", "cache_hit", "cache_key", "job_id", "source"):
            assert key not in output, key
        metadata = output["_cache_metadata"]
        assert metadata["source"] == "remote_model"
        assert metadata["provider_call_attempted"] is True
        assert metadata["provider_call_succeeded"] is True


def test_slow_batches_run_concurrently_and_finish_faster(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    state = {"active": 0, "max_active": 0}

    async def fake_model_call(**kwargs):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.2)
        state["active"] -= 1
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        label = match.group(1) if match else "0"
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {"name": f"Hero {label}", "aliases": [], "evidence": [{"chapter_title": "c", "source_quote": "appeared."}], "confidence": "medium"}
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    novel_id = _import_novel(client, "F4并发", 25)

    started = time.monotonic()
    response = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    elapsed = time.monotonic() - started

    assert response["status"] == "ok"
    # Default concurrency 2: 3 batches x 0.2s finish in ~0.4s, not ~0.6s.
    assert state["max_active"] >= 2, "model calls did not overlap"
    assert elapsed < 0.55, f"concurrent run too slow: {elapsed:.2f}s"


def test_cached_batches_not_recalled_on_resume(tmp_path: Path, monkeypatch):
    """After a failed batch, a resume only re-calls that batch (cache probe hit
    for the others) even with concurrent calls enabled."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []
    fail_next = {"batch_start": 11}

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        start = int(match.group(1)) if match else 0
        if start == fail_next["batch_start"]:
            fail_next["batch_start"] = -1
            raise RuntimeError("")
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {"name": f"Hero {start}", "aliases": [], "evidence": [{"chapter_title": "c", "source_quote": "appeared."}], "confidence": "medium"}
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    novel_id = _import_novel(client, "F4恢复", 15)

    first = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert first["status"] == "partial"
    assert len(calls) == 2  # batches [1-10] ok, [11-15] raised -> fallback

    calls.clear()
    second = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert second["status"] == "ok"
    assert len(calls) == 1, "cached batches were re-called on resume"


def test_failed_batch_error_survives_concurrency_and_retry(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []
    fail_next = {"batch_start": 11}

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        start = int(match.group(1)) if match else 0
        if start == fail_next["batch_start"]:
            fail_next["batch_start"] = -1
            return {"status": "invalid_model_json", "task_type": kwargs["task_type"], "raw_json": "x", "parse_error": "boom"}
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {"name": f"Hero {start}", "aliases": [], "evidence": [{"chapter_title": "c", "source_quote": "appeared."}], "confidence": "medium"}
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    novel_id = _import_novel(client, "F4失败", 15)

    first = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert first["status"] == "invalid_model_json"
    assert "batch 2/2 failed" in first["provenance"]["model_error"]
    job = client.get(f"/analysis-jobs/{first['job_id']}").json()
    assert job["status"] == "failed"

    # Invalid outputs are never cached; the resume re-calls only the failed batch.
    calls.clear()
    second = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert second["status"] == "ok"
    assert len(calls) == 1


def test_concurrency_setting_overrides_and_invalid_falls_back(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    state = {"active": 0, "max_active": 0}

    async def fake_model_call(**kwargs):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.15)
        state["active"] -= 1
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        label = match.group(1) if match else "0"
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {"name": f"Hero {label}", "aliases": [], "evidence": [{"chapter_title": "c", "source_quote": "appeared."}], "confidence": "medium"}
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    with main.db() as conn:
        main.set_setting(conn, "character_extraction_concurrency", "1")
    novel_id = _import_novel(client, "F4串行", 25)

    response = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert response["status"] == "ok"
    assert state["max_active"] == 1, "concurrency=1 must serialize model calls"

    # Out-of-range values fall back to the default (2).
    with main.db() as conn:
        assert main._extraction_concurrency(conn, "character_extraction_concurrency") == 1
        main.set_setting(conn, "character_extraction_concurrency", "99")
        assert main._extraction_concurrency(conn, "character_extraction_concurrency") == 2
        main.set_setting(conn, "character_extraction_concurrency", "abc")
        assert main._extraction_concurrency(conn, "character_extraction_concurrency") == 2
        assert main._extraction_concurrency(conn, "relationship_extraction_concurrency") == 2

def test_setting_extraction_batches_run_concurrently(tmp_path: Path, monkeypatch):
    """Batch 7: setting_extraction reuses the F4 three-phase pattern - only
    model calls overlap; probes/validation/persist stay serial."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    state = {"active": 0, "max_active": 0}

    async def fake_model_call(**kwargs):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.2)
        state["active"] -= 1
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        start = match.group(1) if match else "0"
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "settings": [
                {"category": "world_rule", "name": f"Rule {start}", "description": "Test rule."}
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    novel_id = _import_novel(client, "F4设定并发", 25)

    started = time.monotonic()
    response = client.post(f"/novels/{novel_id}/settings", json={"model": "gpt-test"}).json()
    elapsed = time.monotonic() - started

    assert response["status"] == "ok"
    # 25 chapters at default batch 10 -> 3 batches x 0.2s overlap to ~0.4s.
    assert state["max_active"] >= 2, "setting batch model calls did not overlap"
    assert elapsed < 0.55, f"concurrent settings run too slow: {elapsed:.2f}s"


def test_event_extraction_cached_batches_not_recalled_on_resume(tmp_path: Path, monkeypatch):
    """Batch 7: event_extraction serial cache probes skip cached batches on a
    resume run, exactly like characters/relationships."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        start = match.group(1) if match else "0"
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "events": [
                {"title": f"Event {start}", "description": "Something happened."}
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    novel_id = _import_novel(client, "F4事件恢复", 25)

    first = client.post(f"/novels/{novel_id}/events", json={"model": "gpt-test"}).json()
    assert first["status"] == "ok"
    assert len(calls) == 3  # 25 chapters at default batch 10 -> 3 batches

    calls.clear()
    second = client.post(f"/novels/{novel_id}/events", json={"model": "gpt-test"}).json()
    assert second["status"] == "ok"
    assert len(calls) == 0, "cached event batches were re-called on rerun"
