import re
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import main
from app import model_client
from app.model_client import ModelHTTPError


def _client_with_temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "api.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return TestClient(main.app)


def _import_novel(client, title: str = "Stage3", chapters: int = 6):
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nHero {n} walked into town." for n in range(1, chapters + 1))
    return client.post(
        "/novels/import-txt",
        data={"title": title},
        files={"file": (f"{title}.txt", text.encode("utf-8"), "text/plain")},
    ).json()


def _chapter_summary_ok(**kwargs):
    return {
        "status": "ok",
        "task_type": kwargs["task_type"],
        "short_summary": "Hero walks.",
        "key_events": ["walk"],
        "characters": ["Hero"],
    }


def _job_for(client, novel_id: int, task_type: str) -> dict:
    """Job-page provenance lives on the list endpoint (_job_from_row)."""
    jobs = client.get("/analysis-jobs", params={"novel_id": novel_id}).json()
    return [job for job in jobs if job["task_type"] == task_type][0]


def _batch_range(kwargs: dict) -> int:
    match = re.search(r"batch_chapter_range:(\d+)-(\d+)", kwargs["user_payload"])
    return int(match.group(1)) if match else 0


# ---- E3: _model_error_text / _cached_source 单元行为 ----

def test_model_error_text_falls_back_to_exception_class_name():
    assert main._model_error_text(httpx.ReadTimeout("")) == "ReadTimeout"
    assert main._model_error_text(RuntimeError("")) == "RuntimeError"
    assert main._model_error_text(ModelHTTPError(429, "rate limited")) == "Model API HTTP 429: rate limited"
    assert "boom" in main._model_error_text(RuntimeError("boom"))


def test_cached_source_maps_mixed_to_cached_partial():
    assert main._cached_source({"source": "mixed"}, {}) == "cached_partial"
    assert main._cached_source({"source": "remote_model"}, {}) == "cached_remote_model"
    assert main._cached_source({"source": "local_fallback"}, {}) == "cached_local_fallback"
    assert main._cached_source({}, {"status": "local_fallback"}) == "cached_local_fallback"


# ---- E2: 三个编排任务写 run_summary 并回填 result_cache_key ----

