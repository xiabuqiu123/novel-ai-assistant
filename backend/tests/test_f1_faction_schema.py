"""F1: faction schema/prompt gains structured fields (aliases/type/parent/
sub_organizations/positions/relationships) plus merge/religion/rotation rules,
and persistence stores the structured fields in extracted_facts.extra_json.

No model calls: payload/prompt text and direct persistence are tested.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from app import main


def _rows():
    return [
        {
            "id": 101,
            "chapter_order": 1,
            "title": "大闹天宫",
            "content": "孙悟空大闹天宫，佛祖如来镇压，佛教灵山佛门，龙宫水族。",
        },
        {
            "id": 102,
            "chapter_order": 2,
            "title": "取经起",
            "content": "天庭众神议论，妖界蠢蠢欲动。",
        },
    ]


def _payload():
    rows = _rows()
    return main._setting_extraction_batch_payload(rows, rows)


def _client_with_temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "f1.sqlite3")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return TestClient(main.app)


def test_faction_schema_includes_structured_fields():
    payload = _payload()
    for keyword in (
        '"aliases"',
        '"type"',
        '"parent"',
        '"sub_organizations"',
        '"positions"',
        '"holder"',
        '"holder_intro"',
        '"rotation"',
        '"relationships"',
        "政权/宗教/门派/家族/种族/组织",
    ):
        assert keyword in payload, f"faction structured field missing {keyword}"


def test_faction_prompt_merge_aliases_rule():
    payload = _payload()
    assert "同一势力的不同称呼必须合并为一个条目并写入 aliases（例：神界、天宫与天庭指同一势力）" in payload


def test_faction_prompt_subordinate_rule():
    payload = _payload()
    assert "御马监、蟠桃园这类天庭下属机构必须作为天庭的下属，不得平级单列" in payload


def test_faction_prompt_religious_camp_rule():
    payload = _payload()
    assert "宗教阵营必须收录（例：如来、观音、地藏王归属的灵山佛门/西天）" in payload
    assert "以书中称谓命名" in payload


def test_faction_prompt_rotation_water_and_unmentioned_rules():
    payload = _payload()
    assert "职位需列出担任者与介绍；职位换人必须写轮换情况并标注章节" in payload
    assert "成员势力" in payload
    assert "以书中实际出现的龙宫/水族名称为准" in payload
    assert "未提及的字段写'未提及'，禁止编造" in payload


def test_setting_extraction_schema_stable_prefix_then_variable_data():
    """New rules live in the stable prefix, before the variable batch marker."""
    payload = _payload()
    schema_pos = payload.find("IMPORTANT RULES")
    rule_pos = payload.find("同一势力的不同称呼")
    marker_pos = payload.find("batch_chapter_range:")
    excerpt_pos = payload.find("source_excerpt:")
    assert schema_pos != -1 and marker_pos != -1 and excerpt_pos != -1
    assert schema_pos < rule_pos < marker_pos < excerpt_pos


def test_persist_setting_facts_stores_faction_extra(tmp_path: Path, monkeypatch):
    client = _client_with_temp_db(tmp_path, monkeypatch)
    imported = client.post(
        "/novels/import-txt",
        data={"title": "F1势力"},
        files={"file": ("f1.txt", "第一章 天宫\n天庭众神听令。".encode("utf-8"), "text/plain")},
    ).json()
    novel_id = int(imported["id"])
    with main.db() as conn:
        job = main.create_analysis_job(conn, task_type="setting_extraction", novel_id=novel_id)
        job_id = int(job["id"])
        result = {
            "status": "ok",
            "settings": [
                {
                    "category": "faction",
                    "name": "天庭",
                    "description": "统领三界众神的政权。",
                    "aliases": ["神界", "天宫"],
                    "type": "政权",
                    "parent": None,
                    "sub_organizations": ["御马监", "蟠桃园"],
                    "positions": [
                        {
                            "title": "玉帝",
                            "holder": "玉皇大帝",
                            "holder_intro": "天庭最高统治者。",
                            "rotation": "无",
                        }
                    ],
                    "relationships": [{"other": "灵山佛门", "summary": "各有默契"}],
                    "entities": ["天庭", "玉皇大帝"],
                    "source_chapters": [1],
                    "evidence": [{"chapter_id": 1, "chapter_order": 1, "chapter_title": "天宫", "source_quote": "天庭众神听令。"}],
                    "confidence": "high",
                    "status": "pending_review",
                }
            ],
        }
        persisted = main._persist_setting_facts(conn, novel_id, result, job_id)
        assert persisted == 1
        row = conn.execute(
            "SELECT * FROM extracted_facts WHERE novel_id = ? AND fact_type = 'faction'",
            (novel_id,),
        ).fetchone()
        assert row is not None
        extra = main.json.loads(row["extra_json"])
        assert extra["name"] == "天庭"
        assert extra["description"] == "统领三界众神的政权。"
        assert extra["aliases"] == ["神界", "天宫"]
        assert extra["type"] == "政权"
        assert extra["parent"] is None
        assert extra["sub_organizations"] == ["御马监", "蟠桃园"]
        assert extra["positions"][0]["holder"] == "玉皇大帝"
        assert extra["positions"][0]["rotation"] == "无"
        assert extra["relationships"][0]["other"] == "灵山佛门"
        assert row["entities_json"] and "天庭" in row["entities_json"]
