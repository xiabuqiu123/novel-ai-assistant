"""Task-domain module: conflicts (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import json
import re
from ..database import get_chapter
from ..database import list_chapters
from ..database import list_extracted_facts
from ..database import supersede_previous_run_facts
from ..database import update_analysis_job
from ..database import upsert_extracted_fact
from .cache import _cached_model_task
from .cache import _invalid_output_reason
from .cache import _with_cache_metadata
from .common import _norm_evidence
from .orchestration import _base_source
from .orchestration import _write_run_summary
from typing import Any


def _search_chapter_text_for_explanation(conn, novel_id: int, entities, lo_order, hi_order, limit: int = 4) -> list[dict[str, Any]]:
    """Level-2 retrieval for conflict explanations: search nearby/later chapter original text."""
    ents = [str(e).strip() for e in entities if e and len(str(e).strip()) >= 2]
    if not ents:
        return []
    lo = int(lo_order or 0)
    hi = int(hi_order or 0)
    if hi < lo:
        lo, hi = hi, lo
    hi = hi + 3
    rows = conn.execute(
        "SELECT id, chapter_order, title FROM chapters WHERE novel_id = ? AND chapter_order BETWEEN ? AND ? ORDER BY chapter_order",
        (novel_id, max(lo, 1), hi),
    ).fetchall()
    collected: list[dict[str, Any]] = []
    for row in rows:
        chapter = get_chapter(conn, int(row["id"]))
        text = str(chapter["content"] or "")
        low = text.lower()
        for ent in ents:
            idx = low.find(ent.lower())
            if idx >= 0:
                start = max(0, idx - 24)
                quote = text[start:idx + len(ent) + 220].strip()
                collected.append({
                    "chapter_id": int(chapter["id"]),
                    "chapter_order": int(chapter["chapter_order"]),
                    "chapter_title": str(chapter["title"] or ""),
                    "source_quote": quote,
                })
                break
        if len(collected) >= limit:
            break
    return collected


_CONFLICT_SEVERITIES = ("high", "medium", "low")


def _conflict_judgment_schema() -> str:
    return """Output a JSON object with a "conflicts" list. You are given candidate contradictions already grouped;
