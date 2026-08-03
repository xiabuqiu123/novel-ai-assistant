"""B3: setting_extraction prompt/schema broadens the faction scope.

The fix-plan requires the faction category to explicitly cover regimes,
religions, sects, families, races and organizations (with examples such as
佛教/佛门/灵山佛门, 龙宫水族), while ``location`` is reserved for pure
geography. These tests assert the extraction payload carries that wording so a
model rerun uses the new caliber. (No model calls: payload text only.)
"""

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


def test_setting_extraction_payload_contains_broadened_faction_scope():
    rows = _rows()
    payload = main._setting_extraction_batch_payload(rows, rows)

    # Faction caliber now lists regime/religion/sect/family/race/organization.
    for keyword in ("政权", "宗教", "门派", "家族", "种族"):
        assert keyword in payload, f"faction scope missing {keyword}"
    # Concrete examples the model should treat as factions.
    for keyword in ("佛教", "佛门", "灵山佛门", "龙宫水族"):
        assert keyword in payload, f"faction example missing {keyword}"


def test_setting_extraction_payload_instructs_include_every_faction():
    rows = _rows()
    payload = main._setting_extraction_batch_payload(rows, rows)
    assert "Include EVERY faction" in payload
    assert "mentioned by name" in payload


def test_setting_extraction_payload_clarifies_location_is_geography_only():
    rows = _rows()
    payload = main._setting_extraction_batch_payload(rows, rows)
    assert "ONLY for pure geographic places" in payload
    # 灵山 disambiguation: religion -> faction, mountain -> location.
    assert "灵山" in payload
    assert 'classify it as "faction"' in payload or "not \"location\"" in payload


def test_setting_extraction_schema_is_stable_prefix_then_variable_data():
    """Stable prompt/schema precedes variable batch markers (cache-first)."""
    rows = _rows()
    payload = main._setting_extraction_batch_payload(rows, rows)
    schema_pos = payload.find("IMPORTANT RULES")
    marker_pos = payload.find("batch_chapter_range:")
    excerpt_pos = payload.find("source_excerpt:")
    assert schema_pos != -1 and marker_pos != -1 and excerpt_pos != -1
    assert schema_pos < marker_pos < excerpt_pos