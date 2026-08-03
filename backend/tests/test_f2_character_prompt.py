"""F2: character extraction prompt gains incremental-output/canonical-name/
alias-merge/reincarnation rules plus the 6th attribute "affiliation"
(所属势力), and the batch payload carries known_characters.

No model calls: payload/prompt text only.
"""
from app import main


def _rows():
    return [
        {
            "id": 101,
            "chapter_order": 1,
            "title": "大闹天宫",
            "content": "玄奘与孙悟空同行，沙悟净挑担。",
        },
        {
            "id": 102,
            "chapter_order": 2,
            "title": "取经起",
            "content": "天蓬被贬下凡转世为猪八戒。",
        },
    ]


def _payload():
    rows = _rows()
    return main._character_extraction_batch_payload(rows, rows, ["唐僧", "孙悟空"])


def test_character_schema_incremental_output_rule():
    payload = _payload()
    assert "每批只输出①本批首次出现的新人物；②已知人物中本批有新证据或属性变化的部分。无变化的已知人物不要输出" in payload


def test_character_schema_canonical_name_rule():
    payload = _payload()
    assert "已在 known_characters 的人物必须沿用其规范名；新人物选用书中出现最多的叫法作规范名" in payload


def test_character_schema_alias_merge_rule():
    payload = _payload()
    assert "同一人的所有称呼（简称/尊称/异体/错字变体）全部写入 aliases；禁止同一人拆成多个条目（例：玄奘=唐僧；沙僧=沙和尚=沙悟净=沙悟静 为同一人）" in payload


def test_character_schema_reincarnation_rule():
    payload = _payload()
    assert "若 A 是 B 的前世/转世，保留两个条目，并在各自身份/背景中互相注明" in payload
    assert "天蓬" in payload and "猪八戒" in payload
    assert "后被贬下凡转世为猪八戒" in payload
    assert "前世为天庭天蓬元帅" in payload


def test_character_schema_affiliation_attribute_and_role_split():
    payload = _payload()
    assert '"affiliation"' in payload
    assert "affiliation=所属势力" in payload
    assert "按时间线列出并标注章节" in payload
    assert "妖族（第1-9章）→ 取经队伍（第10章起）" in payload
    assert "无（未提及）" in payload
    assert "身份/背景只写人物本体（出身、种族、师承、血缘、称号来历），组织任职一律写入 affiliation，不要重复" in payload


def test_character_schema_affiliation_label_registered():
    assert main._CHARACTER_ATTRIBUTE_LABELS["affiliation"] == "所属势力"


def test_character_batch_payload_known_characters_in_stable_prefix_order():
    payload = _payload()
    schema_pos = payload.find("IMPORTANT RULES")
    known_pos = payload.find("known_characters: 唐僧, 孙悟空")
    marker_pos = payload.find("batch_chapter_range:")
    excerpt_pos = payload.find("source_excerpt:")
    assert schema_pos != -1 and known_pos != -1 and marker_pos != -1 and excerpt_pos != -1
    assert schema_pos < known_pos < marker_pos < excerpt_pos
    payload_empty = main._character_extraction_batch_payload(_rows(), _rows())
    assert "known_characters: (none extracted yet)" in payload_empty
