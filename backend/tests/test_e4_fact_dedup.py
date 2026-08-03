"""E4: fact persistence replaces per run (superseded) and dedups within a run.

Superseded rows are kept for audit but hidden from default fact listings.
The live (non-superseded) fact set must never accumulate across re-runs.
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app import model_client


def _client_with_temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "e4.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return TestClient(main.app)


def _rows(conn, novel_id: int, fact_type: str):
    return conn.execute(
        "SELECT * FROM extracted_facts WHERE novel_id = ? AND fact_type = ? ORDER BY id",
        (novel_id, fact_type),
    ).fetchall()


def _import_novel(client, title: str, text: str, filename: str = "e4.txt") -> int:
    imported = client.post(
        "/novels/import-txt",
        data={"title": title},
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
    ).json()
    return int(imported["id"])


def test_character_rerun_supersedes_previous_run_without_doubling_active(tmp_path: Path, monkeypatch):
    """E4: two character extraction runs keep the live fact count stable; the
    first run's facts become superseded (audit kept, not deleted)."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(
        client,
        "E4 chars",
        "第一章 开端\n李青走进青石镇。王叔提醒他北山危险。\n第二章 北山\n李青进入北山，王叔随后赶到。\n第三章 决战\n李青与王叔联手。",
    )

    first = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert first["persisted_facts"] >= 1
    with main.db() as conn:
        run1_rows = _rows(conn, novel_id, "character_profile")
    assert run1_rows
    run1_ids = {int(row["id"]) for row in run1_rows}
    run1_job_ids = {row["model_run_id"] for row in run1_rows}
    assert all(row["status"] == "pending_review" for row in run1_rows)

    second = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert second["persisted_facts"] >= 1

    with main.db() as conn:
        all_rows = _rows(conn, novel_id, "character_profile")
        active = [row for row in all_rows if row["status"] in ("active", "pending_review")]
        superseded = [row for row in all_rows if row["status"] == "superseded"]
    # The live fact count is the same as after run 1: no doubling of visible facts.
    assert len(active) == len(run1_rows)
    # Every run-1 row was flipped to superseded (audit trail preserved).
    assert run1_ids <= {int(row["id"]) for row in superseded}
    # The new run persisted under a fresh job id, never reusing run-1 rows.
    assert {row["model_run_id"] for row in active}.isdisjoint(run1_job_ids)
    # The default facts endpoint hides superseded rows.
    facts = client.get(f"/novels/{novel_id}/facts?fact_type=character_profile").json()
    assert len(facts) == len(run1_rows)
    assert all(fact["status"] != "superseded" for fact in facts)


def test_relationship_rerun_supersedes_previous_run_without_doubling_active(tmp_path: Path, monkeypatch):
    """E4: relationship extraction also replaces per run instead of appending."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(
        client,
        "E4 rels",
        "First chapter\nLi Qing met Wang in town. They talked about North Mountain.\n"
        "Second chapter\nLi Qing and Wang reached the mountain together.",
    )

    first = client.post(f"/novels/{novel_id}/relationships", json={"model": "gpt-test"}).json()
    assert first["persisted_facts"] >= 1
    with main.db() as conn:
        run1_rows = _rows(conn, novel_id, "character_relationship")
    assert run1_rows
    run1_ids = {int(row["id"]) for row in run1_rows}

    second = client.post(f"/novels/{novel_id}/relationships", json={"model": "gpt-test"}).json()
    assert second["persisted_facts"] >= 1

    with main.db() as conn:
        all_rows = _rows(conn, novel_id, "character_relationship")
        active = [row for row in all_rows if row["status"] in ("active", "pending_review")]
        superseded = [row for row in all_rows if row["status"] == "superseded"]
    assert len(active) == len(run1_rows)
    assert run1_ids <= {int(row["id"]) for row in superseded}
    facts = client.get(f"/novels/{novel_id}/facts?fact_type=character_relationship").json()
    assert len(facts) == len(run1_rows)
    assert all(fact["status"] != "superseded" for fact in facts)


def test_confirmed_fact_survives_rerun_while_pending_facts_are_superseded(tmp_path: Path, monkeypatch):
    """E4: human-reviewed (confirmed) facts are never superseded or reset by a
    re-run; only active/pending_review facts from earlier runs are replaced."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(
        client,
        "E4 review",
        "First chapter\nLi Qing met Wang in town. Wang warned Li Qing about North Mountain.",
    )
    client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"})
    facts = client.get(f"/novels/{novel_id}/facts?fact_type=character_profile").json()
    assert len(facts) >= 2
    confirmed_id = int(facts[0]["id"])
    review = client.patch(
        f"/review/extracted_fact/{confirmed_id}",
        json={"status": "confirmed", "note": "verified"},
    ).json()
    assert review["status"] == "confirmed"

    client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test", "force_refresh": True})

    with main.db() as conn:
        rows = _rows(conn, novel_id, "character_profile")
        confirmed = [row for row in rows if int(row["id"]) == confirmed_id]
    assert confirmed and confirmed[0]["status"] == "confirmed"
    # The confirmed row is not superseded and keeps its identity.
    confirmed_visible = client.get(
        f"/novels/{novel_id}/facts?fact_type=character_profile&status=confirmed"
    ).json()
    assert [fact["id"] for fact in confirmed_visible] == [confirmed_id]
    # Every non-confirmed run-1 fact was superseded; the rerun inserted fresh
    # pending rows that never reuse the confirmed row.
    with main.db() as conn:
        superseded = [row for row in _rows(conn, novel_id, "character_profile") if row["status"] == "superseded"]
        active = [row for row in _rows(conn, novel_id, "character_profile") if row["status"] in ("active", "pending_review", "confirmed")]
    assert superseded
    pending = client.get(f"/novels/{novel_id}/facts?fact_type=character_profile&status=pending_review").json()
    assert pending
    assert all(fact["id"] != confirmed_id for fact in pending)
    # The confirmed row is the only confirmed fact, and the live set stays the
    # same size as after run 1 (confirmed + fresh pending).
    assert len(active) == len(facts)


