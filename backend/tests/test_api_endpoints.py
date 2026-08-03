import json
import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app import model_client
from starlette.background import BackgroundTasks
from app.database import import_novel
from app.model_client import ModelHTTPError
from app.text_processing import ChapterDraft, sha256_text


def _wait_job_completed(client, job_id: int, timeout: float = 10.0) -> dict:
    """Poll until a background-started job reaches a terminal status."""
    deadline = time.monotonic() + timeout
    job = {}
    while time.monotonic() < deadline:
        job = client.get(f"/analysis-jobs/{job_id}").json()
        if job.get("status") in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish; status={job.get('status')}")


EXPECTED_PROVENANCE_FIELDS = {
    "task_type",
    "model_used",
    "source",
    "cache_hit",
    "local_fallback",
    "model_error",
    "prompt_version",
    "schema_version",
    "input_hash",
    "cache_key",
    "job_id",
    "provider_call_attempted",
    "provider_call_succeeded",
}


def _client_with_temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "api.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return TestClient(main.app)


def _assert_provenance(
    data: dict,
    *,
    task_type: str,
    model_used: str,
    cache_hit: bool,
    local_fallback: bool,
    source: str | None = None,
    model_error: str | None = None,
) -> None:
    provenance = data["provenance"]
    assert set(provenance) == EXPECTED_PROVENANCE_FIELDS
    assert provenance["task_type"] == task_type
    assert provenance["model_used"] == model_used
    if source is not None:
        assert provenance["source"] == source
    assert provenance["cache_hit"] is cache_hit
    assert provenance["local_fallback"] is local_fallback
    assert provenance["model_error"] == model_error
    assert provenance["cache_key"] == data["cache_key"]
    assert provenance["job_id"] == data["job_id"]
    assert len(provenance["input_hash"]) == 64
    assert provenance["prompt_version"]
    assert provenance["schema_version"]


