"""F3: character merge refactor - alias-to-canonical mapping across batches,
per-attribute merge with evidence union, reincarnation protection, and
duplicate_candidates reporting. No model calls (fake model in end-to-end
tests; merge helpers unit-tested directly).
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app import model_client


def _client_with_temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "f3.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return TestClient(main.app)


def _import_six_chapter_novel(client, title: str) -> int:
    text = "\n\n".join(f"第{n}章\nChapter {n} text." for n in range(1, 7))
    imported = client.post(
        "/novels/import-txt",
        data={"title": title},
        files={"file": ("f3.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    return int(imported["id"])


def test_cross_batch_alias_merge_combines_shaseng_into_one(tmp_path: Path, monkeypatch):
    """沙僧/沙和尚/沙悟净 from three batches merge into one canonical entry."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        start = int(match.group(1)) if match else 0
        if start == 1:
            name, aliases = "沙僧", ["沙和尚"]
        elif start == 3:
            name, aliases = "沙和尚", ["沙悟净"]
        else:
            name, aliases = "沙悟净", ["沙僧"]
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [
                {
                    "name": name,
                    "aliases": aliases,
                    "evidence": [{"chapter_title": f"Chapter {start}", "source_quote": f"{name} appeared."}],
                    "confidence": "medium",
                }
            ],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    with main.db() as conn:
        main.set_setting(conn, "character_extraction_batch_size", "2")
    novel_id = _import_six_chapter_novel(client, "F3沙僧合并")

    response = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    assert response["status"] == "ok"
    characters = response["characters"]
    assert len(characters) == 1
    entry = characters[0]
    assert entry["name"] == "沙僧"
    assert set(entry["aliases"]) == {"沙和尚", "沙悟净"}
    assert len(entry["evidence"]) == 3
    assert response.get("duplicate_candidates") == []

    with main.db() as conn:
        facts = main.list_extracted_facts(conn, novel_id, fact_type="character_profile")
    assert facts
    entities = set()
    for fact in facts:
        entities.update(fact["entities"])
    assert {"沙僧", "沙和尚", "沙悟净"} <= entities


def test_attribute_merge_keeps_evidence_union_and_affiliation_timeline(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "F3属性"},
        files={"file": ("f3a.txt", b"First chapter\nLi Qing arrived.", "text/plain")},
    ).json()
    with main.db() as conn:
        job = main.create_analysis_job(conn, task_type="character_extraction", novel_id=imported["id"], request={})
        existing = {
            "name": "沙僧",
            "aliases": [],
            "attributes": [
                {"attribute": "personality", "value": "冷静", "evidence": [{"chapter_title": "1", "source_quote": "q1"}, {"chapter_title": "1", "source_quote": "q2"}]},
                {"attribute": "affiliation", "value": "妖族（第1-9章）", "evidence": [{"chapter_title": "1", "source_quote": "q3"}]},
                {"attribute": "appearance", "value": "红发", "evidence": []},
            ],
        }
        incoming = {
            "name": "沙和尚",
            "aliases": [],
            "attributes": [
                {"attribute": "personality", "value": "沉稳", "evidence": [{"chapter_title": "2", "source_quote": "q2"}, {"chapter_title": "2", "source_quote": "q4"}]},
                {"attribute": "affiliation", "value": "妖族（第1-9章）→ 取经队伍（第10章起）", "evidence": [{"chapter_title": "2", "source_quote": "q5"}]},
                {"attribute": "appearance", "value": "未提及", "evidence": []},
            ],
        }
        main._merge_character_entry(existing, incoming)
        by_key = {attr["attribute"]: attr for attr in existing["attributes"]}

    assert existing["name"] == "沙僧"
    assert "沙和尚" in existing["aliases"]
    assert by_key["personality"]["value"] == "沉稳"
    assert {item["source_quote"] for item in by_key["personality"]["evidence"]} == {"q1", "q2", "q4"}
    assert by_key["affiliation"]["value"] == "妖族（第1-9章）→ 取经队伍（第10章起）"
    # 未提及 never erases an existing real value.
    assert by_key["appearance"]["value"] == "红发"


def test_tianpeng_and_zhu_bajie_stay_separate(tmp_path: Path, monkeypatch):
    """Reincarnation pair with disjoint names/aliases must not merge."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        start = int(match.group(1)) if match else 0
        if start == 1:
            entry = {"name": "天蓬", "aliases": ["天蓬元帅"]}
        else:
            entry = {"name": "猪八戒", "aliases": ["八戒"]}
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [entry | {"evidence": [{"chapter_title": "c", "source_quote": "appeared."}], "confidence": "medium"}],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    with main.db() as conn:
        main.set_setting(conn, "character_extraction_batch_size", "3")
    novel_id = _import_six_chapter_novel(client, "F3转世")

    response = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    names = {character["name"] for character in response["characters"]}
    assert names == {"天蓬", "猪八戒"}
    assert response.get("duplicate_candidates") == []


def test_duplicate_candidates_report_alias_crossing(tmp_path: Path, monkeypatch):
    """Entries linked by a later alias get reported, not auto-merged."""
    client = _client_with_temp_db(tmp_path, monkeypatch)

    async def fake_model_call(**kwargs):
        match = re.search(r"batch_chapter_range:(\d+)", kwargs["user_payload"])
        start = int(match.group(1)) if match else 0
        if start == 1:
            entry = {"name": "沙和尚", "aliases": []}
        elif start == 3:
            entry = {"name": "沙僧", "aliases": []}
        else:
            entry = {"name": "沙悟净", "aliases": ["沙僧", "沙和尚"]}
        return {
            "status": "ok",
            "task_type": kwargs["task_type"],
            "characters": [entry | {"evidence": [{"chapter_title": "c", "source_quote": "appeared."}], "confidence": "medium"}],
        }

    monkeypatch.setattr(model_client, "call_openai_compatible", fake_model_call)
    client.post("/settings/model", json={"api_key": "sk-test", "base_url": "", "model": "gpt-test"})
    with main.db() as conn:
        main.set_setting(conn, "character_extraction_batch_size", "2")
    novel_id = _import_six_chapter_novel(client, "F3疑似重名")

    response = client.post(f"/novels/{novel_id}/characters", json={"model": "gpt-test"}).json()
    names = {character["name"] for character in response["characters"]}
    assert names == {"沙和尚", "沙僧"}
    candidates = response.get("duplicate_candidates") or []
    assert any(
        (candidate["name_a"] == "沙僧" and candidate["name_b"] == "沙和尚")
        or (candidate["name_a"] == "沙和尚" and candidate["name_b"] == "沙僧")
        for candidate in candidates
    )


def test_merge_target_prefers_direct_name_hit():
    alias_map = {"沙僧": "沙僧", "沙和尚": "沙僧", "沙悟净": "沙僧"}
    character = {"name": "沙悟净", "aliases": ["沙僧"]}
    assert main._character_merge_target("沙悟净", character, alias_map) == "沙僧"
    character2 = {"name": "沙僧", "aliases": []}
    assert main._character_merge_target("沙僧", character2, alias_map) == "沙僧"
    character3 = {"name": "孙悟空", "aliases": ["行者"]}
    assert main._character_merge_target("孙悟空", character3, alias_map) is None