judge each candidate only from the provided evidence. Each conflict must be an object with:
"type" (string: copy the candidate's category, one of "character_profile", "world_rule", "timeline", "item_ability", "plot_logic", "relationship"),
"title" (string),
"severity" (string: one of "high", "medium", "low"),
"entities" (list of strings),
"earlier_evidence" (list of objects with chapter_title and source_quote),
"later_evidence" (list of objects with chapter_title and source_quote),
"possible_explanation" (string, empty if none),
"explanation_evidence" (list of objects with chapter_title and source_quote; copy from candidate if any),
"model_judgment" (string, your judgment: "conflict", "explorable", "not_conflict"),
"confidence" (string: one of "high", "medium", "low").

IMPORTANT RULES:
- Judge only from provided evidence; do NOT invent new plot facts.
- If evidence does not actually contradict, set model_judgment to "not_conflict" and severity to "low".
"""


# 有界化: 大书候选与引文可能达到数十万字, 单次模型请求必须受限（PRD 7 有界目标）。
_MAX_CONFLICT_CANDIDATES = 60
_MAX_CONFLICT_QUOTE_CHARS = 200


# noqa: E501
_CONFLICT_CANDIDATE_EVIDENCE_KEYS = ("earlier_evidence", "later_evidence", "explanation_evidence")


def _bound_conflict_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    bounded = dict(candidate)
    for key in _CONFLICT_CANDIDATE_EVIDENCE_KEYS:
        items = bounded.get(key)
        if not isinstance(items, list):
            continue
        clipped: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                clipped.append(
                    {
                        **item,
                        "source_quote": str(item.get("source_quote") or "")[:_MAX_CONFLICT_QUOTE_CHARS],
                    }
                )
            elif isinstance(item, str):
                clipped.append(item[:_MAX_CONFLICT_QUOTE_CHARS])
            else:
                clipped.append(item)
        bounded[key] = clipped
    return bounded


def _conflict_judgment_payload(candidates: list[dict[str, Any]]) -> str:
    total = len(candidates)
    bounded = [_bound_conflict_candidate(candidate) for candidate in candidates[:_MAX_CONFLICT_CANDIDATES]]
    payload = json.dumps(
        {
            "candidates": bounded,
            "candidate_count": total,
            "truncated": total > _MAX_CONFLICT_CANDIDATES,
        },
        ensure_ascii=False,
    )
    return _conflict_judgment_schema() + "\n\n" + payload


_CONFLICT_CANDIDATE_TYPES = (
    "character_profile", "world_rule", "timeline",
    "item_ability", "plot_logic", "relationship",
)


_REL_OPPOSITES = {
    ("ally", "enemy"), ("friend", "enemy"), ("ally", "rival"),
    ("romantic", "enemy"), ("family", "enemy"), ("family", "rival"),
}


_NEG_POLARITY_MARKERS = (
    "不能", "不可", "禁止", "无法", "绝不", "绝非", "禁",
    "cannot", "must not", "forbidden", "never", "not allowed",
)


_POS_POLARITY_MARKERS = (
    "可以", "能够", "允许", "许可", "必须", "应当", "应该",
    "can", "may", "must", "allowed",
)


_IGNORE_ATTRIBUTE_VALUE = "未提及"


def _fact_evidence(fact: dict[str, Any]) -> list[dict[str, Any]]:
    ev = fact.get("evidence")
    return [item for item in ev if isinstance(item, dict)] if isinstance(ev, list) else []


def _fact_entities(fact: dict[str, Any]) -> list[str]:
    ents = fact.get("entities")
    if not isinstance(ents, list):
        return []
    return [str(e).strip() for e in ents if str(e).strip()]


def _fact_chapter_order(fact: dict[str, Any]) -> int | None:
    extra = fact.get("extra")
    if isinstance(extra, dict):
        co = extra.get("chapter_order")
        if isinstance(co, (int, float)) and co:
            return int(co)
    for item in _fact_evidence(fact):
        if isinstance(item, dict):
            co = item.get("chapter_order")
            if isinstance(co, (int, float)) and co:
                return int(co)
    return None


def _conflict_order_key(fact: dict[str, Any]) -> tuple[int, int]:
    return (_fact_chapter_order(fact) or 0, int(fact.get("id") or 0))


def _evidence_or_quote(fact: dict[str, Any]) -> list[dict[str, Any]]:
    ev = _fact_evidence(fact)
    if ev:
        return [dict(item) for item in ev]
    quote = str(fact.get("source_quote") or "").strip()
    return [{"source_quote": quote}] if quote else []


def _ev_chapter_order(ev: list[dict[str, Any]]) -> int | None:
    for item in ev or []:
        if isinstance(item, dict):
            co = item.get("chapter_order")
            if isinstance(co, (int, float)) and co:
                return int(co)
    return None


def _conflict_polarity(text: str) -> int:
    if not text:
        return 0
    low = text.lower()
    for marker in _NEG_POLARITY_MARKERS:
        if marker in text or marker in low:
            return -1
    for marker in _POS_POLARITY_MARKERS:
        if marker in text or marker in low:
            return 1
    return 0


def _first_temporal_number(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:岁|年|月|日)", text or "")
    if match:
        return int(match.group(1))
    return None


def _new_conflict_candidate(
    conflict_type: str,
    title: str,
    entities: list[str],
    earlier: dict[str, Any],
    later: dict[str, Any],
    severity: str = "medium",
) -> dict[str, Any]:
    return {
        "type": conflict_type,
        "title": title,
        "severity": severity,
        "entities": entities,
        "earlier_evidence": _evidence_or_quote(earlier),
        "later_evidence": _evidence_or_quote(later),
        "possible_explanation": "",
        "explanation_evidence": [],
        "model_judgment": "",
        "confidence": "low",
    }


def _detect_character_attribute_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fact in facts:
        if str(fact.get("fact_type") or "") != "character_profile":
            continue
        content = str(fact.get("content") or "")
        if " · " not in content or ": " not in content:
            continue
        left, value = content.split(": ", 1)
        if " · " not in left:
            continue
        name, label = left.split(" · ", 1)
        groups.setdefault((name.strip(), label.strip()), []).append({"value": value.strip(), "fact": fact})
    out: list[dict[str, Any]] = []
    for (name, label), items in groups.items():
        distinct = {it["value"] for it in items if it["value"] and it["value"] != _IGNORE_ATTRIBUTE_VALUE}
        if len(distinct) < 2:
            continue
        ordered = sorted(items, key=lambda it: _conflict_order_key(it["fact"]))
        earlier, later = ordered[0]["fact"], ordered[-1]["fact"]
        out.append(_new_conflict_candidate("character_profile", f"{name} · {label} 前后不一致", [name], earlier, later))
    return out


def _detect_world_setting_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if str(fact.get("fact_type") or "") not in ("world_rule", "setting_fact"):
            continue
        content = str(fact.get("content") or "")
        name = content.split(": ", 1)[0].strip()
        if not name:
            ents = _fact_entities(fact)
            name = ents[0] if ents else ""
        if not name:
            continue
        groups.setdefault(name, []).append(fact)
    out: list[dict[str, Any]] = []
    for name, items in groups.items():
        if len(items) < 2:
            continue
        scored = [(it, _conflict_polarity(str(it.get("content") or "") + " " + str(it.get("source_quote") or ""))) for it in items]
        negs = [s for s in scored if s[1] == -1]
        poss = [s for s in scored if s[1] == 1]
        if not negs or not poss:
            continue
        earlier, later = sorted([negs[0][0], poss[0][0]], key=_conflict_order_key)
        entities = list({name, *(_fact_entities(earlier) + _fact_entities(later))})[:4]
        out.append(_new_conflict_candidate("world_rule", f"设定规则冲突：{name}", entities, earlier, later))
    return out


def _detect_timeline_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if str(fact.get("fact_type") or "") != "event":
            continue
        ents = _fact_entities(fact)
        if not ents:
            continue
        co = _fact_chapter_order(fact)
        if co is None:
            continue
        extra = fact.get("extra") if isinstance(fact.get("extra"), dict) else {}
        temp = _first_temporal_number(str(extra.get("time_context") or "") + " " + str(fact.get("content") or ""))
        if temp is None:
            temp = _first_temporal_number(" ".join(str((it or {}).get("source_quote") or "") for it in _fact_evidence(fact)))
        if temp is None:
            continue
        for ent in ents:
            groups.setdefault(ent, []).append({"co": co, "temp": temp, "fact": fact})
    out: list[dict[str, Any]] = []
    for ent, items in groups.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda e: e["co"])
        for idx in range(len(items) - 1):
            a, b = items[idx], items[idx + 1]
            if b["co"] > a["co"] and b["temp"] < a["temp"]:
                out.append(_new_conflict_candidate("timeline", f"时间线冲突：{ent} 时间先后矛盾", [ent], a["fact"], b["fact"]))
                break
    return out


_RELATIONSHIP_CONTENT_PATTERN = re.compile(r"^(.+?)\s*-\[(.+?)\]->\s*(.+?)(?::\s.*)?$")


def _relationship_fact_type(fact: dict[str, Any]) -> str:
    extra = fact.get("extra")
    if isinstance(extra, dict):
        extra_type = str(extra.get("relation_type") or "").strip().lower()
        if extra_type:
            return extra_type
    match = _RELATIONSHIP_CONTENT_PATTERN.match(str(fact.get("content") or ""))
    if match:
        return match.group(2).strip().lower()
    return ""


def _detect_relationship_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[frozenset, list[dict[str, Any]]] = {}
    for fact in facts:
        if str(fact.get("fact_type") or "") != "character_relationship":
            continue
        entities = _fact_entities(fact)
        if len(entities) >= 2:
            frm, to = entities[0], entities[1]
        else:
            match = _RELATIONSHIP_CONTENT_PATTERN.match(str(fact.get("content") or ""))
            if not match:
                continue
            frm = match.group(1).strip()
            to = match.group(3).strip()
        rel = _relationship_fact_type(fact)
        if not frm or not to or not rel:
            continue
        groups.setdefault(frozenset((frm, to)), []).append({"rel": rel, "fact": fact, "pair": (frm, to)})
    out: list[dict[str, Any]] = []
    for items in groups.values():
        if len(items) < 2:
            continue
        rels = {it["rel"] for it in items}
        opposed = any((r1, r2) in _REL_OPPOSITES or (r2, r1) in _REL_OPPOSITES for r1 in rels for r2 in rels if r1 != r2)
        if not opposed:
            continue
        items.sort(key=lambda it: _conflict_order_key(it["fact"]))
        earlier, later = items[0], items[-1]
        pair = earlier["pair"]
        out.append(_new_conflict_candidate("relationship", f"关系变化冲突：{pair[0]} 与 {pair[1]} 关系前后不一致", [pair[0], pair[1]], earlier["fact"], later["fact"]))
    return out


def _detect_item_ability_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same item/ability reported with different quantities (PRD: item & ability conflicts)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if str(fact.get("fact_type") or "") not in ("setting_fact", "world_rule"):
            continue
        content = str(fact.get("content") or "")
        name = content.split(": ", 1)[0].strip()
        if not name:
            ents = _fact_entities(fact)
            name = ents[0] if ents else ""
        if not name:
            continue
        joined = content + " " + " ".join(str((it or {}).get("source_quote") or "") for it in _fact_evidence(fact))
        match = re.search(r"\d+", joined)
        if match is None:
            continue
        groups.setdefault(name, []).append({"qty": int(match.group(0)), "fact": fact})
    out: list[dict[str, Any]] = []
    for name, items in groups.items():
        if len(items) < 2:
            continue
        if len({it["qty"] for it in items}) < 2:
            continue
        ordered = sorted(items, key=lambda it: _conflict_order_key(it["fact"]))
        earlier, later = ordered[0]["fact"], ordered[-1]["fact"]
        out.append(_new_conflict_candidate("item_ability", f"物品/能力冲突：{name}", [name], earlier, later))
    return out


def _detect_plot_logic_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unexplained reversals: death contradicted by a later revive (PRD: plot logic)."""
    death_markers = ("dead", "died", "killed", "slain", "死亡", "去世", "陨落", "身死")
    revive_markers = ("revived", "resurrected", "returned", "复活", "重生", "归来", "苏醒")
    groups: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if str(fact.get("fact_type") or "") != "event":
            continue
        ents = _fact_entities(fact)
        if not ents:
            continue
        joined = str(fact.get("content") or "") + " " + " ".join(str((it or {}).get("source_quote") or "") for it in _fact_evidence(fact))
        low = joined.lower()
        is_death = any(m in joined or m in low for m in death_markers)
        is_revive = any(m in joined or m in low for m in revive_markers)
        if not (is_death or is_revive):
            continue
        mode = "death" if is_death else "revive"
        for ent in ents:
            groups.setdefault(ent, []).append({"mode": mode, "fact": fact})
    out: list[dict[str, Any]] = []
    for ent, items in groups.items():
        deaths = [it for it in items if it["mode"] == "death"]
        revives = [it for it in items if it["mode"] == "revive"]
        if not deaths or not revives:
            continue
        death_fact = sorted(deaths, key=lambda it: _conflict_order_key(it["fact"]))[0]["fact"]
        revive_fact = sorted(revives, key=lambda it: _conflict_order_key(it["fact"]))[0]["fact"]
        earlier, later = sorted([death_fact, revive_fact], key=_conflict_order_key)
        out.append(_new_conflict_candidate("plot_logic", f"剧情逻辑冲突：{ent}", [ent], earlier, later))
    return out


def _find_conflict_explanation(conn, novel_id: int, candidate: dict[str, Any], all_facts: list[dict[str, Any]]) -> None:
    """PRD workflow step 4: look for possible explanations in nearby and later chapters."""
    ents = set(candidate.get("entities") or [])
    earlier_order = _ev_chapter_order(candidate.get("earlier_evidence") or [])
    later_order = _ev_chapter_order(candidate.get("later_evidence") or [])
    lo = earlier_order or 0
    hi = (later_order or 0) + 3
    own_quotes = set()
    for key in ("earlier_evidence", "later_evidence"):
        for item in candidate.get(key) or []:
            q = str((item or {}).get("source_quote") or "")
            if q:
                own_quotes.add(q)
    collected: list[dict[str, Any]] = []
    for fact in all_facts:
        if not ents:
            break
        if not (ents & set(_fact_entities(fact))):
            continue
        co = _fact_chapter_order(fact)
        if co is None or co < lo or co > hi:
            continue
        for item in _fact_evidence(fact):
            q = str((item or {}).get("source_quote") or "")
            if not q or q in own_quotes:
                continue
            collected.append({"chapter_title": str((item or {}).get("chapter_title") or ""), "source_quote": q})
            if len(collected) >= 4:
                break
        if len(collected) >= 4:
            break
    # PRD 6.2 level-2: also search nearby/later chapter original text for explanation evidence.
    if len(collected) < 4:
        existing_quotes = set(own_quotes)
        for item in collected:
            q = str((item or {}).get("source_quote") or "")
            if q:
                existing_quotes.add(q)
        text_items = _search_chapter_text_for_explanation(conn, novel_id, sorted(ents), lo or 0, hi, limit=4 - len(collected))
        for item in text_items:
            q = str(item.get("source_quote") or "")
            if not q or q in existing_quotes:
                continue
            existing_quotes.add(q)
            collected.append(item)
            if len(collected) >= 4:
                break
    if collected:
        explanation = "在附近或后续章节中找到关于 " + "、".join(sorted(ents)) + " 的描述，可能解释该差异（以下为相关证据，需人工复核）。"
    else:
        explanation = ""
    candidate["explanation_evidence"] = collected
    candidate["possible_explanation"] = explanation


def _local_conflict_detection(conn, novel_id: int, facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic candidate contradictions over persisted facts.

    Covers character attribute, world rule/setting fact, event timeline,
    relationship-change, item/ability and plot-logic conflicts. Candidates are conservative and remain
    pending_review until a human confirms them. Possible explanations are
    looked up from related facts around and after the evidence window.
    """
    conflicts: list[dict[str, Any]] = []
    conflicts += _detect_character_attribute_conflicts(facts)
    conflicts += _detect_world_setting_conflicts(facts)
    conflicts += _detect_timeline_conflicts(facts)
    conflicts += _detect_relationship_conflicts(facts)
    conflicts += _detect_item_ability_conflicts(facts)
    conflicts += _detect_plot_logic_conflicts(facts)
    for candidate in conflicts:
        _find_conflict_explanation(conn, novel_id, candidate, facts)
    return {
        "status": "local_fallback",
        "task_type": "conflict_detection",
        "conflicts": conflicts,
        "evidence_required": True,
    }


def _persist_conflict_facts(
    conn, novel_id: int, result: dict[str, Any], job_id: int, seen_keys: set[str] | None = None
) -> int:
    conflicts = result.get("conflicts")
    if not isinstance(conflicts, list):
        return 0
    if seen_keys is None:
        seen_keys = set()
    superseded = False
    persisted = 0
    for item in conflicts:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "设定冲突").strip()
        if not title:
            continue
        dedup_key = title.strip().lower()
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        if not superseded:
            supersede_previous_run_facts(
                conn, novel_id=novel_id, fact_type="setting_conflict", current_run_id=job_id
            )
            superseded = True
        severity = str(item.get("severity") or "low").strip().lower()
        if severity not in _CONFLICT_SEVERITIES:
            severity = "low"
        entities_raw = item.get("entities") if isinstance(item.get("entities"), list) else []
        entities = [str(e).strip() for e in entities_raw if str(e).strip()]
        earlier_ev = _norm_evidence(item.get("earlier_evidence"))
        later_ev = _norm_evidence(item.get("later_evidence"))
        first_quote = ""
        if earlier_ev:
            first_quote = str(earlier_ev[0].get("source_quote") or "")
        explanation_ev = _norm_evidence(item.get("explanation_evidence"))
        conflict_type = str(item.get("type") or "").strip()
        confidence_value = str(item.get("confidence") or "low")
        extra = {
            "type": conflict_type,
            "severity": severity,
            "title": title,
            "entities": entities,
            "earlier_evidence": earlier_ev,
            "later_evidence": later_ev,
            "possible_explanation": str(item.get("possible_explanation") or ""),
            "explanation_evidence": explanation_ev,
            "model_judgment": str(item.get("model_judgment") or ""),
            "confidence": confidence_value,
        }
        upsert_extracted_fact(
            conn,
            novel_id=novel_id,
            fact_type="setting_conflict",
            content=title,
            entities=entities,
            source_quote=first_quote,
            confidence=str(item.get("confidence") or "low"),
            status="pending_review",
            model_run_id=job_id,
            evidence=earlier_ev + later_ev + explanation_ev,
            extra=extra,
        )
        persisted += 1
    return persisted


async def _run_conflict_detection_job(conn, novel_id: int, model: str, force_refresh: bool, job_id: int) -> dict[str, Any]:
    rows = list_chapters(conn, novel_id)
    if not rows:
        raise ValueError("novel not found or has no chapters")
    facts: list[dict[str, Any]] = []
    for fact_type in ("character_profile", "world_rule", "setting_fact", "event", "character_relationship"):
        facts.extend(list_extracted_facts(conn, novel_id, fact_type=fact_type))
    local = _local_conflict_detection(conn, novel_id, facts)
    payload = _conflict_judgment_payload(local.get("conflicts", []))
    result = await _cached_model_task(
        conn,
        "conflict_detection",
        payload,
        model,
        force_refresh,
        job_id=job_id,
        fallback_output=local,
    )
    invalid_reason = _invalid_output_reason(result, "conflict_detection")
    if invalid_reason is not None:
        error = f"conflict_detection failed: {invalid_reason}"
        update_analysis_job(conn, job_id, status="failed", progress=100, error=error)
        _write_run_summary(
            conn,
            model=model,
            task_type="conflict_detection",
            novel_id=novel_id,
            job_id=job_id,
            summary=_with_cache_metadata(
                {"status": "failed", "task_type": "conflict_detection", "model_error": error},
                source="mixed",
                provider_call_attempted=True,
                provider_call_succeeded=False,
                model_error=error,
            ),
            failed_batches=[{"error": error}],
        )
        return result | {"persisted_facts": 0}
    conflicts = result.get("conflicts")
    if not isinstance(conflicts, list):
        conflicts = local.get("conflicts", [])
    persisted = _persist_conflict_facts(conn, novel_id, {"conflicts": conflicts}, job_id)
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    source_value = _base_source(
        str(provenance.get("source") or result.get("source") or "remote_model")
    )
    model_error = str(provenance.get("model_error") or result.get("model_error") or "").strip()
    _write_run_summary(
        conn,
        model=model,
        task_type="conflict_detection",
        novel_id=novel_id,
        job_id=job_id,
        summary=_with_cache_metadata(
            {"status": str(result.get("status") or "ok"), "task_type": "conflict_detection", "conflicts": conflicts},
            source=source_value,
            provider_call_attempted=bool(provenance.get("provider_call_attempted")),
            provider_call_succeeded=bool(provenance.get("provider_call_succeeded")),
            model_error=model_error or None,
        ),
        failed_batches=[],
    )
    update_analysis_job(conn, job_id, status="completed", progress=100)
    return result | {"persisted_facts": persisted}