def test_whole_book_analysis_writes_run_summary_provenance(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return _chapter_summary_ok(**kwargs)

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = _import_novel(client, chapters=4)

    started = client.post(f"/novels/{imported['id']}/analyze-all/start", json={"model": "gpt-test"}).json()
    job = _job_for(client, imported["id"], "whole_book_analysis")

    assert job["status"] == "completed"
    assert job["result_cache_key"] != ""
    assert job["cache_source"] == "cached_remote_model"
    assert job["provider_call_attempted"] is True
    assert job["provider_call_succeeded"] is True
    with main.db() as conn:
        cached = main.get_cache(conn, job["result_cache_key"])
        row = conn.execute(
            "SELECT task_type FROM model_cache WHERE cache_key = ?", (job["result_cache_key"],)
        ).fetchone()
    assert cached is not None
    assert cached["task_type"] == "whole_book_analysis"
    assert cached["_cache_metadata"]["source"] == "remote_model"
    assert cached["_cache_metadata"]["model_error"] is None
    assert cached["failed_batches"] == []
    assert row["task_type"] == "whole_book_analysis_run_summary"


def test_whole_book_analysis_partial_mixed_surfaces_error(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        if "Hero 2 walked" in kwargs["user_payload"]:
            raise httpx.ReadTimeout("")
        return _chapter_summary_ok(**kwargs)

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = _import_novel(client, chapters=4)

    client.post(f"/novels/{imported['id']}/analyze-all/start", json={"model": "gpt-test"}).json()
    job = _job_for(client, imported["id"], "whole_book_analysis")

    assert job["status"] == "completed"
    assert job["cache_source"] == "cached_partial"
    assert job["model_error"] == "ReadTimeout"
    assert "ReadTimeout" in job["error"]
    with main.db() as conn:
        cached = main.get_cache(conn, job["result_cache_key"])
    assert cached is not None
    assert cached["_cache_metadata"]["source"] == "mixed"
    assert cached["_cache_metadata"]["model_error"] == "ReadTimeout"
    assert any(item["chapter_order"] == 2 and item["error"] == "ReadTimeout" for item in cached["failed_batches"])


def test_setting_extraction_partial_writes_run_summary_and_cached_partial(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        if _batch_range(kwargs) == 1:
            raise httpx.ReadTimeout("")
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
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = _import_novel(client, chapters=4)

    client.post(f"/novels/{imported['id']}/settings/start", json={"model": "gpt-test"}).json()
    job = _job_for(client, imported["id"], "setting_extraction")

    assert job["status"] == "completed"
    assert job["cache_source"] == "cached_partial"
    assert job["model_error"] == "ReadTimeout"
    assert "ReadTimeout" in job["error"]
    with main.db() as conn:
        cached = main.get_cache(conn, job["result_cache_key"])
    assert cached is not None
    assert cached["task_type"] == "setting_extraction"
    assert cached["_cache_metadata"]["source"] == "mixed"
    assert cached["_cache_metadata"]["model_error"] == "ReadTimeout"
    assert cached["failed_batches"] == [{"batch_index": 1, "chapter_range": "1-4", "error": "ReadTimeout"}]


def test_event_extraction_run_summary_key_cleared_by_cache_clear(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "events": [
                {
                    "title": f"Event {_batch_range(kwargs)}",
                    "content": "Something happened.",
                    "entities": ["Hero"],
                    "source_quote": "Quoted.",
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = _import_novel(client, chapters=3)

    started = client.post(f"/novels/{imported['id']}/events/start", json={"model": "gpt-test"}).json()
    job = _job_for(client, imported["id"], "event_extraction")
    assert job["status"] == "completed"
    assert job["cache_source"] == "cached_remote_model"
    key = job["result_cache_key"]
    assert key != ""
    with main.db() as conn:
        assert main.get_cache(conn, key) is not None

    cleared = client.delete(f"/novels/{imported['id']}/cache?task_type=event_extraction")
    assert cleared.status_code == 200
    with main.db() as conn:
        assert main.get_cache(conn, key) is None
    refreshed = _job_for(client, imported["id"], "event_extraction")
    assert refreshed["result_cache_key"] == ""


def test_conflict_detection_writes_run_summary(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {"status": "ok", "task_type": kwargs["task_type"], "conflicts": []}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = _import_novel(client, chapters=2)

    response = client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"})
    assert response.status_code == 200
    job = _job_for(client, imported["id"], "conflict_detection")

    assert job["status"] == "completed"
    assert job["result_cache_key"] != ""
    assert job["cache_source"] == "cached_remote_model"
    assert job["provider_call_attempted"] is True
    assert job["provider_call_succeeded"] is True
    with main.db() as conn:
        cached = main.get_cache(conn, job["result_cache_key"])
    assert cached is not None
    assert cached["task_type"] == "conflict_detection"
    assert cached["_cache_metadata"]["source"] == "remote_model"
    assert cached["failed_batches"] == []


def test_character_extraction_partial_surfaces_batch_error_in_combined(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        if _batch_range(kwargs) == 3:
            raise RuntimeError("boom")
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {
                    "name": f"Hero {_batch_range(kwargs)}",
                    "aliases": [],
                    "evidence": [{"chapter_title": "Chapter", "source_quote": "appeared."}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    with main.db() as conn:
        main.set_setting(conn, "character_extraction_batch_size", "1")
    imported = _import_novel(client, chapters=4)

    client.post(f"/novels/{imported['id']}/characters/start", json={"model": "gpt-test"}).json()
    job = _job_for(client, imported["id"], "character_extraction")

    assert job["status"] == "completed"
    assert job["cache_source"] == "cached_partial"
    assert job["model_error"] == "boom"
    assert "boom" in job["error"]
    with main.db() as conn:
        cached = main.get_cache(conn, job["result_cache_key"])
    assert cached is not None
    assert cached["_cache_metadata"]["source"] == "mixed"
    assert cached["_cache_metadata"]["model_error"] == "boom"