def test_persist_character_facts_dedups_by_name_and_aliases_within_run(tmp_path: Path, monkeypatch):
    """E4: within one run, characters sharing a normalized name or alias are
    persisted once (first occurrence wins)."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(client, "Dedup chars", "第一章\n李青来了。")
    with main.db() as conn:
        job = main.create_analysis_job(conn, task_type="character_extraction", novel_id=novel_id, request={})
        result = {
            "characters": [
                {
                    "name": "悟空",
                    "aliases": [],
                    "evidence": [{"chapter_title": "第一章", "source_quote": "悟空来了。"}],
                    "confidence": "low",
                },
                {
                    "name": "孙悟空",
                    "aliases": ["悟空"],
                    "evidence": [{"chapter_title": "第一章", "source_quote": "悟空来了。"}],
                    "confidence": "low",
                },
                {
                    "name": "李青",
                    "aliases": [],
                    "evidence": [{"chapter_title": "第一章", "source_quote": "李青来了。"}],
                    "confidence": "low",
                },
            ]
        }
        persisted = main._persist_character_facts(conn, novel_id, result, int(job["id"]))
        rows = _rows(conn, novel_id, "character_profile")
    assert persisted == 2  # 悟空 (dup of 孙悟空's alias) skipped, 李青 kept
    assert len(rows) == 2
    contents = [row["content"] for row in rows]
    assert any("悟空" in content for content in contents)
    assert any("李青" in content for content in contents)


def test_persist_relationship_facts_dedups_by_normalized_pair_within_run(tmp_path: Path, monkeypatch):
    """E4: relationships dedup on the normalized (from,to) pair; direction
    swapped variants collapse into one row."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(client, "Dedup rels", "第一章\n李青与王叔相遇。")
    with main.db() as conn:
        job = main.create_analysis_job(conn, task_type="relationship_extraction", novel_id=novel_id, request={})
        result = {
            "relationships": [
                {
                    "from_character": "李青",
                    "to_character": "王叔",
                    "relation_type": "friend",
                    "evidence": [{"chapter_title": "第一章", "source_quote": "相遇。"}],
                    "confidence": "low",
                },
                {
                    "from_character": "王叔",
                    "to_character": "李青",
                    "relation_type": "friend",
                    "evidence": [{"chapter_title": "第一章", "source_quote": "相遇。"}],
                    "confidence": "low",
                },
                {
                    "from_character": "李青",
                    "to_character": "师父",
                    "relation_type": "mentor",
                    "evidence": [{"chapter_title": "第一章", "source_quote": "拜师。"}],
                    "confidence": "low",
                },
            ]
        }
        persisted = main._persist_relationship_facts(conn, novel_id, result, int(job["id"]))
        rows = _rows(conn, novel_id, "character_relationship")
    assert persisted == 2
    assert len(rows) == 2