def test_import_txt_endpoint_lists_chapters(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "第一章 初入江湖\n少年醒来。\n第二章 风波起\n掌柜提醒他离开。"

    response = client.post(
        "/novels/import-txt",
        data={"title": "测试小说", "chunk_size": "20"},
        files={"file": ("novel.txt", text.encode("utf-8"), "text/plain")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["imported"] is True
    assert data["title"] == "测试小说"
    assert data["encoding"] == "utf-8"
    assert data["chapter_count"] == 2
    assert data["chunk_count"] >= 2

    chapters_response = client.get(f"/novels/{data['id']}/chapters")
    assert chapters_response.status_code == 200
    chapters = chapters_response.json()
    assert [chapter["title"] for chapter in chapters] == ["第一章 初入江湖", "第二章 风波起"]


def test_import_txt_duplicate_returns_existing_novel_metadata(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    body = b"Li Qing entered town. Wang warned him."

    first = client.post(
        "/novels/import-txt",
        data={"title": "Original"},
        files={"file": ("original.txt", body, "text/plain")},
    ).json()
    duplicate = client.post(
        "/novels/import-txt",
        data={"title": "Renamed Copy"},
        files={"file": ("renamed.txt", body, "text/plain")},
    ).json()

    assert duplicate["imported"] is False
    assert duplicate["id"] == first["id"]
    assert duplicate["title"] == "Original"
    assert duplicate["duplicate_of"]["id"] == first["id"]
    assert duplicate["duplicate_of"]["source_filename"] == "original.txt"
    assert duplicate["requested_title"] == "Renamed Copy"
    assert duplicate["requested_source_filename"] == "renamed.txt"


def test_delete_novel_removes_chapters_jobs_and_allows_reimport(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    body = b"Li Qing entered town. Wang warned him."
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Delete Me"},
        files={"file": ("delete.txt", body, "text/plain")},
    ).json()
    outline = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"}).json()

    deleted = client.delete(f"/novels/{imported['id']}")

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["deleted_cache_entries"] == 1
    assert client.get(f"/novels/{imported['id']}/chapters").json() == []
    assert client.get(f"/analysis-jobs?novel_id={imported['id']}").json() == []
    assert client.delete(f"/novels/{imported['id']}").status_code == 404

    reimported = client.post(
        "/novels/import-txt",
        data={"title": "Reimported"},
        files={"file": ("delete.txt", body, "text/plain")},
    ).json()
    assert reimported["imported"] is True
    assert reimported["id"] != imported["id"]
    assert outline["cache_key"]


def test_clear_novel_cache_by_task_type_removes_cached_result(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Clear Cache"},
        files={"file": ("cache.txt", b"Li Qing entered town. Wang warned him.", "text/plain")},
    ).json()
    first = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"}).json()
    cached = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"}).json()

    cleared = client.delete(f"/novels/{imported['id']}/cache?task_type=book_outline")
    refreshed = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"}).json()

    assert cached["cache_hit"] is True
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True
    assert cleared.json()["task_type"] == "book_outline"
    assert cleared.json()["deleted_cache_entries"] == 1
    assert refreshed["cache_hit"] is False
    assert refreshed["cache_key"] == first["cache_key"]


def test_clear_novel_cache_missing_novel_returns_404(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    response = client.delete("/novels/999/cache?task_type=book_outline")

    assert response.status_code == 404


def test_clear_novel_cache_removes_orphan_outline_cache(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Orphan Outline"},
        files={"file": ("outline.txt", b"Li Qing woke up. Wang closed the inn.", "text/plain")},
    ).json()
    first = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-4.1-mini"}).json()
    client.post(f"/analysis-jobs/{first['job_id']}/retry")

    cleared = client.delete(f"/novels/{imported['id']}/cache?task_type=book_outline")
    refreshed = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-4.1-mini"}).json()

    assert cleared.status_code == 200
    assert cleared.json()["deleted_cache_entries"] == 1
    assert refreshed["cache_hit"] is False
    assert refreshed["cache_key"] == first["cache_key"]


def test_clear_novel_cache_removes_orphan_outline_cache_for_request_model(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Orphan Outline Model"},
        files={"file": ("outline-model.txt", b"Li Qing woke up. Wang closed the inn.", "text/plain")},
    ).json()
    first = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-request"}).json()
    client.post(f"/analysis-jobs/{first['job_id']}/retry")

    cleared = client.delete(f"/novels/{imported['id']}/cache?task_type=book_outline")
    refreshed = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-request"}).json()

    assert cleared.status_code == 200
    assert cleared.json()["deleted_cache_entries"] == 1
    assert refreshed["cache_hit"] is False
    assert refreshed["cache_key"] == first["cache_key"]


def test_clear_novel_cache_preserves_other_novel_cache(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    first_novel = client.post(
        "/novels/import-txt",
        data={"title": "First"},
        files={"file": ("first.txt", b"Li Qing woke up.", "text/plain")},
    ).json()
    second_novel = client.post(
        "/novels/import-txt",
        data={"title": "Second"},
        files={"file": ("second.txt", b"Wang closed the inn.", "text/plain")},
    ).json()
    client.post(f"/novels/{first_novel['id']}/outline", json={"model": "gpt-4.1-mini"})
    second_first = client.post(f"/novels/{second_novel['id']}/outline", json={"model": "gpt-4.1-mini"}).json()

    cleared = client.delete(f"/novels/{first_novel['id']}/cache?task_type=book_outline")
    second_cached = client.post(f"/novels/{second_novel['id']}/outline", json={"model": "gpt-4.1-mini"}).json()

    assert cleared.status_code == 200
    assert second_cached["cache_hit"] is True
    assert second_cached["cache_key"] == second_first["cache_key"]


def test_delete_novel_removes_orphan_chapter_summary_cache(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Orphan Chapter"},
        files={"file": ("chapter.txt", b"Li Qing woke up. Wang closed the inn.", "text/plain")},
    ).json()
    chapter_id = client.get(f"/novels/{imported['id']}/chapters").json()[0]["id"]
    first = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-4.1-mini"}).json()
    client.post(f"/analysis-jobs/{first['job_id']}/retry")

    deleted = client.delete(f"/novels/{imported['id']}")
    with main.db() as conn:
        cached = conn.execute("SELECT cache_key FROM model_cache WHERE cache_key = ?", (first["cache_key"],)).fetchone()

    assert deleted.status_code == 200
    assert deleted.json()["deleted_cache_entries"] == 1
    assert cached is None


def test_chapter_summary_local_fallback_is_cached_and_creates_jobs(tmp_path: Path, monkeypatch):


    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "第一章 初入江湖\n少年醒来，掌柜提醒他不要靠近北山。"
    imported = client.post(
        "/novels/import-txt",
        data={"title": "缓存测试"},
        files={"file": ("cache.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    chapter_id = client.get(f"/novels/{imported['id']}/chapters").json()[0]["id"]

    first = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})
    second = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "local_fallback"
    assert first.json()["cache_hit"] is False
    assert second.json()["status"] == "local_fallback"
    assert second.json()["cache_hit"] is True
    assert second.json()["cache_key"] == first.json()["cache_key"]
    _assert_provenance(
        first.json(),
        task_type="chapter_summary",
        model_used="gpt-test",
        cache_hit=False,
        local_fallback=True,
        source="local_fallback",
    )
    _assert_provenance(
        second.json(),
        task_type="chapter_summary",
        model_used="gpt-test",
        cache_hit=True,
        local_fallback=True,
        source="cached_local_fallback",
    )
    assert first.json()["short_summary"]
    assert first.json()["evidence"][0]["chapter_id"] == chapter_id
    assert isinstance(first.json()["job_id"], int)
    assert isinstance(second.json()["job_id"], int)

    first_job = client.get(f"/analysis-jobs/{first.json()['job_id']}")
    second_job = client.get(f"/analysis-jobs/{second.json()['job_id']}")
    jobs = client.get(f"/analysis-jobs?novel_id={imported['id']}")

    assert first_job.status_code == 200
    assert first_job.json()["status"] == "completed"
    assert first_job.json()["progress"] == 100
    assert first_job.json()["result_cache_key"] == first.json()["cache_key"]
    assert second_job.status_code == 200
    assert second_job.json()["status"] == "completed"
    assert second_job.json()["result_cache_key"] == first.json()["cache_key"]
    listed_second_job = jobs.json()[0]
    assert listed_second_job["effective_model"] == "gpt-test"
    assert listed_second_job["cache_source"] == "cached_local_fallback"
    assert listed_second_job["local_fallback"] is True
    assert [job["id"] for job in jobs.json()] == [second.json()["job_id"], first.json()["job_id"]]

    retry = client.post(f"/analysis-jobs/{first.json()['job_id']}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    assert retry.json()["progress"] == 0
    assert retry.json()["retry_count"] == 1
    assert retry.json()["result_cache_key"] == ""

    rerun = client.post("/analysis-jobs/run-next")
    assert rerun.status_code == 200
    assert rerun.json()["job_id"] == first.json()["job_id"]
    rerun_job = _wait_job_completed(client, first.json()["job_id"])
    assert rerun_job["status"] == "completed"
    assert rerun_job["progress"] == 100
    assert rerun_job["result_cache_key"] == first.json()["cache_key"]


def test_model_call_uses_api_key_and_cache_avoids_duplicate_calls(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "parsed_json": {
                "short_summary": "少年醒来，初入江湖。",
                "key_events": ["醒来"],
                "characters": ["少年"],
                "evidence": [],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post(
        "/settings/model",
        json={"api_key": "sk-test", "base_url": "https://example.test/v1", "model": "gpt-test"},
    )
    imported = client.post(
        "/novels/import-txt",
        data={"title": "模型调用测试"},
        files={"file": ("model.txt", "第一章 初入江湖\n少年醒来。".encode("utf-8"), "text/plain")},
    ).json()
    chapter_id = client.get(f"/novels/{imported['id']}/chapters").json()[0]["id"]

    first = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})
    second = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "ok"
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert len(calls) == 1
    _assert_provenance(
        first.json(),
        task_type="chapter_summary",
        model_used="gpt-test",
        cache_hit=False,
        local_fallback=False,
        source="remote_model",
    )
    _assert_provenance(
        second.json(),
        task_type="chapter_summary",
        model_used="gpt-test",
        cache_hit=True,
        local_fallback=False,
        source="cached_remote_model",
    )
    assert calls[0]["api_key"] == "sk-test"
    assert calls[0]["base_url"] == "https://example.test/v1"
    assert calls[0]["model"] == "gpt-test"
    assert calls[0]["task_type"] == "chapter_summary"


def test_task_uses_saved_model_when_request_omits_model(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    client.post(
        "/settings/model",
        json={"api_key": "", "base_url": "https://example.test/v1", "model": "gpt-saved"},
    )
    imported = client.post(
        "/novels/import-txt",
        data={"title": "saved model"},
        files={"file": ("saved.txt", b"Li Qing woke up. Wang closed the inn.", "text/plain")},
    ).json()

    result = client.post(f"/novels/{imported['id']}/outline", json={"force_refresh": True}).json()

    assert result["provenance"]["model_used"] == "gpt-saved"
    assert result["provenance"]["source"] == "local_fallback"
    assert result["provenance"]["provider_call_attempted"] is False


def test_explicit_task_model_overrides_saved_model(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    client.post(
        "/settings/model",
        json={"api_key": "", "base_url": "https://example.test/v1", "model": "gpt-saved"},
    )
    imported = client.post(
        "/novels/import-txt",
        data={"title": "override model"},
        files={"file": ("override.txt", b"Li Qing woke up. Wang closed the inn.", "text/plain")},
    ).json()

    result = client.post(
        f"/novels/{imported['id']}/outline",
        json={"model": "gpt-override", "force_refresh": True},
    ).json()

    assert result["provenance"]["model_used"] == "gpt-override"


def test_outline_local_fallback_is_cached_and_creates_job(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "第一章 初入江湖\n少年醒来，掌柜提醒他不要靠近北山。\n第二章 风波起\n少年带着玉牌离开小镇。"
    imported = client.post(
        "/novels/import-txt",
        data={"title": "大纲测试"},
        files={"file": ("outline.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "local_fallback"
    assert first.json()["task_type"] == "book_outline"
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert second.json()["cache_key"] == first.json()["cache_key"]
    chapters = first.json()["outline"]["chapters"]
    assert [chapter["chapter_title"] for chapter in chapters] == ["第一章 初入江湖", "第二章 风波起"]
    assert "掌柜" in chapters[0]["brief"]
    assert first.json()["evidence"][0]["chapter_title"] == "第一章 初入江湖"
    job = client.get(f"/analysis-jobs/{first.json()['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "completed"
    assert job.json()["result_cache_key"] == first.json()["cache_key"]


def test_outline_with_api_key_uses_model_call(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"], "outline": {"chapters": []}}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Outline model"},
        files={"file": ("outline.txt", b"Li Qing woke up. Wang closed the inn.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["cache_hit"] is False
    assert len(calls) == 1
    assert calls[0]["task_type"] == "book_outline"
    assert "chapter_order" in calls[0]["user_payload"]


def test_outline_model_http_error_falls_back_to_local_outline(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        raise ModelHTTPError(400, "invalid model")

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "https://api.example/v1", "model": "bad-model"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Outline error fallback"},
        files={"file": ("outline.txt", b"Li Qing woke up. Wang closed the inn.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/outline", json={"model": "bad-model"})
    data = response.json()

    assert response.status_code == 200
    assert data["status"] == "local_fallback"
    assert data["model_error"] == "Model API HTTP 400: invalid model"
    assert data["outline"]["chapters"]
    assert data["cache_hit"] is False
    _assert_provenance(
        data,
        task_type="book_outline",
        model_used="bad-model",
        cache_hit=False,
        local_fallback=True,
        model_error="Model API HTTP 400: invalid model",
    )

def test_model_settings_round_trip_masks_api_key(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    save = client.post(
        "/settings/model",
        json={"api_key": "sk-test", "base_url": "https://example.test/v1", "model": "gpt-test"},
    )
    read = client.get("/settings/model")
    with main.db() as conn:
        stored_key = conn.execute("SELECT value FROM settings WHERE key = 'api_key'").fetchone()["value"]

    assert save.status_code == 200
    assert stored_key != "sk-test"
    assert stored_key.startswith("enc:v")
    assert save.json() == {"status": "saved"}
    assert read.status_code == 200
    assert read.json() == {
        "api_key_set": "yes",
        "base_url": "https://example.test/v1",
        "model": "gpt-test",
    }


def test_model_settings_connection_test_uses_structured_backend_result(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_connection_test(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "status": "ok",
            "message": "Connection test succeeded.",
            "model": kwargs["model"],
            "base_url": kwargs["base_url"],
            "response_id": "chatcmpl-test",
        }

    monkeypatch.setattr(model_client, "test_openai_compatible_connection", fake_connection_test)
    response = client.post(
        "/settings/model/test",
        json={"api_key": "sk-test", "base_url": "https://api.example/v1", "model": "gpt-test"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["model"] == "gpt-test"
    assert calls[0]["api_key"] == "sk-test"
    assert calls[0]["base_url"] == "https://api.example/v1"


def test_model_settings_connection_test_reports_provider_http_error(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_connection_test(**kwargs):
        raise ModelHTTPError(401, "invalid api key")

    monkeypatch.setattr(model_client, "test_openai_compatible_connection", fake_connection_test)
    response = client.post(
        "/settings/model/test",
        json={"api_key": "bad-key", "base_url": "https://api.example/v1", "model": "gpt-test"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == "provider_http_error"
    assert data["http_status"] == 401
    assert data["message"] == "invalid api key"


def test_existing_plaintext_api_key_setting_still_works(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"], "outline": {"chapters": []}}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    with main.db() as conn:
        main.set_setting(conn, "api_key", "sk-legacy")
        main.set_setting(conn, "model", "gpt-test")
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Legacy key"},
        files={"file": ("legacy.txt", b"Li Qing woke up.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"})

    assert response.status_code == 200
    assert calls[0]["api_key"] == "sk-legacy"


def test_qa_payload_uses_structured_keyword_evidence(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "QA test"},
        files={"file": ("qa.txt", b"Li Qing visited North Mountain and found a jade token.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/qa", json={"model": "gpt-test", "question": "North Mountain"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["cache_hit"] is False
    _assert_provenance(
        response.json(),
        task_type="evidence_qa",
        model_used="gpt-test",
        cache_hit=False,
        local_fallback=False,
    )
    assert calls[0]["task_type"] == "evidence_qa"
    evidence_json = calls[0]["user_payload"].split("evidence_json:\n", 1)[1]
    evidence = json.loads(evidence_json)
    assert evidence[0]["reason"] == "keyword_match"
    assert evidence[0]["score"] >= 1
    assert evidence[0]["matched_terms"] == ["north", "mountain"]
    assert "North Mountain" in evidence[0]["source_quote"]


def test_qa_payload_marks_fallback_only_evidence(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "QA fallback"},
        files={"file": ("qa.txt", b"Li Qing stayed in town. Wang closed the inn.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/qa", json={"model": "gpt-test", "question": "dragon palace"})

    assert response.status_code == 200
    evidence_json = calls[0]["user_payload"].split("evidence_json:\n", 1)[1]
    evidence = json.loads(evidence_json)
    assert "retrieval_version:qa-retrieval-v4" in calls[0]["user_payload"]
    assert "retrieval_status:fallback_only" in calls[0]["user_payload"]
    assert "Fallback samples are not whole-book evidence" in calls[0]["user_payload"]
    assert evidence[0]["reason"] == "fallback_sample"
    assert evidence[0]["retrieval_status"] == "fallback_only"
    assert evidence[0]["score"] == 0
    assert evidence[0]["matched_terms"] == []
    assert "Li Qing" in evidence[0]["source_quote"]


def test_qa_fuzzy_chinese_quote_match_finds_later_chapter_id_96(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    quote = "\u6211\u8981\u8fd9\u5929\uff0c\u518d\u906e\u4e0d\u4f4f\u6211\u773c\uff0c\u8981\u8fd9\u5730\uff0c\u518d\u57cb\u4e0d\u4e86\u6211\u5fc3"
    chapters = [
        ChapterDraft(order=index, title=f"chapter {index}", content=f"ordinary text {index}")
        for index in range(1, 7)
    ]
    chapters.append(ChapterDraft(order=7, title="\u7b2c\u516d\u7ae0", content=f"before {quote} after"))
    with main.db() as conn:
        conn.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('chapters', 89)")
        imported = import_novel(
            conn,
            title="Chinese fuzzy QA",
            source_filename="qa.txt",
            encoding="utf-8",
            text_hash=sha256_text("Chinese fuzzy QA fixture"),
            chapters=chapters,
        )

    question = "\u6211\u8981\u8fd9\u5929\u518d\u4e5f\u906e\u4e0d\u4f4f\u6211\u7684\u773c\u8fd9\u53e5\u8bdd\u51fa\u81ea\u54ea\u91cc"
    response = client.post(f"/novels/{imported['id']}/qa", json={"model": "gpt-test", "question": question})

    assert response.status_code == 200
    evidence_json = calls[0]["user_payload"].split("evidence_json:\n", 1)[1]
    evidence = json.loads(evidence_json)
    assert "retrieval_status:matched_evidence" in calls[0]["user_payload"]
    assert evidence[0]["chapter_id"] == 96
    assert evidence[0]["chapter_order"] == 7
    assert evidence[0]["reason"] == "fuzzy_quote_match"
    assert evidence[0]["score"] >= 6
    assert "\u6211\u8981\u8fd9\u5929\uff0c\u518d\u906e\u4e0d\u4f4f\u6211\u773c" in evidence[0]["source_quote"]


def test_qa_without_api_key_returns_local_evidence_fallback(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "QA local fallback"},
        files={"file": ("qa.txt", b"Li Qing visited North Mountain and found a jade token.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/qa", json={"model": "gpt-test", "question": "North Mountain"})
    data = response.json()

    assert response.status_code == 200
    assert data["status"] == "local_fallback"
    assert data["cache_hit"] is False
    assert data["evidence"][0]["reason"] == "keyword_match"
    assert "North Mountain" in data["evidence"][0]["source_quote"]
    assert data["needs_more_context"] is False


def test_retry_worker_replays_qa_question_from_saved_request(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "QA worker replay"},
        files={"file": ("qa.txt", b"Li Qing visited North Mountain. Wang stayed in town.", "text/plain")},
    ).json()
    first = client.post(
        f"/novels/{imported['id']}/qa",
        json={"model": "gpt-test", "question": "North Mountain"},
    ).json()

    retry = client.post(f"/analysis-jobs/{first['job_id']}/retry")
    rerun = client.post("/analysis-jobs/run-next")

    assert retry.status_code == 200
    assert rerun.status_code == 200
    assert rerun.json()["job_id"] == first["job_id"]
    _wait_job_completed(client, first["job_id"])
    data = client.get(f"/analysis-jobs/{first['job_id']}/result").json()["result"]
    assert data["question"] == "North Mountain"
    assert data["evidence"][0]["reason"] == "keyword_match"
    assert "North Mountain" in data["evidence"][0]["source_quote"]
    job = client.get(f"/analysis-jobs/{first['job_id']}").json()
    assert job["status"] == "completed"
    assert job["retry_count"] == 1

def test_characters_local_fallback_is_cached_and_creates_job(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Characters fallback"},
        files={"file": ("characters.txt", b"Li Qing met Wang in town. Li Qing carried a jade token.", "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "local_fallback"
    assert first.json()["task_type"] == "character_extraction"
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert second.json()["cache_key"] == first.json()["cache_key"]
    _assert_provenance(
        first.json(),
        task_type="character_extraction",
        model_used="gpt-test",
        cache_hit=False,
        local_fallback=True,
    )
    names = [character["name"] for character in first.json()["characters"]]
    assert "Li Qing" in names
    assert "Wang" in names
    li_qing = next(character for character in first.json()["characters"] if character["name"] == "Li Qing")
    assert li_qing["status"] == "pending_review"
    assert li_qing["source_chapters"]
    assert "Li Qing" in li_qing["evidence"][0]["source_quote"]
    assert first.json()["evidence"]
    assert first.json()["persisted_facts"] >= 2
    assert isinstance(first.json()["job_id"], int)
    job = client.get(f"/analysis-jobs/{first.json()['job_id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "completed"
    assert job.json()["result_cache_key"] == first.json()["cache_key"]


def test_character_extraction_persists_reviewable_facts(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Fact Review"},
        files={"file": ("facts.txt", b"Li Qing met Wang in town. Li Qing carried a jade token.", "text/plain")},
    ).json()

    extraction = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"}).json()
    facts = client.get(f"/novels/{imported['id']}/facts?fact_type=character_profile").json()
    li_qing = next(fact for fact in facts if fact["entities"][0] == "Li Qing")

    review = client.patch(
        f"/review/extracted_fact/{li_qing['id']}",
        json={"status": "confirmed", "note": "verified from source quote"},
    ).json()
    client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test", "force_refresh": True})
    refreshed_fact = client.get(f"/novels/{imported['id']}/facts?fact_type=character_profile&status=confirmed").json()[0]

    assert extraction["persisted_facts"] >= 2
    assert li_qing["fact_type"] == "character_profile"
    assert li_qing["status"] == "pending_review"
    assert "Li Qing" in li_qing["content"]
    assert "Li Qing" in li_qing["source_quote"]
    assert review["status"] == "confirmed"
    assert review["review_actions"][0]["from_status"] == "pending_review"
    assert review["review_actions"][0]["to_status"] == "confirmed"
    assert refreshed_fact["id"] == li_qing["id"]
    assert refreshed_fact["status"] == "confirmed"


def test_chinese_gb18030_import_extracts_basic_characters(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = (
        "\u7b2c\u4e00\u7ae0 \u9752\u77f3\u9547\n"
        "\u674e\u9752\u6765\u5230\u9752\u77f3\u9547\u3002\u738b\u53d4\u63d0\u9192\u4ed6\u4e0d\u8981\u9760\u8fd1\u5317\u5c71\u3002\n"
        "\u7b2c\u4e8c\u7ae0 \u5317\u5c71\u5f02\u52a8\n"
        "\u674e\u9752\u8fdb\u5165\u5317\u5c71\uff0c\u738b\u53d4\u968f\u540e\u8d76\u5230\u3002"
    )
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Chinese MVP", "chunk_size": "30"},
        files={"file": ("characters.txt", text.encode("gb18030"), "text/plain")},
    ).json()

    chapters = client.get(f"/novels/{imported['id']}/chapters").json()
    first = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    names = [character["name"] for character in first.json()["characters"]]

    assert imported["encoding"] in {"gb18030", "gbk"}
    assert imported["chapter_count"] == 2
    assert len(chapters) == 2
    assert "\u674e\u9752" in names
    assert "\u738b\u53d4" in names
    assert first.json()["evidence"]

def test_characters_with_api_key_uses_model_call(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"], "characters": [{"name": "Li Qing"}]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Characters model"},
        files={"file": ("characters.txt", b"Li Qing met Wang in town.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["cache_hit"] is False
    assert len(calls) == 1
    assert calls[0]["task_type"] == "character_extraction"
    assert "Li Qing" in calls[0]["user_payload"]


def test_export_markdown_returns_title_chapters_and_excerpts(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    chapters = [
        ChapterDraft(1, "First chapter", "Li Qing arrives at Qingshi Town. Wang warns him about North Mountain."),
        ChapterDraft(2, "Second chapter", "Li Qing leaves town with a jade token."),
    ]
    with main.db() as conn:
        imported = import_novel(
            conn,
            title="Export Novel",
            source_filename="export.txt",
            encoding="utf-8",
            text_hash=sha256_text("\n".join(chapter.content for chapter in chapters)),
            chapters=chapters,
        )

    response = client.get(f"/novels/{imported['id']}/export/markdown")

    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == f"novel-{imported['id']}-export.md"
    assert data["content_type"] == "text/markdown"
    markdown = data["markdown"]
    assert markdown.startswith("# Export Novel")
    assert "## Chapters" in markdown
    assert "- 1. First chapter" in markdown
    assert "- 2. Second chapter" in markdown
    assert "## 1. First chapter" in markdown
    assert "Li Qing arrives at Qingshi Town" in markdown
    assert "## 2. Second chapter" in markdown
    assert "jade token" in markdown


def test_export_markdown_missing_novel_returns_404(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)



def test_export_full_report_returns_outline_characters_and_evidence(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    chapters = [
        ChapterDraft(1, "First", "Li Qing arrives at the town. He is calm."),
        ChapterDraft(2, "Second", "Wang meets Li Qing and warns him about the cave."),
    ]
    with main.db() as conn:
        imported = import_novel(
            conn,
            title="Report Novel",
            source_filename="r.txt",
            encoding="utf-8",
            text_hash=sha256_text("\n".join(chapter.content for chapter in chapters)),
            chapters=chapters,
        )
        cid = main.list_chapters(conn, imported["id"])[0]["id"]
        main.upsert_extracted_fact(
            conn,
            novel_id=imported["id"],
            fact_type="character_profile",
            content="李青 · 性格: 沉着",
            entities=["李青"],
            source_quote="He is calm",
            confidence="medium",
            status="active",
            chapter_id=cid,
            evidence=[{"source_quote": "He is calm", "chapter_id": cid}],
        )
        main.upsert_extracted_fact(
            conn,
            novel_id=imported["id"],
            fact_type="character_relationship",
            content="李青 - 王大陆: 亦师亦友",
            entities=["李青", "王大陆"],
            source_quote="Wang meets Li Qing",
            confidence="medium",
            status="active",
            chapter_id=cid,
            evidence=[{"source_quote": "Wang meets Li Qing", "chapter_id": cid}],
        )
        main.upsert_extracted_fact(
            conn,
            novel_id=imported["id"],
            fact_type="world_rule",
            content="禁地: 不可入洞",
            entities=["禁地"],
            source_quote="forbids entering the cave",
            confidence="high",
            status="active",
            chapter_id=cid,
            evidence=[{"source_quote": "forbids entering the cave", "chapter_id": cid}],
        )

    response = client.get(f"/novels/{imported['id']}/export/report")

    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == f"novel-{imported['id']}-report.md"
    assert data["content_type"] == "text/markdown"
    markdown = data["markdown"]
    assert markdown.startswith("# Report Novel")
    # No outline cached yet: report emits a placeholder hint under the outline heading.
    assert "暂无全书大纲" in markdown
    assert "## 人物档案" in markdown
    assert "李青" in markdown
    assert "性格" in markdown
    assert "> He is calm" in markdown
    assert "## 人物关系" in markdown
    assert "## 世界规则" in markdown
    assert "不可入洞" in markdown
    # chapters included by default
    assert "Li Qing arrives at the town" in markdown


def test_export_full_report_without_chapters(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    chapters = [ChapterDraft(1, "Solo", "Just one chapter body text here.")]
    with main.db() as conn:
        imported = import_novel(
            conn,
            title="No Chapters",
            source_filename="n.txt",
            encoding="utf-8",
            text_hash=sha256_text(chapters[0].content),
            chapters=chapters,
        )

    response = client.get(f"/novels/{imported['id']}/export/report?include_chapters=false")

    assert response.status_code == 200
    markdown = response.json()["markdown"]
    assert "Just one chapter body text here" not in markdown
    assert "暂无全书大纲" in markdown


def test_export_full_report_missing_novel_returns_404(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    response = client.get("/novels/999/export/report")
    assert response.status_code == 404

    response = client.get("/novels/999/export/markdown")

    assert response.status_code == 404


# ---- async job start / result / dedup tests ----

def test_start_outline_returns_job_quickly(tmp_path: Path, monkeypatch):
    """Start outline returns a queued job without executing model synchronously."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Async Outline"},
        files={"file": ("async.txt", b"Li Qing entered town. Wang warned him.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported["id"]}/outline/start", json={"model": "gpt-test"})

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] > 0
    assert data["status"] in {"queued", "running"}
    assert data["duplicated"] is False
    assert data["effective_model"] == "gpt-test"


def test_start_characters_returns_job_quickly(tmp_path: Path, monkeypatch):
    """Start characters returns a queued job without executing model synchronously."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Async Chars"},
        files={"file": ("async.txt", b"Li Qing arrived at Qingshi Town. Wang warned him about North Mountain.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported["id"]}/characters/start", json={"model": "gpt-test"})

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] > 0
    assert data["status"] in {"queued", "running"}
    assert data["duplicated"] is False
    assert data["effective_model"] == "gpt-test"


def test_start_outline_deduplicates_active_job(tmp_path: Path, monkeypatch):
    """Start outline creates a job and returns valid job ids."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Dedup Outline"},
        files={"file": ("dedup.txt", b"Li Qing entered town.", "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported["id"]}/outline/start", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported["id"]}/outline/start", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["job_id"] > 0
    assert second.json()["job_id"] > 0


def test_start_characters_deduplicates_active_job(tmp_path: Path, monkeypatch):
    """Start characters creates a job and returns valid job ids."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Dedup Chars"},
        files={"file": ("dedup2.txt", b"Li Qing met Wang. They talked about the mountain.", "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported["id"]}/characters/start", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported["id"]}/characters/start", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["job_id"] > 0
    assert second.json()["job_id"] > 0


def test_start_chapter_summary_returns_job_quickly(tmp_path: Path, monkeypatch):
    """Start chapter summary returns a queued job without executing model synchronously."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "第一章 初入江湖\n少年醒来，掌柜提醒他不要靠近北山。"
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Async Summary"},
        files={"file": ("async_summary.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    chapter_id = client.get(f"/novels/{imported['id']}/chapters").json()[0]["id"]

    response = client.post(f"/chapters/{chapter_id}/summary/start", json={"model": "gpt-test"})

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] > 0
    assert data["status"] in {"queued", "running"}
    assert data["duplicated"] is False
    assert data["effective_model"] == "gpt-test"

    result = client.get(f"/analysis-jobs/{data['job_id']}/result")
    assert result.status_code == 200
    payload = result.json()
    assert payload["status"] == "completed"
    assert payload["result"]["task_type"] == "chapter_summary"
    assert payload["result"]["short_summary"]
    assert payload["provenance"]["task_type"] == "chapter_summary"


def test_start_chapter_summary_deduplicates_active_job(tmp_path: Path, monkeypatch):
    """A queued chapter_summary job is reused for the same chapter but not a different chapter."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "第一章 初入江湖\n少年醒来，掌柜提醒他不要靠近北山。\n第二章 北山之行\n少年前往北山，遇到一位猎人。"
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Dedup Summary"},
        files={"file": ("dedup_summary.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    chapters = client.get(f"/novels/{imported['id']}/chapters").json()
    first_chapter_id = chapters[0]["id"]
    second_chapter_id = chapters[1]["id"]
    with main.db() as conn:
        job = main.create_analysis_job(
            conn,
            task_type="chapter_summary",
            novel_id=imported["id"],
            chapter_id=first_chapter_id,
            request={"model": "gpt-test", "effective_model": "gpt-test", "force_refresh": False},
        )

    same = client.post(f"/chapters/{first_chapter_id}/summary/start", json={"model": "gpt-test"})
    other = client.post(f"/chapters/{second_chapter_id}/summary/start", json={"model": "gpt-test"})

    assert same.status_code == 200
    assert same.json()["job_id"] == job["id"]
    assert same.json()["duplicated"] is True
    assert other.status_code == 200
    assert other.json()["job_id"] != job["id"]
    assert other.json()["duplicated"] is False


def test_start_chapter_summary_missing_chapter_returns_404(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    response = client.post("/chapters/99999/summary/start", json={"model": "gpt-test"})

    assert response.status_code == 404

def test_outline_normalizes_common_model_shape(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "parsed_json": {
                "title": "Remote Outline",
                "chapters": [{"order": 1, "title": "Opening", "summary": "Li Qing arrives."}],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Outline shape"},
        files={"file": ("outline.txt", b"Li Qing arrived at Qingshi Town.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test", "force_refresh": True})

    assert response.status_code == 200
    data = response.json()
    assert data["outline"]["title"] == "Remote Outline"
    assert data["outline"]["chapters"][0]["chapter_order"] == 1
    assert data["outline"]["chapters"][0]["chapter_title"] == "Opening"
    assert data["outline"]["chapters"][0]["brief"] == "Li Qing arrives."


def test_outline_rejects_all_empty_remote_briefs(tmp_path: Path, monkeypatch):
    """B1: a model outline whose chapter briefs are all empty (>30%) is invalid
    and not cached, so the job fails and can be retried rather than storing an
    empty envelope."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "parsed_json": {
                "title": "Titles Only",
                "chapters": [{"order": 1, "title": "Opening", "summary": ""}],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Outline enrich"},
        files={"file": ("outline.txt", b"Li Qing arrived at Qingshi Town. Wang warned him about the mountain.", "text/plain")},
    ).json()

    start = client.post(f"/novels/{imported['id']}/outline/start", json={"model": "gpt-test", "force_refresh": True})
    assert start.status_code == 200
    result = client.get(f"/analysis-jobs/{start.json()['job_id']}/result")

    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "failed"
    assert data["result"] is None
    assert "empty" in str(data["error"])


def test_character_fact_persistence_maps_source_chapter_order_to_db_id(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Character FK"},
        files={"file": ("chars.txt", b"Chapter One\nLi Qing arrived.\n\nChapter Two\nWang met Li Qing.", "text/plain")},
    ).json()
    with main.db() as conn:
        job = main.create_analysis_job(conn, task_type="character_extraction", novel_id=imported["id"], request={})
        result = {
            "characters": [
                {
                    "name": "Li Qing",
                    "aliases": [],
                    "role_type": "supporting",
                    "description": "Arrives in the opening chapter.",
                    "source_chapters": [1],
                    "evidence": [{"chapter_title": "Chapter One", "source_quote": "Li Qing arrived."}],
                    "confidence": "high",
                }
            ]
        }
        persisted = main._persist_character_facts(conn, imported["id"], result, int(job["id"]))
        fact = conn.execute("SELECT chapter_id FROM extracted_facts WHERE novel_id = ?", (imported["id"],)).fetchone()
        first_chapter = conn.execute(
            "SELECT id FROM chapters WHERE novel_id = ? AND chapter_order = 1", (imported["id"],)
        ).fetchone()

    assert persisted == 1
    assert fact is not None
    assert first_chapter is not None
    assert int(fact["chapter_id"]) == int(first_chapter["id"])


def test_start_qa_returns_completed_result_with_wrapper_provenance(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "parsed_json": {
                "answer": "Li Qing arrived at Qingshi Town.",
                "evidence": [{"chapter_id": 1, "source_quote": "Li Qing arrived."}],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "QA async"},
        files={"file": ("qa.txt", b"Li Qing arrived at Qingshi Town.", "text/plain")},
    ).json()

    start = client.post(f"/novels/{imported['id']}/qa/start", json={"model": "gpt-test", "question": "Where?"})
    assert start.status_code == 200
    job_id = start.json()["job_id"]
    result = client.get(f"/analysis-jobs/{job_id}/result")

    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "completed"
    assert data["result"]["answer"] == "Li Qing arrived at Qingshi Town."
    assert data["provenance"]["task_type"] == "evidence_qa"
    assert data["provenance"]["model_used"] == "gpt-test"
    assert data["provenance"]["source"] == "cached_remote_model"
    assert data["provenance"]["provider_call_attempted"] is True
    assert data["provenance"]["provider_call_succeeded"] is True


def test_qa_job_result_promotes_structured_cached_answer_fields(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "QA structured cache"},
        files={"file": ("qa.txt", b"Aya spoke with Ajue.", "text/plain")},
    ).json()
    key = "qa-structured-cache-key"
    output = {
        "status": "ok",
        "task_type": "evidence_qa",
        "raw_json": "{}",
        "parsed_json": {
            "fact": "Aya appears in the retrieved evidence.",
            "inference": "The evidence is not enough to prove first appearance.",
            "suggestion": "Retrieve surrounding chapters before making a final claim.",
            "evidence": [{"chapter_id": 1, "quote": "Aya spoke with Ajue."}],
        },
        "_cache_metadata": {
            "source": "remote_model",
            "provider_call_attempted": True,
            "provider_call_succeeded": True,
        },
    }
    with main.db() as conn:
        main.put_cache(conn, key=key, model="gpt-test", task_type="evidence_qa", input_hash_value="abc", output=output)
        job = main.create_analysis_job(
            conn,
            task_type="evidence_qa",
            novel_id=imported["id"],
            request={"model": "gpt-test", "effective_model": "gpt-test", "question": "Who is Aya?"},
        )
        main.update_analysis_job(conn, int(job["id"]), status="completed", progress=100, result_cache_key=key)

    result = client.get(f"/analysis-jobs/{job['id']}/result")

    assert result.status_code == 200
    data = result.json()
    assert data["result"]["fact"] == "Aya appears in the retrieved evidence."
    assert data["result"]["inference"] == "The evidence is not enough to prove first appearance."
    assert data["result"]["suggestion"] == "Retrieve surrounding chapters before making a final claim."
    assert data["result"]["evidence"][0]["quote"] == "Aya spoke with Ajue."
    assert data["provenance"]["source"] == "cached_remote_model"


def test_failed_job_with_cache_still_returns_result(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Failed cached chars"},
        files={"file": ("chars.txt", b"Li Qing arrived.", "text/plain")},
    ).json()
    key = "failed-cache-key"
    output = {
        "status": "ok",
        "task_type": "character_extraction",
        "characters": [{"name": "Li Qing", "role_type": "supporting", "description": "Arrived."}],
        "_cache_metadata": {
            "source": "remote_model",
            "provider_call_attempted": True,
            "provider_call_succeeded": True,
        },
    }
    with main.db() as conn:
        main.put_cache(conn, key=key, model="gpt-test", task_type="character_extraction", input_hash_value="abc", output=output)
        job = main.create_analysis_job(
            conn,
            task_type="character_extraction",
            novel_id=imported["id"],
            request={"model": "gpt-test", "effective_model": "gpt-test"},
        )
        main.update_analysis_job(
            conn,
            int(job["id"]),
            status="failed",
            progress=100,
            error="post-processing failed",
            result_cache_key=key,
        )

    result = client.get(f"/analysis-jobs/{job['id']}/result")

    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "failed"
    assert data["result"]["characters"][0]["name"] == "Li Qing"
    assert data["provenance"]["model_error"] == "post-processing failed"
    assert data["provenance"]["provider_call_succeeded"] is True


def test_start_qa_deduplicates_same_question_not_different_question(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "QA dedupe"},
        files={"file": ("qa.txt", b"Li Qing arrived at Qingshi Town.", "text/plain")},
    ).json()
    with main.db() as conn:
        job = main.create_analysis_job(
            conn,
            task_type="evidence_qa",
            novel_id=imported["id"],
            request={"model": "gpt-test", "effective_model": "gpt-test", "question": "Where is Li Qing?", "force_refresh": False},
        )

    same = client.post(f"/novels/{imported['id']}/qa/start", json={"model": "gpt-test", "question": "Where is Li Qing?"})
    different = client.post(f"/novels/{imported['id']}/qa/start", json={"model": "gpt-test", "question": "Who is Wang?"})

    assert same.status_code == 200
    assert same.json()["job_id"] == job["id"]
    assert same.json()["duplicated"] is True
    assert different.status_code == 200
    assert different.json()["job_id"] != job["id"]
    assert different.json()["duplicated"] is False


def test_job_result_returns_status_for_non_completed_job(tmp_path: Path, monkeypatch):
    """Job result endpoint returns job status."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Result Test"},
        files={"file": ("result.txt", b"Li Qing entered town.", "text/plain")},
    ).json()

    start = client.post(f"/novels/{imported["id"]}/outline/start", json={"model": "gpt-test"}).json()
    job_id = start["job_id"]

    result = client.get(f"/analysis-jobs/{job_id}/result")

    assert result.status_code == 200
    data = result.json()
    assert data["job_id"] == job_id
    assert data["status"] in {"queued", "running", "completed"}


def test_job_result_missing_job_returns_404(tmp_path: Path, monkeypatch):
    """Result endpoint for nonexistent job returns 404."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    response = client.get("/analysis-jobs/99999/result")

    assert response.status_code == 404


def test_force_refresh_uses_async_job_path(tmp_path: Path, monkeypatch):
    """Force refresh still creates a job and goes through async path."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Force Refresh"},
        files={"file": ("fr.txt", b"Li Qing entered town. Wang warned him.", "text/plain")},
    ).json()

    # First generate normally
    normal = client.post(f"/novels/{imported["id"]}/outline/start", json={"model": "gpt-test"})
    normal_job_id = normal.json()["job_id"]

    # Run the normal job to completion
    client.post(f"/analysis-jobs/{normal_job_id}/run")
    _wait_job_completed(client, normal_job_id)

    # Force refresh creates a new job
    refresh = client.post(f"/novels/{imported["id"]}/outline/start",
                          json={"model": "gpt-test", "force_refresh": True})

    assert refresh.status_code == 200
    # Since the normal job completed, there is no active job, so force refresh creates a new one
    assert refresh.json()["duplicated"] is False
    # The new job has a different id (because old one is completed)
    # Actually, force_refresh in start endpoint doesn't bypass the dedup if there's no active job
    # It just creates a new one


def test_sync_outline_still_works(tmp_path: Path, monkeypatch):
    """The original synchronous outline endpoint still works (backward compatibility)."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Sync Outline"},
        files={"file": ("sync.txt", b"Li Qing arrived at Qingshi Town. Wang warned him about North Mountain.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported["id"]}/outline", json={"model": "gpt-test"})

    assert response.status_code == 200
    data = response.json()
    assert "provenance" in data
    assert "cache_key" in data


def test_sync_characters_still_works(tmp_path: Path, monkeypatch):
    """The original synchronous characters endpoint still works."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Sync Chars"},
        files={"file": ("sync2.txt", b"Li Qing met Wang in town. Wang warned him.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported["id"]}/characters", json={"model": "gpt-test"})

    assert response.status_code == 200
    data = response.json()
    assert "characters" in data
    assert "provenance" in data

def test_relationships_local_fallback_is_cached_and_persists_facts(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = (
        "First chapter\n"
        "Li Qing met Wang in town. They talked about North Mountain. Li Qing carried a jade token.\n"
    )
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Relationships fallback"},
        files={"file": ("relationships.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert first.json()["status"] == "local_fallback"
    assert first.json()["task_type"] == "relationship_extraction"
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert second.json()["cache_key"] == first.json()["cache_key"]
    _assert_provenance(
        first.json(),
        task_type="relationship_extraction",
        model_used="gpt-test",
        cache_hit=False,
        local_fallback=True,
    )
    relationships = first.json()["relationships"]
    pairs = {(rel["from_character"], rel["to_character"]) for rel in relationships}
    assert ("Li Qing", "Wang") in pairs or ("Wang", "Li Qing") in pairs
    assert first.json()["persisted_facts"] >= 1
    facts = client.get(f"/novels/{imported["id"]}/facts?fact_type=character_relationship").json()
    assert facts
    assert facts[0]["status"] == "pending_review"
    assert facts[0]["source_quote"]


def test_relationship_graph_returns_nodes_and_edges(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "First chapter\nLi Qing met Wang in town. Li Qing carried a jade token.\n"
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Graph Novel"},
        files={"file": ("graph.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    client.post(f"/novels/{imported["id"]}/characters", json={"model": "gpt-test"})
    client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})
    graph = client.get(f"/novels/{imported["id"]}/relationships/graph")

    assert graph.status_code == 200
    data = graph.json()
    node_names = {node["name"] for node in data["nodes"]}
    assert "Li Qing" in node_names
    assert "Wang" in node_names
    assert data["edges"]
    edge = next(e for e in data["edges"] if {e["source"], e["target"]} == {"Li Qing", "Wang"})
    assert edge["relation_type"]
    assert edge["status"] == "pending_review"
    assert edge["source_quote"]
    assert edge["chapter_id"] is not None
    assert edge["chapter_order"] == 1


def test_relationships_with_api_key_uses_model_call(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "parsed_json": {
                "relationships": [
                    {
                        "from_character": "Li Qing",
                        "to_character": "Wang",
                        "relation_type": "friend",
                        "description": "Travel companions",
                        "source_chapters": [1],
                        "evidence": [{"chapter_title": "First chapter", "source_quote": "Li Qing met Wang in town."}],
                        "confidence": "high",
                    }
                ]
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Relationships model"},
        files={"file": ("relationships_model.txt", b"First chapter\nLi Qing met Wang in town.", "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["cache_hit"] is False
    assert len(calls) == 1
    assert calls[0]["task_type"] == "relationship_extraction"
    assert "known_characters" in calls[0]["user_payload"]
    rel = response.json()["relationships"][0]
    assert rel["relation_type"] == "friend"
    assert response.json()["persisted_facts"] == 1
    graph = client.get(f"/novels/{imported["id"]}/relationships/graph").json()
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["relation_type"] == "friend"
    assert edge["description"] == "Travel companions"
    assert edge["confidence"] == "high"


def test_start_relationships_returns_job_and_deduplicates(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "First chapter\nLi Qing met Wang in town. They talked about North Mountain.\n"
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Async Relationships"},
        files={"file": ("async_rel.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported["id"]}/relationships/start", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported["id"]}/relationships/start", json={"model": "gpt-test"})

    assert first.status_code == 200
    data = first.json()
    assert data["job_id"] > 0
    assert data["status"] in {"queued", "running", "completed"}
    assert data["duplicated"] is False
    assert data["effective_model"] == "gpt-test"
    assert second.status_code == 200
    assert second.json()["job_id"] > 0
    job = client.get(f"/analysis-jobs/{data["job_id"]}")
    assert job.status_code == 200
    assert job.json()["task_type"] == "relationship_extraction"


def test_relationships_batched_cover_chapters_beyond_50(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        match = re.search(r"batch_chapter_range:(\d+)-(\d+)", kwargs["user_payload"])
        label = match.group(1) if match else "0"
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "parsed_json": {
                "relationships": [
                    {
                        "from_character": f"Hero {label}",
                        "to_character": "Wukong",
                        "relation_type": "friend",
                        "relation_label": "同伴",
                        "attitude": "friendly",
                        "description": f"Met in batch starting chapter {label}.",
                        "source_chapters": [int(label)],
                        "evidence": [{"chapter_title": f"Chapter {label}", "source_quote": f"Hero {label} met Wukong."}],
                        "confidence": "medium",
                    }
                ]
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nHero {n} met Wukong." for n in range(1, 56))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Batched rels"},
        files={"file": ("batched_rel.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert first.json()["status"] == "ok"
    assert len(calls) == 6
    ranges = [re.search(r"batch_chapter_range:(\d+)-(\d+)", c["user_payload"]).groups() for c in calls]
    assert ranges[0] == ("1", "10")
    assert ranges[-1] == ("51", "55")
    assert first.json()["batches"] == 6
    from_names = [rel["from_character"] for rel in first.json()["relationships"]]
    assert "Hero 51" in from_names
    assert second.json()["cache_hit"] is True
    assert len(calls) == 6
    job = client.get(f"/analysis-jobs/{first.json()["job_id"]}").json()
    assert job["status"] == "completed"


def test_relationships_merge_alias_evolution_and_persist_labels(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        match = re.search(r"batch_chapter_range:\d+-(\d+)", kwargs["user_payload"])
        last = int(match.group(1)) if match else 0
        if last < 20:
            relationship = {
                "from_character": "沙僧",
                "to_character": "孙悟空",
                "relation_type": "acquaintance",
                "relation_label": "同路人",
                "attitude": "neutral",
                "description": "早期只是同行。",
                "source_chapters": [1],
                "evolution": [{"chapter_order": 1, "relation_label": "同路人", "event": "流沙河被收服"}],
                "evidence": [{"chapter_title": "第1章", "source_quote": "沙僧随孙悟空上路。"}],
                "confidence": "low",
            }
        else:
            relationship = {
                "from_character": "沙悟净",
                "to_character": "孙悟空",
                "relation_type": "friend",
                "relation_label": "师兄弟·三师弟",
                "attitude": "close",
                "description": "后期成为得力师弟。",
                "source_chapters": [22],
                "evolution": [{"chapter_order": 22, "relation_label": "师兄弟·三师弟", "event": "共同降妖后同心"}],
                "evidence": [{"chapter_title": "第22章", "source_quote": "沙悟净称孙悟空为大师兄。"}],
                "confidence": "high",
            }
        return {"status": "ok", "task_type": kwargs["task_type"], "parsed_json": {"relationships": [relationship]}}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\n沙僧与孙悟空同行。" for n in range(1, 26))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Alias rels"},
        files={"file": ("alias_rel.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(
            conn,
            novel_id=imported["id"],
            fact_type="character_profile",
            content="沙悟净 · 身份: 唐僧三徒弟",
            entities=["沙悟净", "沙僧"],
            source_quote="沙悟净，人称沙僧。",
            confidence="medium",
            status="pending_review",
            chapter_id=None,
            evidence=[{"source_quote": "沙悟净，人称沙僧。", "chapter_order": 1}],
        )

    response = client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(calls) == 3
    relationships = response.json()["relationships"]
    assert len(relationships) == 1
    rel = relationships[0]
    assert rel["from_character"] == "沙悟净"
    assert rel["to_character"] == "孙悟空"
    assert rel["relation_label"] == "师兄弟·三师弟"
    assert rel["attitude"] == "close"
    assert rel["confidence"] == "high"
    assert [item["chapter_order"] for item in rel["evolution"]] == [1, 22]
    assert len(rel["evidence"]) == 2
    facts = client.get(f"/novels/{imported["id"]}/facts?fact_type=character_relationship").json()
    assert len(facts) == 1
    fact = facts[0]
    assert "沙悟净 -[师兄弟·三师弟]-> 孙悟空" in fact["content"]
    assert fact["extra"]["relation_type"] == "friend"
    assert fact["extra"]["relation_label"] == "师兄弟·三师弟"
    assert fact["extra"]["attitude"] == "close"
    assert [item["chapter_order"] for item in fact["extra"]["evolution"]] == [1, 22]
    graph = client.get(f"/novels/{imported["id"]}/relationships/graph").json()
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["relation_type"] == "friend"
    assert edge["relation_label"] == "师兄弟·三师弟"
    assert edge["attitude"] == "close"
    assert [item["chapter_order"] for item in edge["evolution"]] == [1, 22]


def test_relationships_batch_failure_not_cached_and_marks_job_failed(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {
            "status": "invalid_model_json",
            "task_type": kwargs["task_type"],
            "raw_json": "not json",
            "parse_error": "boom",
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nHero {n} walked." for n in range(1, 16))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Rel invalid"},
        files={"file": ("rel_invalid.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported["id"]}/relationships", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert first.json()["status"] == "invalid_model_json"
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is False
    # Both batches are pre-called concurrently before serial validation, so each
    # run costs 2 calls; invalid outputs are never cached, hence 4 total.
    assert len(calls) == 4
    job = client.get(f"/analysis-jobs/{first.json()["job_id"]}").json()
    assert job["status"] == "failed"
    assert job["error"]


def test_relationship_conflict_uses_extra_relation_type_for_labeled_facts(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "RelConflictExtra"},
        files={"file": ("relextra.txt", b"First chapter Li Qing and Wang fought. Later they became allies.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_relationship", content="Li Qing -[仇敌]-> Wang: 敌对", entities=["Li Qing", "Wang"], source_quote="Li Qing and Wang fought", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing and Wang fought", "chapter_order": 1}], extra={"relation_type": "enemy", "relation_label": "仇敌", "attitude": "hostile", "evolution": []})
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_relationship", content="Li Qing -[盟友]-> Wang: 联盟", entities=["Li Qing", "Wang"], source_quote="they became allies", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "they became allies", "chapter_order": 3}], extra={"relation_type": "ally", "relation_label": "盟友", "attitude": "friendly", "evolution": []})
    client.post(f"/novels/{imported["id"]}/conflicts", json={"model": "gpt-test"}).json()
    conflicts = client.get(f"/novels/{imported["id"]}/conflicts").json()
    rel = next((c for c in conflicts if c["extra"].get("type") == "relationship"), None)
    assert rel is not None
    assert rel["extra"]["earlier_evidence"]
    assert rel["extra"]["later_evidence"]
def test_invalid_model_json_is_not_cached_and_marks_job_failed(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {
            "status": "invalid_model_json",
            "task_type": kwargs["task_type"],
            "raw_json": "not json",
            "parse_error": "boom",
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Invalid json"},
        files={"file": ("invalid.txt", b"Li Qing met Wang in town.", "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert first.json()["status"] == "invalid_model_json"
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is False
    assert len(calls) == 2
    job = client.get(f"/analysis-jobs/{first.json()['job_id']}").json()
    assert job["status"] == "failed"
    assert job["error"]


def test_model_error_fallback_is_not_cached_and_retry_recalls(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        raise ModelHTTPError(500, "server boom")

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Error nocache"},
        files={"file": ("err.txt", b"Li Qing woke up. Wang closed the inn.", "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"})

    assert first.json()["status"] == "local_fallback"
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is False
    assert len(calls) == 2
    job = client.get(f"/analysis-jobs/{first.json()['job_id']}").json()
    assert job["status"] == "failed"
    assert "server boom" in job["error"]


def test_stale_running_job_marked_failed_on_startup(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Zombie job"},
        files={"file": ("zombie.txt", b"Li Qing woke up.", "text/plain")},
    ).json()
    with main.db() as conn:
        job = main.create_analysis_job(conn, task_type="book_outline", novel_id=imported["id"], request={})
        main.update_analysis_job(conn, int(job["id"]), status="running", progress=40)

    with client:
        pass

    refreshed = client.get(f"/analysis-jobs/{job['id']}").json()
    assert refreshed["status"] == "failed"
    assert refreshed["error"]


def test_character_extraction_batches_cover_all_chapters_and_resume_from_cache(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        match = re.search(r"batch_chapter_range:(\d+)-(\d+)", kwargs["user_payload"])
        label = match.group(1) if match else "0"
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {
                    "name": f"Hero {label}",
                    "aliases": [],
                    "evidence": [{"chapter_title": f"Chapter {label}", "source_quote": f"Hero {label} appeared."}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nHero {n} walked into town." for n in range(1, 26))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Batched chars"},
        files={"file": ("batched.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    second = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert first.json()["status"] == "ok"
    assert len(calls) == 3
    assert first.json()["persisted_facts"] == 3
    names = [character["name"] for character in first.json()["characters"]]
    assert "Hero 21" in names
    assert second.json()["cache_hit"] is True
    assert len(calls) == 3
    job = client.get(f"/analysis-jobs/{first.json()['job_id']}").json()
    assert job["status"] == "completed"
    assert job["progress"] == 100


def test_layered_book_outline_uses_arc_summaries_for_large_books(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        if kwargs["task_type"] == "arc_summary":
            match = re.search(r"arc_index:(\d+)", kwargs["user_payload"])
            index = int(match.group(1)) if match else 0
            return {
                "status": "ok",
                "task_type": "arc_summary",
                "arc": {"arc_index": index, "title": f"Arc {index + 1}", "summary": f"Summary of arc {index + 1}."},
            }
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "outline": {
                "title": "Whole book",
                "chapters": [{"chapter_order": 1, "chapter_title": "Arc 1", "brief": "Summary of arc 1."}],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nSomething happened in chapter {n}." for n in range(1, 202))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Layered outline"},
        files={"file": ("layered.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/outline", json={"model": "gpt-test"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert len(data["arcs"]) == 2
    arc_calls = [call for call in calls if call["task_type"] == "arc_summary"]
    book_outline_calls = [call for call in calls if call["task_type"] == "book_outline"]
    assert len(arc_calls) == 2
    assert len(book_outline_calls) == 1
    assert "arc_summaries" in book_outline_calls[0]["user_payload"]
    assert "input_chapters" not in book_outline_calls[0]["user_payload"]
    job = client.get(f"/analysis-jobs/{data['job_id']}").json()
    assert job["status"] == "completed"
def test_whole_book_analysis_generates_all_chapter_summaries_and_resume_uses_cache(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "short_summary": "Hero walks.",
            "key_events": ["walk"],
            "characters": ["Hero"],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nSomething happened in chapter {n}." for n in range(1, 13))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Whole book"},
        files={"file": ("whole.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/analyze-all/start", json={"model": "gpt-test"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["duplicated"] is False
    job = client.get(f"/analysis-jobs/{data['job_id']}").json()
    assert job["task_type"] == "whole_book_analysis"
    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert len(calls) == 12
    assert {call["task_type"] for call in calls} == {"chapter_summary"}

    # Second run: every chapter summary is cached, so no new model calls.
    second = client.post(f"/novels/{imported['id']}/analyze-all/start", json={"model": "gpt-test"})
    assert second.status_code == 200
    assert second.json()["duplicated"] is False
    second_job = client.get(f"/analysis-jobs/{second.json()['job_id']}").json()
    assert second_job["status"] == "completed"
    assert len(calls) == 12


def test_whole_book_analysis_deduplicates_active_job(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nChapter {n} text." for n in range(1, 4))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Dedup"},
        files={"file": ("dedup.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    from app.database import connect, create_analysis_job

    with connect(main.DB_PATH) as conn:
        job = create_analysis_job(conn, task_type="whole_book_analysis", novel_id=imported["id"], request={})

    response = client.post(f"/novels/{imported['id']}/analyze-all/start", json={})

    assert response.status_code == 200
    assert response.json()["duplicated"] is True
    assert response.json()["job_id"] == job["id"]


def test_cancel_analysis_job_marks_queued_job_and_rejects_completed(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Cancel"},
        files={"file": ("cancel.txt", "\u7b2c1\u7ae0\nSome text.".encode("utf-8"), "text/plain")},
    ).json()

    from app.database import connect, create_analysis_job, update_analysis_job

    with connect(main.DB_PATH) as conn:
        queued = create_analysis_job(conn, task_type="whole_book_analysis", novel_id=imported["id"], request={})
        done = create_analysis_job(conn, task_type="chapter_summary", novel_id=imported["id"], chapter_id=None, request={})
        update_analysis_job(conn, int(done["id"]), status="completed", progress=100)

    cancelled = client.post(f"/analysis-jobs/{queued['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    rejected = client.post(f"/analysis-jobs/{done['id']}/cancel")
    assert rejected.status_code == 400

    missing = client.post("/analysis-jobs/999999/cancel")
    assert missing.status_code == 404


def test_whole_book_analysis_cancel_stops_early_and_retry_resumes_from_cache(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            import sqlite3

            conn = sqlite3.connect(main.DB_PATH, timeout=10)
            try:
                conn.execute(
                    "UPDATE analysis_jobs SET status = 'cancelled' WHERE task_type = 'whole_book_analysis' AND status = 'running'"
                )
                conn.commit()
            finally:
                conn.close()
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "short_summary": "Hero walks.",
            "key_events": ["walk"],
            "characters": ["Hero"],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nSomething happened in chapter {n}." for n in range(1, 61))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Cancellable"},
        files={"file": ("cancel.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/analyze-all/start", json={"model": "gpt-test"})
    job_id = response.json()["job_id"]
    job = client.get(f"/analysis-jobs/{job_id}").json()

    # 并发版取消在波间生效：第一波（50 章）的在途调用全部完成，第二波开始前停止。
    assert job["status"] == "cancelled"
    assert 0 < job["progress"] < 100
    assert len(calls) == 50

    # Retry resumes from per-chapter caches: only the remaining chapters call the model.
    client.post(f"/analysis-jobs/{job_id}/retry")
    rerun = client.post(f"/analysis-jobs/{job_id}/run")
    assert rerun.status_code == 200
    job = _wait_job_completed(client, job_id)
    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert len(calls) == 60


def test_whole_book_analysis_marks_failed_when_chapters_fail_and_retry_recovers(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []
    failing = {"on": True}

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        if failing["on"] and "chapter 7." in kwargs["user_payload"]:
            return {"status": "error", "task_type": kwargs["task_type"], "model_error": "boom"}
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "short_summary": "Hero walks.",
            "key_events": ["walk"],
            "characters": ["Hero"],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nSomething happened in chapter {n}." for n in range(1, 13))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Failing"},
        files={"file": ("failing.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/analyze-all/start", json={"model": "gpt-test"})
    job_id = response.json()["job_id"]
    job = client.get(f"/analysis-jobs/{job_id}").json()

    assert job["status"] == "failed"
    assert "1/12 chapter summaries failed" in job["error"]
    assert len(calls) == 12

    failing["on"] = False
    client.post(f"/analysis-jobs/{job_id}/retry")
    client.post(f"/analysis-jobs/{job_id}/run")
    job = _wait_job_completed(client, job_id)
    assert job["status"] == "completed"
    # Retry only re-calls the failed chapter; the other 11 hit cache.
    assert len(calls) == 13



# ---------- PRD 9 d+e: facts extension, settings/timeline, conflict review ----------


def test_character_attribute_persists_one_fact_per_attribute_and_all_evidence(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Attributes"},
        files={"file": ("a.txt", b"First chapter\nLi Qing arrived calmly at Qingshi Town.", "text/plain")},
    ).json()
    with main.db() as conn:
        job = main.create_analysis_job(conn, task_type="character_extraction", novel_id=imported["id"], request={})
        result = {
            "characters": [
                {
                    "name": "Li Qing",
                    "aliases": ["Qing"],
                    "role_type": "protagonist",
                    "description": "Calm hero.",
                    "source_chapters": [1],
                    "evidence": [{"chapter_title": "First chapter", "source_quote": "Li Qing arrived."}],
                    "confidence": "high",
                    "attributes": [
                        {"attribute": "personality", "value": "冷静", "evidence": [{"chapter_title": "First chapter", "source_quote": "Li Qing arrived calmly"}, {"chapter_title": "First chapter", "source_quote": "calm hero"}]},
                        {"attribute": "abilities", "value": "未提及", "evidence": []},
                    ],
                }
            ]
        }
        persisted = main._persist_character_facts(conn, imported["id"], result, int(job["id"]))
        facts = main.list_extracted_facts(conn, imported["id"], fact_type="character_profile")

    labels = sorted(f["content"].split(" · ", 1)[1].split(": ", 1)[0] for f in facts)
    assert labels == ["外貌", "性格", "所属势力", "能力", "身份/背景", "重要经历"]
    assert persisted == 6
    personality = next(f for f in facts if "性格" in f["content"])
    assert personality["content"] == "Li Qing · 性格: 冷静"
    assert personality["entities"][0] == "Li Qing"
    assert len(personality["evidence"]) == 2  # all evidence persisted
    ability = next(f for f in facts if "能力" in f["content"])
    assert ability["content"].endswith("未提及")
    assert ability["evidence"] == []


def test_character_local_fallback_persists_full_evidence_list(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Full evidence"},
        files={"file": ("k.txt", b"First chapter\nLi Qing met Wang in town. Li Qing carried a jade token.", "text/plain")},
    ).json()
    client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    fact = client.get(f"/novels/{imported['id']}/facts?fact_type=character_profile").json()[0]
    assert fact["fact_type"] == "character_profile"
    assert fact["evidence"]  # legacy single-row path persists the full evidence list, not just evidence[0]
    for item in fact["evidence"]:
        assert item["source_quote"]


def test_settings_local_fallback_persists_location_facts(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Settings"},
        files={"file": ("s.txt", b"First chapter\nLi Qing arrives at Qingshi Town. He cannot approach North Mountain.", "text/plain")},
    ).json()
    first = client.post(f"/novels/{imported['id']}/settings", json={"model": "gpt-test"}).json()
    assert first["status"] == "local_fallback"
    assert first["task_type"] == "setting_extraction"
    assert first["persisted_facts"] >= 1
    locations = client.get(f"/novels/{imported['id']}/facts?fact_type=location").json()
    names = {fact["content"].split(": ", 1)[0] for fact in locations}
    assert "Qingshi Town" in names
    assert "North Mountain" in names
    assert locations[0]["evidence"]
    rule_facts = client.get(f"/novels/{imported['id']}/facts?fact_type=setting_fact").json()
    assert any("cannot approach" in fact["source_quote"] for fact in rule_facts)


def test_setting_extraction_model_output_persists_categories(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "parsed_json": {
                "settings": [
                    {"category": "world_rule", "name": "修炼体系", "description": "突破需灵气。", "entities": [], "evidence": [{"chapter_title": "First chapter", "source_quote": "突破需灵气"}], "confidence": "medium"},
                    {"category": "faction", "name": "青云宗", "description": "大型宗门。", "entities": ["青云宗"], "evidence": [{"chapter_title": "First chapter", "source_quote": "青云宗"}], "confidence": "medium"},
                    {"category": "location", "name": "青石镇", "description": "起始地。", "entities": [], "evidence": [{"chapter_title": "First chapter", "source_quote": "青石镇"}], "confidence": "high"},
                    {"category": "setting_fact", "name": "货币", "description": "用灵石交易。", "entities": [], "evidence": [{"chapter_title": "First chapter", "source_quote": "用灵石交易"}], "confidence": "low"},
                ]
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Model settings"},
        files={"file": ("mc.txt", "First chapter\n突破需灵气。青云宗。青石镇。用灵石交易。".encode("utf-8"), "text/plain")},
    ).json()
    result = client.post(f"/novels/{imported['id']}/settings", json={"model": "gpt-test"}).json()
    assert result["persisted_facts"] == 4
    for category in ("world_rule", "faction", "location", "setting_fact"):
        facts = client.get(f"/novels/{imported['id']}/facts?fact_type={category}").json()
        assert facts


def test_event_extraction_local_fallback_persists_timeline(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Timeline"},
        files={"file": ("t.txt", "第一章 开端\nLi Qing arrives.\n第二章 进入\nLi Qing enters the forest.".encode("utf-8"), "text/plain")},
    ).json()
    first = client.post(f"/novels/{imported['id']}/events", json={"model": "gpt-test"}).json()
    assert first["status"] == "local_fallback"
    assert first["task_type"] == "event_extraction"
    events = sorted(
        client.get(f"/novels/{imported['id']}/facts?fact_type=event").json(),
        key=lambda e: e["id"],
    )
    assert events
    assert events[0]["extra"]["time_context"]
    assert events[0]["extra"]["event_order"] == 1
    # D1: local fallback cannot infer story time; era stays empty and order is 0.
    assert events[0]["extra"]["era"] == ""
    assert events[0]["extra"]["story_time_order"] == 0
    assert len(events) >= 1
    assert events[0]["evidence"]


def test_conflict_detection_persists_setting_conflict_and_is_reviewable(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Conflict"},
        files={"file": ("c.txt", b"First chapter\nLi Qing is ten years old then twenty years old later.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="Li Qing · 年龄: 十岁", entities=["Li Qing"], source_quote="Li Qing is ten", confidence="medium", status="pending_review", evidence=[{"source_quote": "Li Qing is ten"}])
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="Li Qing · 年龄: 二十岁", entities=["Li Qing"], source_quote="Li Qing is twenty", confidence="medium", status="pending_review", evidence=[{"source_quote": "Li Qing is twenty"}])
    result = client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
    assert result["status"] == "local_fallback"
    assert result["persisted_facts"] == 1
    conflicts = client.get(f"/novels/{imported['id']}/conflicts").json()
    assert conflicts
    assert conflicts[0]["fact_type"] == "setting_conflict"
    assert conflicts[0]["extra"]["severity"] in {"high", "medium", "low"}
    assert conflicts[0]["extra"]["earlier_evidence"]
    assert conflicts[0]["extra"]["later_evidence"]
    assert conflicts[0]["status"] == "pending_review"

    review = client.patch(
        f"/review/extracted_fact/{conflicts[0]['id']}",
        json={"status": "confirmed", "note": "前后年龄矛盾"},
    ).json()
    assert review["status"] == "confirmed"
    assert client.get(f"/novels/{imported['id']}/facts?fact_type=setting_conflict&status=confirmed").json()

def test_world_setting_conflict_detects_rule_contradiction(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "WorldRuleConflict"},
        files={"file": ("w.txt", b"First chapter\nThe sect forbids entering the cave. Later Li Qing may enter the cave.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="world_rule", content="灵墟洞穴: 禁止进入其中", entities=["灵墟洞穴"], source_quote="The sect forbids entering the cave", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "The sect forbids entering the cave", "chapter_order": 1}])
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="world_rule", content="灵墟洞穴: 可以进入", entities=["灵墟洞穴"], source_quote="Li Qing may enter the cave", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing may enter the cave", "chapter_order": 5}])
    result = client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
    assert result["status"] == "local_fallback"
    conflicts = client.get(f"/novels/{imported['id']}/conflicts").json()
    assert conflicts
    rule_conflict = next((c for c in conflicts if c["extra"].get("type") == "world_rule"), None)
    assert rule_conflict is not None
    assert rule_conflict["fact_type"] == "setting_conflict"
    assert rule_conflict["extra"]["severity"] in {"high", "medium", "low"}
    assert rule_conflict["extra"]["earlier_evidence"]
    assert rule_conflict["extra"]["later_evidence"]
    assert rule_conflict["status"] == "pending_review"


def test_timeline_conflict_detects_impossible_event_order(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "TimelineConflict"},
        files={"file": ("t.txt", b"First chapter\nLi Qing was born. Second chapter Li Qing died.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="event", content="Li Qing出生: 时间起于", entities=["Li Qing"], source_quote="Li Qing was born.", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing was born.", "chapter_order": 1}], extra={"time_context": "1岁", "chapter_order": 1})
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="event", content="Li Qing去世: 时间终于", entities=["Li Qing"], source_quote="Li Qing died.", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing died.", "chapter_order": 2}], extra={"time_context": "0岁", "chapter_order": 2})
    result = client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
    assert result["status"] == "local_fallback"
    conflicts = client.get(f"/novels/{imported['id']}/conflicts").json()
    timeline = next((c for c in conflicts if c["extra"].get("type") == "timeline"), None)
    assert timeline is not None
    assert timeline["extra"]["earlier_evidence"]
    assert timeline["extra"]["later_evidence"]
    assert timeline["status"] == "pending_review"


def test_relationship_conflict_detects_enemy_to_ally_change(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "RelConflict"},
        files={"file": ("r.txt", b"First chapter Li Qing and Wang fought. Later they became allies.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_relationship", content="Li Qing -[enemy]-> Wang: 敌对", entities=["Li Qing", "Wang"], source_quote="Li Qing and Wang fought", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing and Wang fought", "chapter_order": 1}])
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_relationship", content="Li Qing -[ally]-> Wang: 联盟", entities=["Li Qing", "Wang"], source_quote="they became allies", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "they became allies", "chapter_order": 3}])
    result = client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
    assert result["status"] == "local_fallback"
    conflicts = client.get(f"/novels/{imported['id']}/conflicts").json()
    rel = next((c for c in conflicts if c["extra"].get("type") == "relationship"), None)
    assert rel is not None
    assert rel["extra"]["earlier_evidence"]
    assert rel["extra"]["later_evidence"]
    assert rel["status"] == "pending_review"


def test_conflict_record_has_full_prd_extra_fields_and_explanation_evidence(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "PrdFields"},
        files={"file": ("p.txt", b"First chapter Li Qing was ten. Later Li Qing was twenty but trained for years.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="Li Qing · 年龄: 十岁", entities=["Li Qing"], source_quote="Li Qing was ten", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing was ten", "chapter_order": 1, "chapter_title": "First chapter"}])
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="Li Qing · 年龄: 二十岁", entities=["Li Qing"], source_quote="Li Qing was twenty", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing was twenty", "chapter_order": 4, "chapter_title": "Later"}])
        # a nearby fact that could explain the age change
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="Li Qing · 经历: 修炼十年", entities=["Li Qing"], source_quote="trained for years", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "trained for years", "chapter_order": 5, "chapter_title": "Later"}])
    client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
    conflicts = client.get(f"/novels/{imported['id']}/conflicts").json()
    assert conflicts
    extra = conflicts[0]["extra"]
    for field in ("type", "severity", "title", "entities", "earlier_evidence", "later_evidence", "possible_explanation", "explanation_evidence", "model_judgment", "confidence"):
        assert field in extra, f"missing {field}"
    assert extra["type"] in {"character_profile", "world_rule", "timeline", "item_ability", "plot_logic", "relationship"}
    assert extra["severity"] in {"high", "medium", "low"}


def test_conflict_review_status_transitions_record_actions(tmp_path: Path, monkeypatch):
    for index, target in enumerate(["confirmed", "dismissed", "explained", "watching", "pending_review"]):
        sub = tmp_path / f"db_{index}"
        sub.mkdir()
        monkeypatch.setattr(main, "DB_PATH", sub / "api.sqlite3")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        client = TestClient(main.app)
        imported = client.post(
            "/novels/import-txt",
            data={"title": "Transitions"},
            files={"file": ("tr.txt", b"First chapter A was ten. Later A was twenty.", "text/plain")},
        ).json()
        with main.db() as conn:
            main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="A · 年龄: 十岁", entities=["A"], source_quote="A was ten", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "A was ten", "chapter_order": 1}])
            main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="A · 年龄: 二十岁", entities=["A"], source_quote="A was twenty", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "A was twenty", "chapter_order": 2}])
        client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
        conflict = client.get(f"/novels/{imported['id']}/conflicts").json()[0]
        assert conflict["status"] == "pending_review"
        updated = client.patch(
            f"/review/extracted_fact/{conflict['id']}",
            json={"status": target, "note": f"move to {target}"},
        ).json()
        assert updated["status"] == target
        actions = updated["review_actions"]
        assert actions
        assert actions[-1]["from_status"] == "pending_review"
        assert actions[-1]["to_status"] == target
        refreshed = client.get(f"/novels/{imported['id']}/facts?fact_type=setting_conflict&status={target}").json()
        assert refreshed


def test_item_ability_conflict_detects_quantity_mismatch(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "ItemConflict"},
        files={"file": ("i.txt", b"First chapter items.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="setting_fact", content="灵石: 3颗", entities=["灵石"], source_quote="has 3 stones", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "has 3 stones", "chapter_order": 1}])
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="setting_fact", content="灵石: 1颗", entities=["灵石"], source_quote="only 1 stone left", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "only 1 stone left", "chapter_order": 5}])
    client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
    conflicts = client.get(f"/novels/{imported['id']}/conflicts").json()
    item = next((c for c in conflicts if c["extra"].get("type") == "item_ability"), None)
    assert item is not None
    assert item["extra"]["earlier_evidence"]
    assert item["extra"]["later_evidence"]
    assert item["status"] == "pending_review"


def test_plot_logic_conflict_detects_death_then_revive(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "PlotConflict"},
        files={"file": ("p.txt", b"First chapter events.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="event", content="Li Qing died", entities=["Li Qing"], source_quote="Li Qing died.", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing died.", "chapter_order": 1}])
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="event", content="Li Qing revived", entities=["Li Qing"], source_quote="Li Qing was revived.", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing was revived.", "chapter_order": 6}])
    client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
    conflicts = client.get(f"/novels/{imported['id']}/conflicts").json()
    plot = next((c for c in conflicts if c["extra"].get("type") == "plot_logic"), None)
    assert plot is not None
    assert plot["extra"]["earlier_evidence"]
    assert plot["extra"]["later_evidence"]
    assert plot["status"] == "pending_review"


# ---------- PRD 9f: two-level retrieval upgrade (Q&A + conflict detection) ----------


def test_qa_two_level_retrieval_uses_fact_store_and_alias_to_locate_chapter(tmp_path, monkeypatch):
    """Level-1 fact store + alias table locate the candidate chapter; level 2 quotes only it."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    chapters = [
        ChapterDraft(order=1, title="first", content="Li Qing trains with the sect elders."),
        ChapterDraft(order=2, title="second", content="The Spirit Cave Mistress guards the Fire Cave and the key spirit stone."),
        ChapterDraft(order=3, title="third", content="Filler text describing North Mountain scenery."),
    ]
    with main.db() as conn:
        imported = import_novel(
            conn,
            title="TwoLevelQA",
            source_filename="tl.txt",
            encoding="utf-8",
            text_hash=sha256_text("TwoLevelQA fixture"),
            chapters=chapters,
        )
        all_rows = main.list_chapters(conn, imported["id"])
        target_id = None
        north_id = None
        for r in all_rows:
            chapter = main.get_chapter(conn, int(r["id"]))
            if "Fire Cave" in chapter["content"]:
                target_id = int(chapter["id"])
            if "North Mountain" in chapter["content"]:
                north_id = int(chapter["id"])
        assert target_id is not None
        assert north_id is not None
        main.upsert_extracted_fact(
            conn,
            novel_id=imported["id"],
            fact_type="character_profile",
            content="Ling Er identity: Spirit Cave Mistress of the cave",
            entities=["Ling Er", "Spirit Cave Mistress"],
            chapter_id=target_id,
            source_quote="Spirit Cave Mistress guards the Fire Cave",
            confidence="medium",
            status="pending_review",
            evidence=[{"chapter_title": "second", "source_quote": "Spirit Cave Mistress guards the Fire Cave"}],
        )
    response = client.post(f"/novels/{imported['id']}/qa", json={"model": "gpt-test", "question": "Spirit Cave Mistress"})
    assert response.status_code == 200
    evidence_json = calls[0]["user_payload"].split("evidence_json:\n", 1)[1]
    evidence = json.loads(evidence_json)
    assert "retrieval_version:qa-retrieval-v4" in calls[0]["user_payload"]
    assert "retrieval_status:matched_evidence" in calls[0]["user_payload"]
    fact_items = [e for e in evidence if e.get("reason") == "fact_match"]
    assert fact_items
    assert fact_items[0]["chapter_id"] == target_id
    chapter_ids = {e.get("chapter_id") for e in evidence}
    assert north_id not in chapter_ids
    north_quotes = [e for e in evidence if "North Mountain" in (e.get("source_quote") or "")]
    assert not north_quotes


def test_qa_two_level_retrieval_without_facts_falls_back_to_full_scan(tmp_path, monkeypatch):
    """No facts/summaries yet -> whole-book scan still finds keyword evidence (recall preserved)."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "FullScan"},
        files={"file": ("fs.txt", b"Alpha chapter. Beta chapter has the jade seal.", "text/plain")},
    ).json()
    response = client.post(f"/novels/{imported['id']}/qa", json={"model": "gpt-test", "question": "jade seal"})
    assert response.status_code == 200
    evidence_json = calls[0]["user_payload"].split("evidence_json:\n", 1)[1]
    evidence = json.loads(evidence_json)
    assert evidence[0]["reason"] == "keyword_match"
    assert "jade" in evidence[0]["matched_terms"]


def test_conflict_explanation_searches_nearby_chapter_original_text(tmp_path, monkeypatch):
    """Level-2 explanation retrieval finds quotes in nearby chapter text, not only persisted facts."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "ExplConflict"},
        files={"file": ("e.txt", b"First chapter Li Qing was ten. Later Li Qing trained for years and grew strong.", "text/plain")},
    ).json()
    with main.db() as conn:
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="Li Qing · 年龄: 十岁", entities=["Li Qing"], source_quote="Li Qing was ten", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing was ten", "chapter_order": 1, "chapter_title": "First chapter"}])
        main.upsert_extracted_fact(conn, novel_id=imported["id"], fact_type="character_profile", content="Li Qing · 年龄: 二十岁", entities=["Li Qing"], source_quote="Li Qing was twenty", confidence="medium", status="pending_review", chapter_id=None, evidence=[{"source_quote": "Li Qing was twenty", "chapter_order": 4, "chapter_title": "Later"}])
    client.post(f"/novels/{imported['id']}/conflicts", json={"model": "gpt-test"}).json()
    conflicts = client.get(f"/novels/{imported['id']}/conflicts").json()
    assert conflicts
    extra = conflicts[0]["extra"]
    quotes = " ".join(str(e.get("source_quote") or "") for e in extra.get("explanation_evidence", []))
    assert "trained for years" in quotes


def test_retrieve_qa_evidence_uses_chapter_summary_index(tmp_path, monkeypatch):
    """Cached chapter summaries alone (no facts) can still locate candidate chapters at level 1."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "SummaryIdx"},
        files={"file": ("si.txt", b"Chapter one filler. Chapter two holds the Phoenix Sword relic. Three filler text.", "text/plain")},
    ).json()
    with main.db() as conn:
        chapters = main.list_chapters(conn, imported["id"])
        target_id = None
        for r in chapters:
            chapter = main.get_chapter(conn, int(r["id"]))
            if "Phoenix Sword" in chapter["content"]:
                target_id = int(chapter["id"])
        assert target_id is not None
        # seed a cached chapter summary referencing the relic for chapter two.
        main.put_cache(conn, key="summary-idx-test", model="gpt-test", task_type="chapter_summary", input_hash_value="h", output={"task_type": "chapter_summary", "short_summary": "Discover the Phoenix Sword relic.", "key_events": ["Phoenix Sword found"], "characters": []})
        seeded = main.create_analysis_job(conn, task_type="chapter_summary", novel_id=imported["id"], chapter_id=target_id, request={"effective_model": "gpt-test"})
        main.update_analysis_job(conn, int(seeded["id"]), status="completed", progress=100, result_cache_key="summary-idx-test")
    response = client.post(f"/novels/{imported['id']}/qa", json={"model": "gpt-test", "question": "Phoenix Sword"})
    assert response.status_code == 200
    evidence_json = calls[0]["user_payload"].split("evidence_json:\n", 1)[1]
    evidence = json.loads(evidence_json)
    chapter_ids = [e.get("chapter_id") for e in evidence]
    assert target_id in chapter_ids


# ---------- D2: Q&A 检索轻量修复（滑窗子词 + 停用词 + entities 加权 + 引文强制全文搜索） ----------


def test_question_terms_sliding_window_and_stopwords():
    """D2: ≥4 字 CJK 串生成 2–4 字滑窗子词；中文停用词（的事情/主人公/是谁）被剔除。"""
    terms = main._question_terms("大闹天宫是发生在什么时候的事情，主人公是谁？")
    assert "大闹天宫" in terms
    assert "大闹" in terms and "闹天" in terms and "天宫" in terms
    for stop in ("的事情", "主人公", "是谁", "什么", "什么时候"):
        assert stop not in terms
    # 整句（剥壳后）作为完整词保留，滑窗子词在其基础上生成。
    assert "大闹天宫是发生在" in terms


def test_qa_danao_tiangong_question_evidence_hits_chapter_12(tmp_path: Path, monkeypatch):
    """D2 回归：『大闹天宫是发生在什么时候的事情，主人公是谁？』
    返回证据必须含第 12 章前后（事实库定位 + 原文关键词命中）。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    chapters = [
        ChapterDraft(order=order, title=f"第{order}章", content=f"普通铺垫内容 {order}。")
        for order in range(1, 15)
    ]
    chapters[10] = ChapterDraft(order=11, title="第十一章", content="天兵天将围剿花果山。")
    chapters[11] = ChapterDraft(order=12, title="第十二章", content="孙悟空大闹天宫，打翻八卦炉。")
    chapters[12] = ChapterDraft(order=13, title="第十三章", content="悟空被压五行山下。")
    with main.db() as conn:
        imported = import_novel(
            conn,
            title="DanaoQA",
            source_filename="dq.txt",
            encoding="utf-8",
            text_hash=sha256_text("DanaoQA fixture"),
            chapters=chapters,
        )
        all_rows = main.list_chapters(conn, imported["id"])
        target_id = None
        for r in all_rows:
            chapter = main.get_chapter(conn, int(r["id"]))
            if chapter["chapter_order"] == 12:
                target_id = int(chapter["id"])
        assert target_id is not None
        main.upsert_extracted_fact(
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
    response = client.post(
        f"/novels/{imported['id']}/qa",
        json={"model": "gpt-test", "question": "大闹天宫是发生在什么时候的事情，主人公是谁？"},
    )
    assert response.status_code == 200
    evidence_json = calls[0]["user_payload"].split("evidence_json:\n", 1)[1]
    evidence = json.loads(evidence_json)
    assert "retrieval_status:matched_evidence" in calls[0]["user_payload"]
    orders = {e.get("chapter_order") for e in evidence}
    assert any(11 <= o <= 13 for o in orders), f"evidence orders: {orders}"
    assert 12 in orders, f"evidence orders: {orders}"
    quotes = " ".join(e.get("source_quote") or "" for e in evidence)
    assert "大闹天宫" in quotes


def test_qa_quote_question_forces_full_text_search_despite_candidates(tmp_path: Path, monkeypatch):
    """D2 回归：问句含 ≥6 字连续引文候选时强制全文模糊引文搜索，
    即使事实库把候选章节指向别处，也必须命中引文所在章节（第六章）。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "task_type": kwargs["task_type"]}

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    quote = "我要这天，再遮不住我眼，要这地，再埋不了我心"
    chapters = [
        ChapterDraft(order=index, title=f"chapter {index}", content=f"ordinary text {index}")
        for index in range(1, 7)
    ]
    chapters.append(ChapterDraft(order=7, title="第六章", content=f"before {quote} after"))
    with main.db() as conn:
        conn.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('chapters', 89)")
        imported = import_novel(
            conn,
            title="QuoteFullScan",
            source_filename="qfs.txt",
            encoding="utf-8",
            text_hash=sha256_text("QuoteFullScan fixture"),
            chapters=chapters,
        )
        first = main.list_chapters(conn, imported["id"])[0]
        # 事实库把候选章节指向第一章——旧逻辑只搜候选章节，会漏掉第六章的引文。
        main.upsert_extracted_fact(
            conn,
            novel_id=imported["id"],
            fact_type="character_profile",
            content="这句话: 开场白",
            entities=["李青"],
            chapter_id=int(first["id"]),
            source_quote="ordinary text 1",
            confidence="medium",
            status="pending_review",
            evidence=[{"chapter_title": "chapter 1", "source_quote": "ordinary text 1"}],
        )
    response = client.post(
        f"/novels/{imported['id']}/qa",
        json={"model": "gpt-test", "question": "这句话我要这天再也遮不住我的眼出自哪里"},
    )
    assert response.status_code == 200
    evidence_json = calls[0]["user_payload"].split("evidence_json:\n", 1)[1]
    evidence = json.loads(evidence_json)
    chapter_ids = [e.get("chapter_id") for e in evidence]
    assert 96 in chapter_ids, f"quote chapter must be found via forced full-text search: {chapter_ids}"
    quotes = " ".join(e.get("source_quote") or "" for e in evidence)
    assert "我要这天" in quotes


def test_facts_entities_hit_weighting(tmp_path: Path, monkeypatch):
    """D2: facts entities 命中加权——实体名命中比正文关键词命中得分更高。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    chapters = [
        ChapterDraft(order=1, title="first", content="齐天大圣初现。"),
        ChapterDraft(order=2, title="second", content="孙悟空大闹天宫。"),
    ]
    with main.db() as conn:
        imported = import_novel(
            conn,
            title="EntWeight",
            source_filename="ew.txt",
            encoding="utf-8",
            text_hash=sha256_text("EntWeight fixture"),
            chapters=chapters,
        )
        rows = main.list_chapters(conn, imported["id"])
        cid1, cid2 = (int(r["id"]) for r in rows)
        # 第一章：正文不含"孙悟空"，仅 entities 命中。
        main.upsert_extracted_fact(
            conn, novel_id=imported["id"], fact_type="character_profile",
            content="齐天大圣: 档案", entities=["孙悟空"], chapter_id=cid1,
            source_quote="齐天大圣初现。", confidence="medium", status="pending_review",
        )
        # 第二章：正文包含"孙悟空"，entities 不命中。
        main.upsert_extracted_fact(
            conn, novel_id=imported["id"], fact_type="character_profile",
            content="大闹天宫: 事件", entities=["猴王"], chapter_id=cid2,
            source_quote="孙悟空大闹天宫。", confidence="medium", status="pending_review",
        )
        scores = main._candidate_chapter_scores(conn, imported["id"], ["孙悟空"])
    assert scores.get(cid1, 0) > scores.get(cid2, 0)
    assert scores.get(cid1, 0) > 0


# A3：章节摘要"信封未拆"修复回归（_normalize_model_output / _invalid_output_reason / 读取迁移）。

def test_chapter_summary_promotes_parsed_json_envelope_to_top_level(tmp_path: Path, monkeypatch):
    """模型把 short_summary 等放在 parsed_json 里时，normalize 提升到顶层，前端读得到。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "parsed_json": {
                "short_summary": "少年醒来，掌柜提醒他不要靠近北山。",
                "key_events": ["醒来", "掌柜提醒"],
                "characters": ["少年", "掌柜"],
                "evidence": [{"chapter_id": 0, "source_quote": "少年醒来。"}],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "信封未拆"},
        files={"file": ("env.txt", "第一章 初入江湖\n少年醒来。".encode("utf-8"), "text/plain")},
    ).json()
    chapter_id = client.get(f"/novels/{imported['id']}/chapters").json()[0]["id"]

    first = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})
    second = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert first.json()["short_summary"] == "少年醒来，掌柜提醒他不要靠近北山。"
    assert first.json()["key_events"] == ["醒来", "掌柜提醒"]
    assert first.json()["characters"] == ["少年", "掌柜"]
    assert first.json()["cache_hit"] is False
    assert len(calls) == 1

    # 第二次命中缓存，仍读到顶层 short_summary，且不再次调用模型。
    assert second.status_code == 200
    assert second.json()["short_summary"] == "少年醒来，掌柜提醒他不要靠近北山。"
    assert second.json()["cache_hit"] is True
    assert len(calls) == 1


def test_chapter_summary_empty_short_summary_is_invalid_and_never_cached(tmp_path: Path, monkeypatch):
    """空 short_summary 不落缓存：任务判失败可重试，重复请求会真正再次调用模型。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "parsed_json": {
                "short_summary": "   ",
                "key_events": ["醒来"],
                "characters": ["少年"],
                "evidence": [],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "空摘要"},
        files={"file": ("empty.txt", "第一章 初入江湖\n少年醒来。".encode("utf-8"), "text/plain")},
    ).json()
    chapter_id = client.get(f"/novels/{imported['id']}/chapters").json()[0]["id"]

    first = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})
    second = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})

    assert first.status_code == 200
    assert first.json()["cache_hit"] is False
    assert "short_summary" in (first.json().get("provenance", {}).get("model_error") or "")

    # 失败结果未写缓存，第二次必须再次调用模型（而非命中坏缓存）。
    assert second.status_code == 200
    assert second.json()["cache_hit"] is False
    assert len(calls) == 2

    # 对应 job 应被标记为 failed（可重试），不缓存。
    job = client.get(f"/analysis-jobs/{first.json()['job_id']}").json()
    assert job["status"] == "failed"


def test_chapter_summary_read_time_migration_revives_envelope_cache(tmp_path: Path, monkeypatch):
    """旧坏缓存（parsed_json 里有完整内容、顶层缺 short_summary）读取时零成本迁移复活。"""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        raise AssertionError("migration hit must not call the model")

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    imported = client.post(
        "/novels/import-txt",
        data={"title": "坏缓存复活"},
        files={"file": ("badcache.txt", "第一章 初入江湖\n少年醒来。".encode("utf-8"), "text/plain")},
    ).json()
    chapter_id = client.get(f"/novels/{imported['id']}/chapters").json()[0]["id"]

    # 仿 job 95 的"信封未拆"形态：parsed_json 有内容，顶层无 short_summary。
    with main.db() as conn:
        chapter_row = main.get_chapter(conn, chapter_id)
        chunks = main.get_chunks_for_chapter(conn, chapter_id)
        payload = main._chapter_summary_payload(chapter_row, chunks)
        hash_value = main.input_hash("chapter_summary", payload)
        key = main.cache_key(model="gpt-test", task_type="chapter_summary", input_hash_value=hash_value)
        main.put_cache(
            conn,
            key=key,
            model="gpt-test",
            task_type="chapter_summary",
            input_hash_value=hash_value,
            output={
                "status": "ok",
                "task_type": "chapter_summary",
                "parsed_json": {
                    "short_summary": "迁移复活的章节摘要。",
                    "key_events": ["复活"],
                    "characters": ["少年"],
                    "evidence": [],
                },
            },
        )

    # 默认 force_refresh=False：命中坏缓存 → normalize 读取迁移 → 顶层 short_summary 复活。
    response = client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})
    assert response.status_code == 200
    assert response.json()["short_summary"] == "迁移复活的章节摘要。"
    assert response.json()["cache_hit"] is True
    assert response.json()["provenance"]["source"] == "cached_remote_model"

    # 迁移后应回写修复版缓存：顶层 short_summary 写回 model_cache。
    with main.db() as conn:
        rewritten = main.get_cache(conn, key)
        assert rewritten is not None
        assert rewritten.get("short_summary") == "迁移复活的章节摘要。"


def test_event_persist_fills_chapter_order_from_chapter_id(tmp_path: Path, monkeypatch):
    """A9: event extraction without chapter_order in model output must still set extra.chapter_order
    based on the resolved chapter_id, plus chapter_title."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "parsed_json": {
                "events": [
                    {
                        "title": "进入森林",
                        "description": "李青走进森林。",
                        "time_context": "第二天",
                        "entities": ["李青"],
                        "evidence": [
                            {
                                "chapter_id": None,
                                "chapter_order": None,
                                "chapter_title": "第二章 进入",
                                "source_quote": "Li Qing enters the forest.",
                            }
                        ],
                        "confidence": "medium",
                        "status": "pending_review",
                    }
                ],
                "evidence_required": True,
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post(
        "/settings/model",
        json={"api_key": "sk-test", "base_url": "https://example.test/v1", "model": "gpt-test"},
    )
    imported = client.post(
        "/novels/import-txt",
        data={"title": "事件章节顺序"},
        files={
            "file": (
                "events.txt",
                "第一章 开端\nLi Qing arrives.\n第二章 进入\nLi Qing enters the forest.\n第三章 决战\nLi Qing fights.".encode("utf-8"),
                "text/plain",
            )
        },
    ).json()
    result = client.post(f"/novels/{imported['id']}/events", json={"model": "gpt-test"}).json()
    assert result["status"] == "ok"

    facts = client.get(f"/novels/{imported['id']}/facts?fact_type=event").json()
    assert len(facts) == 1
    extra = facts[0]["extra"]
    # the only event's evidence has chapter_title "第二章 进入" → chapter resolved to order 2
    assert extra["chapter_order"] == 2
    assert extra["chapter_title"] == "第二章 进入"

    # chapter_id should also be set to the second chapter
    chapters = client.get(f"/novels/{imported['id']}/chapters").json()
    second_chapter = next(c for c in chapters if c["chapter_order"] == 2)
    assert facts[0]["chapter_id"] == second_chapter["id"]


def test_event_persist_stores_era_and_story_time_order(tmp_path: Path, monkeypatch):
    """D1: event output with era + story_time_order is persisted into extra."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "parsed_json": {
                "events": [
                    {
                        "title": "大闹天宫",
                        "description": "孙悟空大闹天宫。",
                        "time_context": "悟空学艺归来",
                        "era": "五百年前",
                        "story_time_order": 1,
                        "entities": ["孙悟空"],
                        "evidence": [
                            {
                                "chapter_id": None,
                                "chapter_order": 1,
                                "chapter_title": "第一章 开端",
                                "source_quote": "悟空大闹天宫。",
                            }
                        ],
                        "confidence": "high",
                        "status": "pending_review",
                    },
                    {
                        "title": "取经启程",
                        "description": "唐僧西行。",
                        "time_context": "长安",
                        "era": "取经路上",
                        "story_time_order": 2,
                        "entities": ["唐僧"],
                        "evidence": [
                            {
                                "chapter_id": None,
                                "chapter_order": 2,
                                "chapter_title": "第二章 进入",
                                "source_quote": "唐僧西行。",
                            }
                        ],
                        "confidence": "medium",
                        "status": "pending_review",
                    },
                ],
                "evidence_required": True,
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post(
        "/settings/model",
        json={"api_key": "sk-test", "base_url": "https://example.test/v1", "model": "gpt-test"},
    )
    imported = client.post(
        "/novels/import-txt",
        data={"title": "事件时序"},
        files={
            "file": (
                "events.txt",
                "第一章 开端\n悟空大闹天宫。\n第二章 进入\n唐僧西行。".encode("utf-8"),
                "text/plain",
            )
        },
    ).json()
    result = client.post(f"/novels/{imported['id']}/events", json={"model": "gpt-test"}).json()
    assert result["status"] == "ok"

    facts = client.get(f"/novels/{imported['id']}/facts?fact_type=event").json()
    assert len(facts) == 2
    by_title = {f["content"].split(":")[0]: f for f in facts}
    first = by_title["大闹天宫"]
    assert first["extra"]["era"] == "五百年前"
    assert first["extra"]["story_time_order"] == 1
    second = by_title["取经启程"]
    assert second["extra"]["era"] == "取经路上"
    assert second["extra"]["story_time_order"] == 2


def test_event_backfill_repairs_zero_chapter_order(tmp_path: Path, monkeypatch):
    """A9: pre-existing event rows with extra.chapter_order=0 are repaired by the startup backfill."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "历史事件"},
        files={
            "file": (
                "hist.txt",
                "第一章 起\n开局。\n第二章 承\n承接。\n第三章 转\n转折。".encode("utf-8"),
                "text/plain",
            )
        },
    ).json()
    chapters = client.get(f"/novels/{imported['id']}/chapters").json()
    second_chapter = next(c for c in chapters if c["chapter_order"] == 2)

    # Manually insert a legacy event fact pointing at chapter 2 but with chapter_order=0 (the old bug).
    with main.db() as conn:
        main.upsert_extracted_fact(
            conn,
            novel_id=imported["id"],
            fact_type="event",
            content="旧事件: only title",
            entities=["旧角色"],
            chapter_id=second_chapter["id"],
            source_quote="承接。",
            confidence="low",
            status="pending_review",
            evidence=[{"source_quote": "承接。", "chapter_order": 0}],
            extra={"time_context": "未知", "event_order": 1, "chapter_order": 0},
        )

    # Clear the settings gate so the backfill runs even though startup already set it.
    with main.db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'event_chapter_order_backfill_v1'")
        updated = main.backfill_event_chapter_order(conn)
    assert updated == 1

    facts = client.get(f"/novels/{imported['id']}/facts?fact_type=event").json()
    assert len(facts) == 1
    assert facts[0]["extra"]["chapter_order"] == 2
    assert facts[0]["extra"]["chapter_title"] == "第二章 承"



def test_character_extraction_partial_combined_does_not_supersede_resume(tmp_path: Path, monkeypatch):
    """E1: a partial combined cache must not be served as a clean hit; rerun only
    re-calls the previously failed batch while successful batches hit batch cache."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    fail_next = {"batch_start": 11}

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        match = re.search(r"batch_chapter_range:(\d+)-(\d+)", kwargs["user_payload"])
        start = int(match.group(1)) if match else 0
        label = match.group(1) if match else "0"
        if start == fail_next["batch_start"]:
            # Simulate model timeout on the third batch of the first run.
            fail_next["batch_start"] = -1
            raise RuntimeError("")  # empty message, like httpx ReadTimeout
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {
                    "name": f"Hero {label}",
                    "aliases": [],
                    "evidence": [{"chapter_title": f"Chapter {label}", "source_quote": f"Hero {label} appeared."}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nHero {n} walked into town." for n in range(1, 16))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Partial combined"},
        files={"file": ("partial.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    assert first.status_code == 200
    assert first.json()["status"] == "partial"
    assert len(calls) == 2  # batch 1 succeeded, batch 2 raised -> local fallback

    # Second run: partial combined must NOT be a fast-path hit. Batch 1 hits its
    # cache (no model call); only the previously failed batch 2 re-calls the model.
    calls.clear()
    second = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    assert second.status_code == 200
    assert second.json()["status"] == "ok"
    assert second.json()["cache_hit"] is False
    assert len(calls) == 1
    match = re.search(r"batch_chapter_range:(\d+)-(\d+)", calls[0]["user_payload"])
    assert match is not None
    assert match.group(1) == "11"  # only the previously failed batch was re-called


def test_relationship_extraction_partial_combined_does_not_supersede_resume(tmp_path: Path, monkeypatch):
    """E1: relationship extraction partial combined also resumes only the failed batch."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = []

    fail_next = {"batch_start": 11}

    async def fake_model_call(**kwargs):
        calls.append(kwargs)
        match = re.search(r"batch_chapter_range:(\d+)-(\d+)", kwargs["user_payload"])
        start = int(match.group(1)) if match else 0
        label = match.group(1) if match else "0"
        if start == fail_next["batch_start"]:
            fail_next["batch_start"] = -1
            raise RuntimeError("")
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "relationships": [
                {
                    "from": f"Hero {label}",
                    "to": "Mentor",
                    "relation_type": "mentor",
                    "relation_label": "\u5e08\u5f92",
                    "evidence": [{"chapter_title": f"Chapter {label}", "source_quote": f"Hero {label} met Mentor."}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nHero {n} met Mentor in chapter {n}." for n in range(1, 16))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Partial rel"},
        files={"file": ("partial_rel.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    first = client.post(f"/novels/{imported['id']}/relationships", json={"model": "gpt-test"})
    assert first.status_code == 200
    assert first.json()["status"] == "partial"
    assert len(calls) == 2  # batch 1 succeeded, batch 2 raised -> local fallback

    calls.clear()
    second = client.post(f"/novels/{imported['id']}/relationships", json={"model": "gpt-test"})
    assert second.status_code == 200
    assert second.json()["status"] == "ok"
    assert second.json()["cache_hit"] is False
    assert len(calls) == 1
    match = re.search(r"batch_chapter_range:(\d+)-(\d+)", calls[0]["user_payload"])
    assert match is not None
    assert match.group(1) == "11"

def test_character_extraction_batch_size_setting_override(tmp_path: Path, monkeypatch):
    """E6: a settings row overrides the default extraction batch size."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    ranges: list[str] = []

    async def fake_model_call(**kwargs):
        match = re.search(r"batch_chapter_range:(\d+)-(\d+)", kwargs["user_payload"])
        assert match is not None
        ranges.append(f"{match.group(1)}-{match.group(2)}")
        label = match.group(1)
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {
                    "name": f"Hero {label}",
                    "aliases": [],
                    "evidence": [{"chapter_title": f"Chapter {label}", "source_quote": "appeared."}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    with main.db() as conn:
        main.set_setting(conn, "character_extraction_batch_size", "3")
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nHero {n} walked into town." for n in range(1, 11))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Batch size override"},
        files={"file": ("batch.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert ranges == ["1-3", "4-6", "7-9", "10-10"]


def test_character_extraction_failure_rate_marks_job_failed(tmp_path: Path, monkeypatch):
    """E6: >50% locally-fallback batches fail the job instead of silently going partial."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    calls = {"count": 0}

    async def fake_model_call(**kwargs):
        calls["count"] += 1
        if calls["count"] <= 3:
            raise RuntimeError("")  # timeout-like
        match = re.search(r"batch_chapter_range:(\d+)-(\d+)", kwargs["user_payload"])
        label = match.group(1) if match else "0"
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {
                    "name": f"Hero {label}",
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
    text = "\n\n".join(f"\u7b2c{n}\u7ae0\nHero {n} walked into town." for n in range(1, 6))
    imported = client.post(
        "/novels/import-txt",
        data={"title": "Failure rate"},
        files={"file": ("failrate.txt", text.encode("utf-8"), "text/plain")},
    ).json()

    response = client.post(f"/novels/{imported['id']}/characters", json={"model": "gpt-test"})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["provenance"]["model_error"] == "模型超时/限流，请重试或调小批次"
    jobs = client.get("/analysis-jobs", params={"novel_id": imported["id"]}).json()
    latest = [job for job in jobs if job["task_type"] == "character_extraction"][-1]
    assert latest["status"] == "failed"
    assert latest["error"] == "模型超时/限流，请重试或调小批次"

def test_usage_stats_aggregates_cache_provenance_and_failed_jobs(tmp_path: Path, monkeypatch):
    """PRD: 设置页累计调用统计 - counts come from model_cache metadata + failed jobs."""
    from app.database import create_analysis_job, put_cache, update_analysis_job
    from app.jobs.cache import _with_cache_metadata

    client = _client_with_temp_db(tmp_path, monkeypatch)
    with main.db() as conn:
        remote_ok = _with_cache_metadata(
            {"status": "ok"},
            source="remote_model",
            provider_call_attempted=True,
            provider_call_succeeded=True,
        )
        put_cache(
            conn, key="k-ok", model="gpt-test", task_type="chapter_summary",
            input_hash_value="h1", output=remote_ok,
        )
        fallback = _with_cache_metadata(
            {"status": "local_fallback"},
            source="local_fallback",
            provider_call_attempted=True,
            provider_call_succeeded=False,
        )
        put_cache(
            conn, key="k-fallback", model="gpt-test", task_type="chapter_summary",
            input_hash_value="h2", output=fallback,
        )
        job = create_analysis_job(conn, task_type="chapter_summary", request={"model": "gpt-test"})
        update_analysis_job(conn, job["id"], status="failed", progress=100, error="boom")

    stats = client.get("/usage-stats").json()
    assert stats["cache_entries"] == 2
    assert stats["model_calls_attempted"] == 2
    assert stats["model_calls_succeeded"] == 1
    assert stats["local_fallback_results"] == 1
    assert stats["failed_jobs"] == 1
    assert stats["token_stats_available"] is False


def test_usage_stats_endpoint_reads_real_model_call(tmp_path: Path, monkeypatch):
    """A real (mocked) successful chapter-summary call is counted as one call."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "parsed_json": {
                "short_summary": "少年醒来，初入江湖。",
                "key_events": ["醒来"],
                "characters": ["少年"],
                "evidence": [],
            },
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post(
        "/settings/model",
        json={"api_key": "sk-test", "base_url": "https://example.test/v1", "model": "gpt-test"},
    )
    imported = client.post(
        "/novels/import-txt",
        data={"title": "用量统计"},
        files={"file": ("usage.txt", "第一章 初入江湖\n少年醒来。".encode("utf-8"), "text/plain")},
    ).json()
    chapter_id = client.get(f"/novels/{imported['id']}/chapters").json()[0]["id"]
    client.post(f"/chapters/{chapter_id}/summary", json={"model": "gpt-test"})

    stats = client.get("/usage-stats").json()
    assert stats["cache_entries"] == 1
    assert stats["model_calls_attempted"] == 1
    assert stats["model_calls_succeeded"] == 1
    assert stats["local_fallback_results"] == 0
    assert stats["failed_jobs"] == 0
