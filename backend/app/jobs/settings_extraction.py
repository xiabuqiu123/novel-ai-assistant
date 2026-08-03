"""Task-domain module: settings_extraction (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import re
from ..database import supersede_previous_run_facts
from ..database import upsert_extracted_fact
from .common import _chapter_lookup_for_novel
from .common import _norm_evidence
from .common import _resolve_character_chapter_id
from .orchestration import _extraction_batch_size
from .orchestration import _run_batched_fact_extraction_job
from typing import Any


SETTING_EXTRACTION_BATCH_SIZE = 10


_SETTING_CATEGORIES = ("world_rule", "faction", "location", "setting_fact")


def _setting_extraction_schema() -> str:
    return (
        'Output a JSON object with a "settings" list. Each setting must be an object with:\n'
        '"category" (string: one of "world_rule", "faction", "location", "setting_fact"),\n'
        '"name" (string, the rule/faction/place/setting name),\n'
        '"description" (string, one-sentence detail from provided evidence only),\n'
        '"entities" (list of strings, related entity names),\n'
        '"source_chapters" (list of chapter numbers/ids where this setting appears),\n'
        '"evidence" (list of objects, each with chapter_title and source_quote),\n'
        '"confidence" (string: one of "high", "medium", "low"),\n'
        '"status" ("pending_review").\n\n'
        'For faction settings (category == "faction"), each faction object must also include:\n'
        '"aliases" (list of strings, all synonymous names of the same faction),\n'
        '"type" (string: one of 政权/宗教/门派/家族/种族/组织),\n'
        '"parent" (string or null, superior faction name),\n'
        '"sub_organizations" (list of strings, subordinate branch names),\n'
        '"positions" (list of objects: {"title" 职位名, "holder" 担任者人物名, "holder_intro" 担任者一两句介绍, "rotation" 轮换情况（如"第X章前是A，第X章后是B"；无轮换写"无"）}),\n'
        '"relationships" (list of objects: {"other" 势力名, "summary" 关系描述（如"连年战争"）}; optional).\n\n'
        "IMPORTANT RULES:\n"
        "- Only include settings supported by the provided excerpts; do NOT invent world rules, factions, locations or setting facts.\n"
        "- Every setting must carry at least one source_quote from the provided excerpts.\n"
        '- Use "world_rule" for cultivation/magic/social/geography/economy rules and restrictions.\n'
        '- Use "faction" for regimes, religions, sects, families, races and organizations — any named collective of people (政权/宗教/门派/家族/种族/组织); examples such as 佛教, 佛门, 灵山佛门, 龙宫水族 are all factions. Include EVERY faction mentioned by name in the excerpts.\n'
        '- Use "location" ONLY for pure geographic places (mountains, rivers, cities, realms), not "location" for groups of people. Disambiguation: "灵山" as the Buddhist collective ("灵山佛门") is a faction — classify it as "faction"; "灵山" as the geographic mountain stays "location".\n'
        '- Use "setting_fact" for other concrete worldbuilding facts.\n'
        "- 同一势力的不同称呼必须合并为一个条目并写入 aliases（例：神界、天宫与天庭指同一势力）。\n"
        "- 御马监、蟠桃园这类天庭下属机构必须作为天庭的下属，不得平级单列。\n"
        "- 宗教阵营必须收录（例：如来、观音、地藏王归属的灵山佛门/西天），即使书中未出现'佛教'二字，以书中称谓命名。\n"
        "- 职位需列出担任者与介绍；职位换人必须写轮换情况并标注章节。\n"
        "- 水族类势力必须列出成员势力（以书中实际出现的龙宫/水族名称为准）。\n"
        "- 未提及的字段写'未提及'，禁止编造。\n"
    )


def _setting_extraction_batch_payload(
    batch_rows: list[dict[str, Any]],
    full_rows: list[dict[str, Any]],
) -> str:
    first = batch_rows[0]
    last = batch_rows[-1]
    marker = (
        f"batch_chapter_range:{first['chapter_order']}-{last['chapter_order']} "
        f"batch_chapter_ids:{first['id']}-{last['id']}"
    )
    excerpts = "\n\n".join(
        f"chapter:{row['title']}\nsource_excerpt:{str(row['content'])[:2000]}" for row in full_rows
    )
    return _setting_extraction_schema() + "\n\n" + marker + "\n\n" + excerpts


def _local_setting_extraction(chapter_rows: list[dict[str, Any]]) -> dict[str, Any]:
    locations: dict[str, dict[str, Any]] = {}
    rules: list[dict[str, Any]] = []
    rule_markers = (
        "cannot", "must ", "forbidden", "never ", " only ", "not to ",
        "规则", "不能", "必须", "禁止", "唯有", "永远", "境界",
    )
    en_suffixes = (
        "Town", "Mountain", "City", "Forest", "River", "Kingdom",
        "Academy", "Guild", "Valley", "Village", "Tower", "Palace", "Sect",
    )
    cn_suffixes = ("镇", "城", "山", "谷", "林", "河", "国", "学院", "公会", "家族", "宗", "门", "派")
    en_pattern = re.compile(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+(" + "|".join(en_suffixes) + r")\b")
    cn_pattern = re.compile(r"([\u4e00-\u9fff]{1,6})(?:" + "|".join(cn_suffixes) + r")")

    def _location_item(name: str, row: dict[str, Any], para: str) -> dict[str, Any]:
        return {
            "category": "location",
            "name": name,
            "description": f"{name} 出现在文中。",
            "entities": [name],
            "source_chapters": [int(row["chapter_order"])],
            "evidence": [{
                "chapter_id": int(row["id"]),
                "chapter_order": int(row["chapter_order"]),
                "chapter_title": row["title"],
                "source_quote": para[:500],
            }],
            "confidence": "low",
            "status": "pending_review",
        }

    for row in chapter_rows:
        for paragraph in re.split(r"\n+", row["content"]):
            para = paragraph.strip()
            if not para:
                continue
            for match in en_pattern.finditer(para):
                name = f"{match.group(1)} {match.group(2)}".strip()
                if name not in locations:
                    locations[name] = _location_item(name, row, para)
            for match in cn_pattern.finditer(para):
                name = match.group(0)
                if name not in locations:
                    locations[name] = _location_item(name, row, para)
            if any(marker in para for marker in rule_markers):
                rules.append({
                    "category": "setting_fact",
                    "name": "规则线索",
                    "description": para[:200],
                    "entities": [],
                    "source_chapters": [int(row["chapter_order"])],
                    "evidence": [{
                        "chapter_id": int(row["id"]),
                        "chapter_order": int(row["chapter_order"]),
                        "chapter_title": row["title"],
                        "source_quote": para[:500],
                    }],
                    "confidence": "low",
                    "status": "pending_review",
                })
    settings = list(locations.values())[:50] + rules[:20]
    return {
        "status": "local_fallback",
        "task_type": "setting_extraction",
        "settings": settings,
        "evidence": [s["evidence"][0] for s in settings if s.get("evidence")][:5],
        "evidence_required": True,
    }


def _persist_setting_facts(
    conn, novel_id: int, result: dict[str, Any], job_id: int, seen_keys: set[str] | None = None
) -> int:
    settings = result.get("settings")
    if not isinstance(settings, list):
        return 0
    chapter_lookup = _chapter_lookup_for_novel(conn, novel_id)
    if seen_keys is None:
        seen_keys = set()
    superseded_categories: set[str] = set()
    persisted = 0
    for item in settings:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "setting_fact").strip().lower()
        if category not in _SETTING_CATEGORIES:
            category = "setting_fact"
        name = str(item.get("name") or "").strip()
        desc = str(item.get("description") or item.get("content") or "").strip()
        if name and desc:
            content = f"{name}: {desc}"
        else:
            content = name or desc
        if not content.strip():
            continue
        dedup_key = f"{category}:{content.strip().lower()}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        if category not in superseded_categories:
            supersede_previous_run_facts(
                conn, novel_id=novel_id, fact_type=category, current_run_id=job_id
            )
            superseded_categories.add(category)
        raw_entities = item.get("entities") if isinstance(item.get("entities"), list) else []
        entities = [str(e).strip() for e in raw_entities if str(e).strip()] or ([name] if name else [])
        ev = _norm_evidence(item.get("evidence"))
        first = ev[0] if ev else {}
        chapter_id = _resolve_character_chapter_id(item, first, chapter_lookup)
        source_quote = str(first.get("source_quote") or "")
        extra: dict[str, Any] = {}
        if category == "faction":
            extra = {
                "name": name,
                "description": desc,
                "aliases": [
                    str(alias).strip()
                    for alias in (item.get("aliases") if isinstance(item.get("aliases"), list) else [])
                    if str(alias).strip()
                ],
                "type": str(item.get("type") or "").strip(),
                "parent": str(item.get("parent") or "").strip() or None,
                "sub_organizations": [
                    str(sub).strip()
                    for sub in (item.get("sub_organizations") if isinstance(item.get("sub_organizations"), list) else [])
                    if str(sub).strip()
                ],
                "positions": [
                    dict(position)
                    for position in (item.get("positions") if isinstance(item.get("positions"), list) else [])
                    if isinstance(position, dict)
                ],
                "relationships": [
                    dict(relation)
                    for relation in (item.get("relationships") if isinstance(item.get("relationships"), list) else [])
                    if isinstance(relation, dict)
                ],
            }
            extra = {key: value for key, value in extra.items() if value not in ("", [])}
        upsert_extracted_fact(
            conn,
            novel_id=novel_id,
            fact_type=category,
            content=content,
            entities=entities,
            chapter_id=chapter_id,
            source_quote=source_quote,
            confidence=str(item.get("confidence") or "low"),
            status="pending_review",
            model_run_id=job_id,
            evidence=ev,
            extra=extra,
        )
        persisted += 1
    return persisted


async def _run_setting_extraction_job(conn, novel_id: int, model: str, force_refresh: bool, job_id: int) -> dict[str, Any]:
    return await _run_batched_fact_extraction_job(
        conn, novel_id, model, force_refresh, job_id,
        task_type="setting_extraction",
        batch_size=_extraction_batch_size(
            conn, "setting_extraction_batch_size", SETTING_EXTRACTION_BATCH_SIZE
        ),
        payload_builder=_setting_extraction_batch_payload,
        persist_fn=_persist_setting_facts,
        local_fn=_local_setting_extraction,
        job_label="setting_extraction",
    )