def test_persist_event_facts_dedups_by_title_and_chapter_order_within_run(tmp_path: Path, monkeypatch):
    """E4: events dedup on normalized title + chapter_order within one run."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(client, "Dedup events", "第一章\n孙悟空大闹天宫。\n第二章\n天兵败退。")
    with main.db() as conn:
        job = main.create_analysis_job(conn, task_type="event_extraction", novel_id=novel_id, request={})
        result = {
            "events": [
                {
                    "title": "大闹天宫",
                    "description": "打翻天庭。",
                    "chapter_order": 1,
                    "evidence": [{"chapter_title": "第一章", "source_quote": "孙悟空大闹天宫。"}],
                    "confidence": "medium",
                },
                {
                    "title": "大闹天宫",
                    "description": "再次描述。",
                    "chapter_order": 1,
                    "evidence": [{"chapter_title": "第一章", "source_quote": "孙悟空大闹天宫。"}],
                    "confidence": "medium",
                },
                {
                    "title": "大闹天宫",
                    "description": "第二章继续。",
                    "chapter_order": 2,
                    "evidence": [{"chapter_title": "第二章", "source_quote": "天兵败退。"}],
                    "confidence": "medium",
                },
            ]
        }
        persisted = main._persist_event_facts(conn, novel_id, result, int(job["id"]))
        rows = _rows(conn, novel_id, "event")
    assert persisted == 2
    assert len(rows) == 2
    import json
    orders = sorted(json.loads(row["extra_json"])["chapter_order"] for row in rows)
    assert orders == [1, 2]


def test_event_extraction_dedups_same_event_across_batches(tmp_path: Path, monkeypatch):
    """E4: the batched runner shares a per-run dedup key set, so the same event
    surfaced in two batches is persisted once."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "events": [
                {
                    "title": "大闹天宫",
                    "description": "孙悟空打上天庭。",
                    "chapter_order": 1,
                    "entities": ["孙悟空"],
                    "evidence": [{"chapter_title": "第一章", "source_quote": "孙悟空大闹天宫。"}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    chapters = "\n".join(f"第{n}章\n第{n}章内容。" for n in range(1, 26))
    novel_id = _import_novel(client, "Batch dedup events", chapters)
    with main.db() as conn:
        main.set_setting(conn, "event_extraction_batch_size", "20")

    result = client.post(f"/novels/{novel_id}/events", json={"model": "gpt-test"}).json()
    assert result["status"] == "ok"
    assert result["persisted_facts"] == 1
    facts = client.get(f"/novels/{novel_id}/facts?fact_type=event").json()
    assert len(facts) == 1


def test_persist_setting_facts_supersedes_previous_run_and_dedups_within_run(tmp_path: Path, monkeypatch):
    """E4: setting-type facts replace the previous run and dedup by content
    within a run."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(client, "Dedup settings", "第一章\n灵气复苏。")
    with main.db() as conn:
        job1 = main.create_analysis_job(conn, task_type="setting_extraction", novel_id=novel_id, request={})
        run1 = {
            "settings": [
                {"category": "world_rule", "name": "修炼体系", "description": "突破需灵气。", "entities": [], "evidence": [{"chapter_title": "第一章", "source_quote": "突破需灵气。"}], "confidence": "low"},
                {"category": "world_rule", "name": "修炼体系", "description": "突破需灵气。", "entities": [], "evidence": [{"chapter_title": "第一章", "source_quote": "突破需灵气。"}], "confidence": "low"},
            ]
        }
        assert main._persist_setting_facts(conn, novel_id, run1, int(job1["id"])) == 1
        rows_after_run1 = _rows(conn, novel_id, "world_rule")
        assert len(rows_after_run1) == 1
        job2 = main.create_analysis_job(conn, task_type="setting_extraction", novel_id=novel_id, request={})
        assert main._persist_setting_facts(conn, novel_id, run1, int(job2["id"])) == 1
        rows = _rows(conn, novel_id, "world_rule")
        active = [row for row in rows if row["status"] in ("active", "pending_review")]
        superseded = [row for row in rows if row["status"] == "superseded"]
    assert len(rows) == 2  # audit kept: run-1 row + run-2 row
    assert len(active) == 1
    assert len(superseded) == 1
    assert int(superseded[0]["id"]) == int(rows_after_run1[0]["id"])


def test_persist_conflict_facts_supersedes_previous_run_and_dedups_within_run(tmp_path: Path, monkeypatch):
    """E4: setting_conflict facts also replace per run and dedup by title."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(client, "Dedup conflicts", "第一章\n李青年龄前后矛盾。")
    with main.db() as conn:
        job1 = main.create_analysis_job(conn, task_type="conflict_detection", novel_id=novel_id, request={})
        run1 = {
            "conflicts": [
                {
                    "title": "李青年龄矛盾",
                    "type": "character_profile",
                    "severity": "high",
                    "entities": ["李青"],
                    "earlier_evidence": [{"chapter_title": "第一章", "source_quote": "十岁。"}],
                    "later_evidence": [{"chapter_title": "第一章", "source_quote": "二十岁。"}],
                    "confidence": "medium",
                },
                {
                    "title": "李青年龄矛盾",
                    "type": "character_profile",
                    "severity": "high",
                    "entities": ["李青"],
                    "earlier_evidence": [{"chapter_title": "第一章", "source_quote": "十岁。"}],
                    "later_evidence": [{"chapter_title": "第一章", "source_quote": "二十岁。"}],
                    "confidence": "medium",
                },
            ]
        }
        assert main._persist_conflict_facts(conn, novel_id, run1, int(job1["id"])) == 1
        job2 = main.create_analysis_job(conn, task_type="conflict_detection", novel_id=novel_id, request={})
        assert main._persist_conflict_facts(conn, novel_id, run1, int(job2["id"])) == 1
        rows = _rows(conn, novel_id, "setting_conflict")
        active = [row for row in rows if row["status"] in ("active", "pending_review")]
        superseded = [row for row in rows if row["status"] == "superseded"]
    assert len(rows) == 2
    assert len(active) == 1
    assert len(superseded) == 1


def test_upsert_does_not_reuse_superseded_row(tmp_path: Path, monkeypatch):
    """E4: a later run never resurrects a superseded row; it inserts a fresh
    row so the superseded generation stays intact for audit."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(client, "No resurrect", "第一章\n李青来了。")
    with main.db() as conn:
        job1 = main.create_analysis_job(conn, task_type="character_extraction", novel_id=novel_id, request={})
        first = main.upsert_extracted_fact(
            conn,
            novel_id=novel_id,
            fact_type="character_profile",
            content="李青: protagonist",
            entities=["李青"],
            source_quote="李青来了。",
            confidence="low",
            status="pending_review",
            model_run_id=int(job1["id"]),
        )
        main.supersede_previous_run_facts(
            conn, novel_id=novel_id, fact_type="character_profile", current_run_id=int(job1["id"]) + 100
        )
        first_after_status = conn.execute(
            "SELECT status FROM extracted_facts WHERE id = ?", (int(first["id"]),)
        ).fetchone()["status"]
        job2 = main.create_analysis_job(conn, task_type="character_extraction", novel_id=novel_id, request={})
        second = main.upsert_extracted_fact(
            conn,
            novel_id=novel_id,
            fact_type="character_profile",
            content="李青: protagonist",
            entities=["李青"],
            source_quote="李青来了。",
            confidence="low",
            status="pending_review",
            model_run_id=int(job2["id"]),
        )
    assert int(second["id"]) != int(first["id"])
    assert second["status"] == "pending_review"
    assert first_after_status == "superseded"


def test_list_extracted_facts_excludes_superseded_by_default(tmp_path: Path, monkeypatch):
    """E4: default fact listings hide superseded rows; include_superseded
    exposes them for audit tooling."""
    client = _client_with_temp_db(tmp_path, monkeypatch)
    novel_id = _import_novel(client, "List filter", "第一章\n李青来了。")
    with main.db() as conn:
        main.upsert_extracted_fact(
            conn,
            novel_id=novel_id,
            fact_type="character_profile",
            content="李青: protagonist",
            entities=["李青"],
            source_quote="李青来了。",
            confidence="low",
            status="pending_review",
        )
        main.upsert_extracted_fact(
            conn,
            novel_id=novel_id,
            fact_type="character_profile",
            content="王叔: supporting",
            entities=["王叔"],
            source_quote="王叔来了。",
            confidence="low",
            status="pending_review",
        )
        main.supersede_previous_run_facts(
            conn, novel_id=novel_id, fact_type="character_profile", current_run_id=999
        )
        default_rows = main.list_extracted_facts(conn, novel_id, fact_type="character_profile")
        full_rows = main.list_extracted_facts(
            conn, novel_id, fact_type="character_profile", include_superseded=True
        )
    assert len(default_rows) == 0
    assert len(full_rows) == 2
    assert all(row["status"] == "superseded" for row in full_rows)
