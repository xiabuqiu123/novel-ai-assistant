"""Task-domain module: relationships (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import asyncio
import re
from ..cache import cache_key
from ..cache import input_hash
from ..database import get_cache
from ..database import get_chapter
from ..database import list_chapters
from ..database import list_extracted_facts
from ..database import put_cache
from ..database import supersede_previous_run_facts
from ..database import update_analysis_job
from ..database import upsert_extracted_fact
from ..provenance import with_model_provenance
from .cache import _cache_metadata
from .cache import _cached_source
from .cache import _call_extraction_batch_model
from .cache import _cacheable_output
from .cache import _extraction_cache_probe
from .cache import _invalid_output_reason
from .cache import _task_cache_key
from .cache import _with_cache_metadata
from .characters import _CONFIDENCE_RANK
from .characters import _known_character_names
from .characters import _local_character_extraction
from .common import _PARTIAL_HINT
from .common import _as_int
from .common import _chapter_lookup_for_novel
from .common import _norm_evidence
from .common import _resolve_character_chapter_id
from .orchestration import _extraction_batch_size
from .orchestration import _extraction_concurrency
from typing import Any
from .. import secrets
from .. import database


RELATIONSHIP_EXTRACTION_BATCH_SIZE = 10


def _relationship_batch_payload(
    batch_rows: list[dict[str, Any]],
    full_rows: list[dict[str, Any]],
    names: list[str],
) -> str:
    first = batch_rows[0]
    last = batch_rows[-1]
    marker = f"batch_chapter_range:{first['chapter_order']}-{last['chapter_order']}"
    known = ", ".join(names) if names else "(none extracted yet)"
    excerpts = "\n\n".join(
        f"chapter:{row['title']}\nsource_excerpt:{str(row['content'])[:2000]}" for row in full_rows
    )
    return (
        f"known_characters: {known}\n\n"
        + _relationship_extraction_schema()
        + "\n\n"
        + marker
        + "\n\n"
        + excerpts
    )


def _relationship_combined_payload(chapter_rows: list[dict[str, Any]]) -> str:
    return (
        f"relationship_extraction_combined chapters:{len(chapter_rows)} "
        f"first_chapter_id:{chapter_rows[0]['id']} last_chapter_id:{chapter_rows[-1]['id']}"
    )


_RELATIONSHIP_ATTITUDES = ("hostile", "cold", "neutral", "friendly", "close")


_ATTITUDE_SYNONYMS = {
    "hostile": ("enemy", "antagonistic", "antagonism", "hate", "hatred", "opposed", "敌对", "仇视", "敌对关系"),
    "cold": ("distant", "indifferent", "estranged", "冷淡", "疏远", "冷漠"),
    "neutral": ("中立", "普通"),
    "friendly": ("friend", "ally", "友善", "友好", "盟友"),
    "close": ("intimate", "亲昵", "亲近", "亲密"),
}


def _normalize_relationship_attitude(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in _RELATIONSHIP_ATTITUDES:
        return text
    for attitude, synonyms in _ATTITUDE_SYNONYMS.items():
        if text in synonyms:
            return attitude
    return text


def _sort_relationship_evolution(evolution: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _order_key(item: dict[str, Any]) -> int:
        value = item.get("chapter_order")
        if isinstance(value, (int, float)) and value:
            return int(value)
        return 10 ** 9

    return sorted(evolution, key=_order_key)


def _normalize_relationship_evolution(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("relation_label") or item.get("label") or "").strip()
        event = str(item.get("event") or "").strip()
        if not label and not event:
            continue
        items.append(
            {
                "chapter_order": _as_int(item.get("chapter_order")),
                "relation_label": label,
                "event": event,
            }
        )
    return _sort_relationship_evolution(items)


def _merge_evolution_lists(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(first)
    for item in second:
        if item not in merged:
            merged.append(item)
    return merged


def _relationship_alias_map(conn, novel_id: int) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for fact in list_extracted_facts(conn, novel_id, fact_type="character_profile"):
        entities = fact.get("entities")
        if not isinstance(entities, list) or not entities:
            continue
        canonical = str(entities[0]).strip()
        if not canonical:
            continue
        for name in entities:
            key = str(name).strip()
            if key:
                alias_map[key] = canonical
    return alias_map


def _canonical_relationship_name(name: str, alias_map: dict[str, str]) -> str:
    cleaned = name.strip()
    return alias_map.get(cleaned, cleaned)


def _relationship_pair_key(from_name: str, to_name: str) -> tuple[str, str]:
    left = from_name.strip().lower()
    right = to_name.strip().lower()
    return (left, right) if left <= right else (right, left)


def _merge_relationship_entry(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    for field in ("evidence", "source_chapters"):
        merged = existing.get(field) if isinstance(existing.get(field), list) else []
        incoming_items = incoming.get(field) if isinstance(incoming.get(field), list) else []
        for item in incoming_items:
            if item not in merged:
                merged.append(item)
        existing[field] = merged
    for field in ("relation_label", "attitude", "relation_type", "description"):
        value = str(incoming.get(field) or "").strip()
        if value:
            existing[field] = value
    existing["evolution"] = _sort_relationship_evolution(
        _merge_evolution_lists(
            existing.get("evolution") if isinstance(existing.get("evolution"), list) else [],
            incoming.get("evolution") if isinstance(incoming.get("evolution"), list) else [],
        )
    )
    if _CONFIDENCE_RANK.get(str(incoming.get("confidence")), 0) > _CONFIDENCE_RANK.get(
        str(existing.get("confidence")), 0
    ):
        existing["confidence"] = incoming["confidence"]


def _relationship_extraction_schema() -> str:
    return (
        """Output a JSON object with a "relationships" list. Each relationship must be an object with:
"from_character" (string, character name exactly as it appears in known_characters or the text),
"to_character" (string, character name exactly as it appears in known_characters or the text),
"relation_type" (string: one of "family", "friend", "ally", "enemy", "romantic", "mentor", "subordinate", "rival", "acquaintance", "other"),
"relation_label" (string, short human-readable label such as 师徒/盟友/仇敌),
"attitude" (string: one of "hostile", "cold", "neutral", "friendly", "close"),
"evolution" (list of objects, each with chapter_order (number), relation_label (string) and event (string), covering the relationship states over time),
"description" (string, one-sentence description of the relationship from provided evidence only),
"source_chapters" (list of chapter numbers/ids where this relationship is shown),
"evidence" (list of objects, each with chapter_title and source_quote),
"confidence" (string: one of "high", "medium", "low"),
"status" ("pending_review").

IMPORTANT RULES:
- Only include relationships supported by the provided excerpts.
- Do NOT invent relationships that are not stated or clearly shown in the excerpts.
- Use character names consistently; prefer names from known_characters when they refer to the same person.
- Every relationship must have at least one source_quote from the provided excerpts.
- If a relationship changes over time, output the full evolution with chapter_order entries; otherwise leave evolution as an empty list.
"""
    )


async def _run_relationship_extraction_job(
    conn,
    novel_id: int,
    model: str,
    force_refresh: bool,
    job_id: int,
) -> dict[str, Any]:
    rows = list_chapters(conn, novel_id)
    if not rows:
        raise ValueError("novel not found or has no chapters")
    alias_map = _relationship_alias_map(conn, novel_id)
    names = _known_character_names(conn, novel_id, [get_chapter(conn, int(row["id"])) for row in rows])
    combined_hash = input_hash("relationship_extraction_combined", _relationship_combined_payload(rows))
    combined_key = cache_key(model=model, task_type="relationship_extraction_combined", input_hash_value=combined_hash)
    if not force_refresh:
        cached = get_cache(conn, combined_key)
        if (
            cached is not None
            and cached.get("status") == "ok"
            and _invalid_output_reason(cached, "relationship_extraction") is None
        ):
            persisted = _persist_relationship_facts(conn, novel_id, cached, job_id)
            update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=combined_key)
            cache_meta = _cache_metadata(cached)
            return with_model_provenance(
                cached,
                task_type="relationship_extraction",
                model_used=model,
                cache_hit=True,
                input_hash_value=combined_hash,
                cache_key_value=combined_key,
                job_id=job_id,
                source=_cached_source(cache_meta, cached),
                provider_call_attempted=bool(cache_meta.get("provider_call_attempted")),
                provider_call_succeeded=bool(cache_meta.get("provider_call_succeeded")),
            ) | {"persisted_facts": persisted}

    batch_size = _extraction_batch_size(conn, "relationship_extraction_batch_size", RELATIONSHIP_EXTRACTION_BATCH_SIZE)
    batches = [
        rows[start:start + batch_size]
        for start in range(0, len(rows), batch_size)
    ]
    concurrency = _extraction_concurrency(conn, "relationship_extraction_concurrency")
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    attempted = False
    succeeded = 0
    fallback_count = 0
    all_cached = True
    model_errors: list[str] = []
    update_analysis_job(conn, job_id, status="running", progress=5)

    # Phase 1 (serial): prepare batches and probe their caches. The sqlite
    # connection never crosses threads; only model calls run concurrently.
    api_key = database.get_setting(conn, "api_key")
    api_key = secrets.decrypt_secret(api_key)
    base_url = database.get_setting(conn, "base_url")
    prepared: list[dict[str, Any]] = []
    for batch_rows in batches:
        full_rows = [get_chapter(conn, int(row["id"])) for row in batch_rows]
        payload = _relationship_batch_payload(batch_rows, full_rows, names)
        prepared.append(
            {
                "batch_rows": batch_rows,
                "full_rows": full_rows,
                "payload": payload,
                "fallback": _local_relationship_extraction(full_rows, names),
                "cached": _extraction_cache_probe(conn, "relationship_extraction", payload, model, force_refresh),
            }
        )

    # Phase 2 (concurrent): model calls only, no sqlite access.
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded_relationship_call(spec: dict[str, Any]) -> None:
        if spec["cached"] is not None:
            return
        async with semaphore:
            spec["output"] = await _call_extraction_batch_model(
                "relationship_extraction",
                spec["payload"],
                model,
                api_key,
                base_url,
                spec["fallback"],
            )

    await asyncio.gather(*(_bounded_relationship_call(spec) for spec in prepared))

    # Phase 3 (serial): validate, cache fresh outputs, merge, progress.
    for index, spec in enumerate(prepared):
        result = spec["cached"] if spec["cached"] is not None else spec["output"]
        invalid_reason = _invalid_output_reason(result, "relationship_extraction")
        if invalid_reason is not None:
            error = f"relationship_extraction batch {index + 1}/{len(batches)} failed: {invalid_reason}"
            update_analysis_job(conn, job_id, status="failed", progress=100, error=error)
            return with_model_provenance(
                result,
                task_type="relationship_extraction",
                model_used=model,
                cache_hit=False,
                input_hash_value=input_hash("relationship_extraction", spec["payload"]),
                cache_key_value=_task_cache_key("relationship_extraction", spec["payload"], model),
                job_id=job_id,
                model_error=error,
            ) | {"persisted_facts": 0}
        if spec["cached"] is None and invalid_reason is None:
            batch_provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
            if batch_provenance.get("provider_call_succeeded") is True or batch_provenance.get(
                "provider_call_attempted"
            ) is not True:
                put_cache(
                    conn,
                    key=_task_cache_key("relationship_extraction", spec["payload"], model),
                    model=model,
                    task_type="relationship_extraction",
                    input_hash_value=input_hash("relationship_extraction", spec["payload"]),
                    output=_cacheable_output(result),
                )
        provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
        batch_error = str(provenance.get("model_error") or result.get("model_error") or "").strip()
        if batch_error:
            model_errors.append(batch_error)
        if provenance.get("provider_call_attempted"):
            attempted = True
            if provenance.get("provider_call_succeeded"):
                succeeded += 1
        if result.get("source") == "local_fallback" or result.get("status") == "local_fallback":
            fallback_count += 1
        if provenance.get("cache_hit") is not True:
            all_cached = False
        relationships = result.get("relationships")
        for relationship in relationships if isinstance(relationships, list) else []:
            if not isinstance(relationship, dict):
                continue
            from_name = _canonical_relationship_name(str(relationship.get("from_character") or "").strip(), alias_map)
            to_name = _canonical_relationship_name(str(relationship.get("to_character") or "").strip(), alias_map)
            if not from_name or not to_name or from_name == to_name:
                continue
            normalized = dict(relationship)
            normalized["from_character"] = from_name
            normalized["to_character"] = to_name
            normalized["attitude"] = _normalize_relationship_attitude(relationship.get("attitude"))
            normalized["evolution"] = _normalize_relationship_evolution(relationship.get("evolution"))
            pair_key = _relationship_pair_key(from_name, to_name)
            if pair_key in merged:
                _merge_relationship_entry(merged[pair_key], normalized)
            else:
                merged[pair_key] = normalized
        update_analysis_job(conn, job_id, status="running", progress=5 + 90 * (index + 1) // len(batches))

    relationships = list(merged.values())
    if attempted and fallback_count and fallback_count / len(batches) > 0.5:
        error = "模型超时/限流，请重试或调小批次"
        update_analysis_job(conn, job_id, status="failed", progress=100, error=error)
        return with_model_provenance(
            {
                "status": "failed",
                "task_type": "relationship_extraction",
                "relationships": relationships,
                "batches": len(batches),
                "model_error": error,
            },
            task_type="relationship_extraction",
            model_used=model,
            cache_hit=False,
            input_hash_value=combined_hash,
            cache_key_value=combined_key,
            job_id=job_id,
            source="mixed",
            model_error=error,
            provider_call_attempted=True,
            provider_call_succeeded=False,
        ) | {"persisted_facts": 0}
    if fallback_count == 0:
        status = "ok"
    elif not attempted:
        status = "local_fallback"
    else:
        status = "partial"
    provider_ok = attempted and fallback_count == 0
    # 无失败批次即视为模型来源（本轮调用或缓存命中），避免全缓存重跑误标 local_fallback。
    source = "remote_model" if fallback_count == 0 else ("local_fallback" if not attempted else "mixed")
    combined = _with_cache_metadata(
        {
            "status": status,
            "task_type": "relationship_extraction",
            "relationships": relationships,
            "batches": len(batches),
            "evidence": [
                evidence
                for relationship in relationships
                for evidence in (relationship.get("evidence") if isinstance(relationship.get("evidence"), list) else [])[:1]
            ],
            "evidence_required": True,
        },
        source=source,
        provider_call_attempted=attempted,
        provider_call_succeeded=provider_ok,
        model_error=model_errors[-1] if model_errors else None,
    )
    put_cache(
        conn,
        key=combined_key,
        model=model,
        task_type="relationship_extraction_combined",
        input_hash_value=combined_hash,
        output=combined,
    )
    persisted = _persist_relationship_facts(conn, novel_id, combined, job_id)
    update_analysis_job(
        conn,
        job_id,
        status="completed",
        progress=100,
        result_cache_key=combined_key,
        error=(model_errors[-1] if model_errors else _PARTIAL_HINT) if status == "partial" else None,
    )
    return with_model_provenance(
        combined,
        task_type="relationship_extraction",
        model_used=model,
        cache_hit=all_cached,
        input_hash_value=combined_hash,
        cache_key_value=combined_key,
        job_id=job_id,
        source=source,
        provider_call_attempted=attempted,
        provider_call_succeeded=attempted and succeeded == len(batches),
    ) | {"persisted_facts": persisted}


def _persist_relationship_facts(
    conn, novel_id: int, result: dict[str, Any], job_id: int,
    seen_keys: set[tuple[str, str]] | None = None,
) -> int:
    relationships = result.get("relationships")
    if not isinstance(relationships, list):
        return 0
    chapter_lookup = _chapter_lookup_for_novel(conn, novel_id)
    if seen_keys is None:
        seen_keys = set()
    superseded = False
    persisted = 0
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        from_name = str(relationship.get("from_character") or "").strip()
        to_name = str(relationship.get("to_character") or "").strip()
        if not from_name or not to_name or from_name == to_name:
            continue
        pair_key = _relationship_pair_key(from_name, to_name)
        if pair_key in seen_keys:
            continue
        seen_keys.add(pair_key)
        if not superseded:
            supersede_previous_run_facts(
                conn, novel_id=novel_id, fact_type="character_relationship", current_run_id=job_id
            )
            superseded = True
        relation_type = str(relationship.get("relation_type") or "other").strip() or "other"
        relation_label = str(relationship.get("relation_label") or "").strip()
        attitude = _normalize_relationship_attitude(relationship.get("attitude"))
        evolution = _normalize_relationship_evolution(relationship.get("evolution"))
        description = str(relationship.get("description") or "").strip()
        display_type = relation_label or relation_type
        content = f"{from_name} -[{display_type}]-> {to_name}"
        if description:
            content = f"{content}: {description}"
        evidence_items = _norm_evidence(relationship.get("evidence"))
        evidence = evidence_items[0] if evidence_items else {}
        chapter_id = _resolve_character_chapter_id(relationship, evidence, chapter_lookup)
        extra = {
            "relation_type": relation_type,
            "relation_label": relation_label,
            "attitude": attitude,
            "evolution": evolution,
        }
        upsert_extracted_fact(
            conn,
            novel_id=novel_id,
            fact_type="character_relationship",
            content=content,
            entities=[from_name, to_name],
            chapter_id=chapter_id,
            source_quote=str(evidence.get("source_quote") or ""),
            confidence=str(relationship.get("confidence") or "low"),
            status="pending_review",
            model_run_id=job_id,
            evidence=evidence_items,
            extra=extra,
        )
        persisted += 1
    return persisted


def _local_relationship_extraction(
    chapter_rows: list[dict[str, Any]],
    character_names: list[str],
    limit: int = 60,
) -> dict[str, Any]:
    names = list(character_names)
    if not names:
        fallback = _local_character_extraction(chapter_rows)
        names = [str(c.get("name") or "").strip() for c in fallback.get("characters", [])]
        names = [name for name in names if name]
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for chapter_row in chapter_rows:
        text = chapter_row["content"]
        for paragraph in re.split(r"\n+", text):
            present = [name for name in names if name in paragraph]
            if len(present) < 2:
                continue
            for index, first in enumerate(present):
                for second in present[index + 1:]:
                    key = (first, second)
                    if key not in pairs:
                        pairs[key] = {
                            "from_character": first,
                            "to_character": second,
                            "relation_type": "co_occurrence",
                            "description": "Names appear together in the same paragraph (heuristic co-occurrence).",
                            "source_chapters": [],
                            "evidence": [],
                            "confidence": "low",
                            "status": "pending_review",
                        }
                    item = pairs[key]
                    if chapter_row["chapter_order"] not in item["source_chapters"]:
                        item["source_chapters"].append(chapter_row["chapter_order"])
                    if len(item["evidence"]) < 2:
                        item["evidence"].append(
                            {
                                "chapter_id": chapter_row["id"],
                                "chapter_order": chapter_row["chapter_order"],
                                "chapter_title": chapter_row["title"],
                                "source_quote": paragraph.strip()[:500],
                            }
                        )
            if len(pairs) >= limit:
                break
        if len(pairs) >= limit:
            break
    relationships = list(pairs.values())[:limit]
    return {
        "status": "local_fallback",
        "task_type": "relationship_extraction",
        "relationships": relationships,
        "evidence": [evidence for rel in relationships for evidence in rel["evidence"][:1]],
        "suggestions": [
            "Set an API key for model-generated relationship extraction.",
            "Fallback relationships are co-occurrence hints, not confirmed relations.",
        ],
        "evidence_required": True,
    }


def _parse_relationship_content(content: str) -> dict[str, str]:
    text = content.strip()
    relation = re.search(r"-\[([^\]]+)\]->", text)
    relation_type = relation.group(1).strip() if relation else "related"
    description = ""
    if relation is not None:
        remainder = text[relation.end():].strip()
        if ": " in remainder:
            description = remainder.split(": ", 1)[1].strip()
    return {"relation_type": relation_type or "related", "description": description}