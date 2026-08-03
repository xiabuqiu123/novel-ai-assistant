"""Task-domain module: characters (moved verbatim from main.py by main-split refactor)."""

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
from .common import _PARTIAL_HINT
from .common import _chapter_lookup_for_novel
from .common import _norm_evidence
from .common import _resolve_character_chapter_id
from .common import _source_quote
from .orchestration import _extraction_batch_size
from .orchestration import _extraction_concurrency
from typing import Any
from .. import secrets
from .. import database


CHARACTER_EXTRACTION_BATCH_SIZE = 10


def _character_extraction_batch_payload(
    batch_rows: list[dict[str, Any]],
    full_rows: list[dict[str, Any]],
    known_names: list[str] | None = None,
) -> str:
    first = batch_rows[0]
    last = batch_rows[-1]
    marker = (
        f"batch_chapter_range:{first['chapter_order']}-{last['chapter_order']} "
        f"batch_chapter_ids:{first['id']}-{last['id']}"
    )
    known = ", ".join(known_names) if known_names else "(none extracted yet)"
    excerpts = "\n\n".join(
        f"chapter:{row['title']}\nsource_excerpt:{str(row['content'])[:2000]}" for row in full_rows
    )
    return (
        _character_extraction_schema()
        + "\n\n"
        + f"known_characters: {known}"
        + "\n\n"
        + marker
        + "\n\n"
        + excerpts
    )


def _character_extraction_combined_payload(chapter_rows: list[dict[str, Any]]) -> str:
    return (
        f"character_extraction_combined chapters:{len(chapter_rows)} "
        f"first_chapter_id:{chapter_rows[0]['id']} last_chapter_id:{chapter_rows[-1]['id']}"
    )


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _merge_character_entry(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    existing_name = str(existing.get("name") or "").strip()
    for field in ("aliases", "evidence", "source_chapters"):
        merged = existing.get(field) if isinstance(existing.get(field), list) else []
        incoming_items = incoming.get(field) if isinstance(incoming.get(field), list) else []
        for item in incoming_items:
            if field == "aliases" and str(item).strip() == existing_name:
                continue
            if item not in merged:
                merged.append(item)
        existing[field] = merged
    incoming_name = str(incoming.get("name") or "").strip()
    if incoming_name and incoming_name != existing_name and incoming_name not in existing.get("aliases", []):
        existing.setdefault("aliases", []).append(incoming_name)
    if not str(existing.get("description") or "").strip() and str(incoming.get("description") or "").strip():
        existing["description"] = incoming["description"]
    if _CONFIDENCE_RANK.get(str(incoming.get("confidence")), 0) > _CONFIDENCE_RANK.get(
        str(existing.get("confidence")), 0
    ):
        existing["confidence"] = incoming["confidence"]
    existing["attributes"] = _merge_character_attributes(
        existing.get("attributes") if isinstance(existing.get("attributes"), list) else [],
        incoming.get("attributes") if isinstance(incoming.get("attributes"), list) else [],
    )


def _character_merge_target(
    name: str, character: dict[str, Any], alias_map: dict[str, str]
) -> str | None:
    """Return the lowercased canonical key an incoming character merges into.

    A direct name hit wins; otherwise any alias hit maps to its canonical.
    Returns None when the character starts a new canonical entry.
    """
    direct = alias_map.get(name.lower())
    if direct is not None:
        return direct
    aliases = character.get("aliases") if isinstance(character.get("aliases"), list) else []
    for alias in aliases:
        alias_key = str(alias).strip().lower()
        if alias_key and alias_key in alias_map:
            return alias_map[alias_key]
    return None


def _register_character_aliases(
    alias_map: dict[str, str], canonical_key: str, character: dict[str, Any]
) -> None:
    names = [str(character.get("name") or "").strip()]
    aliases = character.get("aliases") if isinstance(character.get("aliases"), list) else []
    names.extend(str(alias).strip() for alias in aliases)
    for value in names:
        key = value.lower()
        if key:
            alias_map.setdefault(key, canonical_key)


_IGNORED_ATTRIBUTE_VALUES = ("", "未提及", "无（未提及）")


def _merge_affiliation_values(current: str, incoming: str) -> str:
    def segments(value: str) -> list[str]:
        return [segment.strip() for segment in value.split("；") if segment.strip()]

    merged = segments(current)
    for segment in segments(incoming):
        if segment in merged:
            continue
        if any(segment in existing for existing in merged):
            # A shorter duplicate of an existing timeline entry.
            continue
        extended = [existing for existing in merged if existing in segment]
        if extended:
            # The new segment extends existing timeline entries: replace them.
            merged = [existing for existing in merged if existing not in extended]
            merged.append(segment)
            continue
        merged.append(segment)
    return "；".join(merged)


def _merge_character_attributes(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge attributes by name: later non-empty values win (affiliation is
    concatenated along its timeline), evidence is unioned and deduplicated.
    """
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for attr in existing:
        if not isinstance(attr, dict):
            continue
        key = str(attr.get("attribute") or "").strip()
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = attr
    for attr in incoming:
        if not isinstance(attr, dict):
            continue
        key = str(attr.get("attribute") or "").strip()
        if not key:
            continue
        if key not in by_key:
            order.append(key)
            by_key[key] = dict(attr)
            continue
        current = by_key[key]
        incoming_value = str(attr.get("value") or "").strip()
        if key == "affiliation":
            current_value = str(current.get("value") or "").strip()
            if current_value in _IGNORED_ATTRIBUTE_VALUES:
                if incoming_value and incoming_value not in _IGNORED_ATTRIBUTE_VALUES:
                    current["value"] = incoming_value
            elif incoming_value and incoming_value not in _IGNORED_ATTRIBUTE_VALUES:
                current["value"] = _merge_affiliation_values(current_value, incoming_value)
        elif incoming_value and incoming_value not in _IGNORED_ATTRIBUTE_VALUES:
            current["value"] = incoming_value
        merged_evidence = current.get("evidence") if isinstance(current.get("evidence"), list) else []
        for item in attr.get("evidence") if isinstance(attr.get("evidence"), list) else []:
            if item not in merged_evidence:
                merged_evidence.append(item)
        current["evidence"] = merged_evidence
    return [by_key[key] for key in order]


def _character_duplicate_candidates(characters: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Canonical entries the model linked via aliases but kept separate.

    A.aliases contains B.name (or vice versa) -> they may be the same person;
    surfaced as a hint for the user, never auto-merged.
    """
    candidates: list[dict[str, str]] = []
    for i, a in enumerate(characters):
        if not isinstance(a, dict):
            continue
        name_a = str(a.get("name") or "").strip()
        if not name_a:
            continue
        aliases_a = {
            str(alias).strip()
            for alias in (a.get("aliases") if isinstance(a.get("aliases"), list) else [])
            if str(alias).strip()
        }
        for b in characters[i + 1:]:
            if not isinstance(b, dict):
                continue
            name_b = str(b.get("name") or "").strip()
            if not name_b:
                continue
            aliases_b = {
                str(alias).strip()
                for alias in (b.get("aliases") if isinstance(b.get("aliases"), list) else [])
                if str(alias).strip()
            }
            if name_b in aliases_a or name_a in aliases_b:
                candidates.append(
                    {"name_a": name_a, "name_b": name_b, "reason": "别名交叉（疑似同一人物）"}
                )
    return candidates


async def _run_character_extraction_job(
    conn,
    novel_id: int,
    model: str,
    force_refresh: bool,
    job_id: int,
) -> dict[str, Any]:
    rows = list_chapters(conn, novel_id)
    if not rows:
        raise ValueError("novel not found or has no chapters")
    combined_hash = input_hash("character_extraction_combined", _character_extraction_combined_payload(rows))
    combined_key = cache_key(model=model, task_type="character_extraction_combined", input_hash_value=combined_hash)
    if not force_refresh:
        cached = get_cache(conn, combined_key)
        if (
            cached is not None
            and cached.get("status") == "ok"
            and _invalid_output_reason(cached, "character_extraction") is None
        ):
            persisted = _persist_character_facts(conn, novel_id, cached, job_id)
            update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=combined_key)
            cache_meta = _cache_metadata(cached)
            return with_model_provenance(
                cached,
                task_type="character_extraction",
                model_used=model,
                cache_hit=True,
                input_hash_value=combined_hash,
                cache_key_value=combined_key,
                job_id=job_id,
                source=_cached_source(cache_meta, cached),
                provider_call_attempted=bool(cache_meta.get("provider_call_attempted")),
                provider_call_succeeded=bool(cache_meta.get("provider_call_succeeded")),
            ) | {"persisted_facts": persisted}

    batch_size = _extraction_batch_size(conn, "character_extraction_batch_size", CHARACTER_EXTRACTION_BATCH_SIZE)
    batches = [
        rows[start:start + batch_size]
        for start in range(0, len(rows), batch_size)
    ]
    names = _known_character_names(conn, novel_id, [get_chapter(conn, int(row["id"])) for row in rows], model)
    concurrency = _extraction_concurrency(conn, "character_extraction_concurrency")
    merged: dict[str, dict[str, Any]] = {}
    alias_to_canonical: dict[str, str] = {}
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
        payload = _character_extraction_batch_payload(batch_rows, full_rows, names)
        prepared.append(
            {
                "batch_rows": batch_rows,
                "full_rows": full_rows,
                "payload": payload,
                "fallback": _local_character_extraction(full_rows),
                "cached": _extraction_cache_probe(conn, "character_extraction", payload, model, force_refresh),
            }
        )

    # Phase 2 (concurrent): model calls only, no sqlite access.
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded_character_call(spec: dict[str, Any]) -> None:
        if spec["cached"] is not None:
            return
        async with semaphore:
            spec["output"] = await _call_extraction_batch_model(
                "character_extraction",
                spec["payload"],
                model,
                api_key,
                base_url,
                spec["fallback"],
            )

    await asyncio.gather(*(_bounded_character_call(spec) for spec in prepared))

    # Phase 3 (serial): validate, cache fresh outputs, merge, progress.
    for index, spec in enumerate(prepared):
        result = spec["cached"] if spec["cached"] is not None else spec["output"]
        invalid_reason = _invalid_output_reason(result, "character_extraction")
        if invalid_reason is not None:
            error = f"character_extraction batch {index + 1}/{len(batches)} failed: {invalid_reason}"
            update_analysis_job(conn, job_id, status="failed", progress=100, error=error)
            return with_model_provenance(
                result,
                task_type="character_extraction",
                model_used=model,
                cache_hit=False,
                input_hash_value=input_hash("character_extraction", spec["payload"]),
                cache_key_value=_task_cache_key("character_extraction", spec["payload"], model),
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
                    key=_task_cache_key("character_extraction", spec["payload"], model),
                    model=model,
                    task_type="character_extraction",
                    input_hash_value=input_hash("character_extraction", spec["payload"]),
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
        characters = result.get("characters")
        for character in characters if isinstance(characters, list) else []:
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            if not name:
                continue
            target = _character_merge_target(name, character, alias_to_canonical)
            if target is None:
                merge_key = name.lower()
                merged[merge_key] = character
                alias_to_canonical[merge_key] = merge_key
                _register_character_aliases(alias_to_canonical, merge_key, character)
            else:
                _merge_character_entry(merged[target], character)
                _register_character_aliases(alias_to_canonical, target, character)
        update_analysis_job(conn, job_id, status="running", progress=5 + 90 * (index + 1) // len(batches))

    characters = list(merged.values())
    duplicate_candidates = _character_duplicate_candidates(characters)
    if attempted and fallback_count and fallback_count / len(batches) > 0.5:
        error = "模型超时/限流，请重试或调小批次"
        update_analysis_job(conn, job_id, status="failed", progress=100, error=error)
        return with_model_provenance(
            {
                "status": "failed",
                "task_type": "character_extraction",
                "characters": characters,
                "batches": len(batches),
                "model_error": error,
            },
            task_type="character_extraction",
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
            "task_type": "character_extraction",
            "characters": characters,
            "duplicate_candidates": duplicate_candidates,
            "batches": len(batches),
            "evidence": [
                evidence
                for character in characters
                for evidence in (character.get("evidence") if isinstance(character.get("evidence"), list) else [])[:1]
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
        task_type="character_extraction_combined",
        input_hash_value=combined_hash,
        output=combined,
    )
    persisted = _persist_character_facts(conn, novel_id, combined, job_id)
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
        task_type="character_extraction",
        model_used=model,
        cache_hit=all_cached,
        input_hash_value=combined_hash,
        cache_key_value=combined_key,
        job_id=job_id,
        source=source,
        provider_call_attempted=attempted,
        provider_call_succeeded=attempted and succeeded == len(batches),
    ) | {"persisted_facts": persisted}


def _persist_character_facts(
    conn, novel_id: int, result: dict[str, Any], job_id: int, seen_names: set[str] | None = None
) -> int:
    characters = result.get("characters")
    if not isinstance(characters, list):
        return 0
    chapter_lookup = _chapter_lookup_for_novel(conn, novel_id)
    if seen_names is None:
        seen_names = set()
    superseded = False
    persisted = 0
    for character in characters:
        if not isinstance(character, dict):
            continue
        name = str(character.get("name") or "").strip()
        if not name:
            continue
        aliases = character.get("aliases") if isinstance(character.get("aliases"), list) else []
        normalized_names = {name.lower()} | {
            str(alias).strip().lower() for alias in aliases if str(alias).strip()
        }
        if normalized_names & seen_names:
            continue
        seen_names.update(normalized_names)
        if not superseded:
            supersede_previous_run_facts(
                conn, novel_id=novel_id, fact_type="character_profile", current_run_id=job_id
            )
            superseded = True
        entities = [name] + [str(alias).strip() for alias in aliases if str(alias).strip()]
        role_type = str(character.get("role_type") or "unknown").strip() or "unknown"
        description = str(character.get("description") or "").strip()
        evidence_items = _norm_evidence(character.get("evidence"))
        confidence = str(character.get("confidence") or "low")
        attributes = character.get("attributes") if isinstance(character.get("attributes"), list) else None
        if attributes:
            by_key: dict[str, dict[str, Any]] = {}
            for attr in attributes:
                if not isinstance(attr, dict):
                    continue
                key = str(attr.get("attribute") or "").strip().lower()
                if key and key not in by_key:
                    by_key[key] = attr
            for key, label in _CHARACTER_ATTRIBUTE_LABELS.items():
                attr = by_key.get(key)
                value = ""
                attr_evidence: list[dict[str, Any]] = []
                if attr:
                    value = str(attr.get("value") or "").strip()
                    attr_evidence = _norm_evidence(attr.get("evidence"))
                if not value:
                    value = "未提及"
                content = f"{name} · {label}: {value}"
                first = attr_evidence[0] if attr_evidence else {}
                chapter_id = _resolve_character_chapter_id(character, first, chapter_lookup)
                source_quote = str(first.get("source_quote") or "")
                upsert_extracted_fact(
                    conn,
                    novel_id=novel_id,
                    fact_type="character_profile",
                    content=content,
                    entities=entities,
                    chapter_id=chapter_id,
                    source_quote=source_quote,
                    confidence=confidence,
                    status="pending_review",
                    model_run_id=job_id,
                    evidence=attr_evidence,
                )
                persisted += 1
        else:
            first = evidence_items[0] if evidence_items else {}
            chapter_id = _resolve_character_chapter_id(character, first, chapter_lookup)
            content = f"{name}: {role_type}" if not description else f"{name}: {role_type} - {description}"
            upsert_extracted_fact(
                conn,
                novel_id=novel_id,
                fact_type="character_profile",
                content=content,
                entities=entities,
                chapter_id=chapter_id,
                source_quote=str(first.get("source_quote") or ""),
                confidence=confidence,
                status="pending_review",
                model_run_id=job_id,
                evidence=evidence_items,
            )
            persisted += 1
    return persisted


def _character_extraction_schema() -> str:
    return (
        """Output a JSON object with a "characters" list. Each character must be an object with:
"name" (string, the canonical name; see rules below),
"aliases" (list of strings, ALL alternative names, titles, abbreviations, variants and typo spellings of this character),
"role_type" (string: one of "protagonist", "antagonist", "supporting", "minor", "mentioned", "unknown"),
"description" (string, one-sentence role summary from provided evidence only),
"source_chapters" (list of chapter numbers/ids where this character appears),
"evidence" (list of objects, each with chapter_title and source_quote),
"confidence" (string: one of "high", "medium", "low"),
"status" ("pending_review").

IMPORTANT RULES:
- Do NOT include the author as a character.
- Do NOT include chapter titles, abstract nouns, function words, or narration fragments as characters.
- Do NOT include phrases like "author", "because", "however", "today", "tomorrow", "here", "there", "everyone", "himself" etc.
- Only include entities that are actual named characters in the novel.
- Every character must have at least one source_quote from the provided excerpts.
- If unsure whether a phrase is a character name, set confidence to "low".
- 每批只输出①本批首次出现的新人物；②已知人物中本批有新证据或属性变化的部分。无变化的已知人物不要输出。
- 已在 known_characters 的人物必须沿用其规范名；新人物选用书中出现最多的叫法作规范名。
- 同一人的所有称呼（简称/尊称/异体/错字变体）全部写入 aliases；禁止同一人拆成多个条目（例：玄奘=唐僧；沙僧=沙和尚=沙悟净=沙悟静 为同一人）。
- 若 A 是 B 的前世/转世，保留两个条目，并在各自身份/背景中互相注明（例：天蓬 写"后被贬下凡转世为猪八戒"；猪八戒 写"前世为天庭天蓬元帅"）。
- 身份/背景只写人物本体（出身、种族、师承、血缘、称号来历），组织任职一律写入 affiliation，不要重复。
- "attributes" (list of objects): extract each attribute separately with its own evidence list.
  Each attribute object: {"attribute": one of "appearance","personality","identity_background","abilities","key_experiences","affiliation", "value": string, "evidence": list of {chapter_title, source_quote}}.
  appearance=外貌, personality=性格, identity_background=身份/背景, abilities=能力, key_experiences=重要经历, affiliation=所属势力.
  affiliation（所属势力）按时间线列出并标注章节，如"妖族（第1-9章）→ 取经队伍（第10章起）"；同时属多个组织用"、"；无势力或未提及写"无（未提及）"。
  For any attribute with no textual basis, output value "未提及" and an empty evidence list; do NOT invent details.
"""
    )


def _known_character_names(
    conn,
    novel_id: int,
    chapter_rows: list[dict[str, Any]],
    model: str | None = None,
) -> list[str]:
    """Canonical names for the extraction payload known_characters list.

    With a model given (character extraction), persisted facts are only used
    when the last combined run for that model is a clean "ok" cache; otherwise
    an empty list keeps batch payloads stable across partial/failed runs so a
    resume reuses the successful batch caches (E1 semantics).
    """
    if model is not None:
        rows = list_chapters(conn, novel_id)
        if rows:
            combined_hash = input_hash(
                "character_extraction_combined", _character_extraction_combined_payload(rows)
            )
            combined_key = cache_key(
                model=model, task_type="character_extraction_combined", input_hash_value=combined_hash
            )
            cached = get_cache(conn, combined_key)
            if (
                cached is None
                or cached.get("status") != "ok"
                or _invalid_output_reason(cached, "character_extraction") is not None
            ):
                return []
    names: list[str] = []
    for fact in list_extracted_facts(conn, novel_id, fact_type="character_profile"):
        entities = fact.get("entities")
        if isinstance(entities, list) and entities:
            name = str(entities[0]).strip()
            if name and name not in names:
                names.append(name)
    if names:
        return sorted(names)
    fallback = _local_character_extraction(chapter_rows)
    for character in fallback.get("characters", []):
        name = str(character.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return sorted(names)


def _local_character_extraction(chapter_rows: list[dict[str, Any]], limit: int = 50) -> dict[str, Any]:
    candidates: dict[str, dict[str, Any]] = {}
    # Skip front-matter chapters (preface, TOC, author notes)
    front_matter_keywords = (
        "序章 序言 前言 后记 感言 摘要 "
        "目录 作者说 作者感言 作者的话 "
        "本书简介 内容简介"
    ).split()
    for chapter_row in chapter_rows:
        title = chapter_row.get("title", "")
        if any(kw in title for kw in front_matter_keywords):
            continue
        text = chapter_row["content"]
        # Skip very short or front-matter content
        if len(text) < 200 and any(kw in text for kw in ("作者", "版权", "转载", "免费")):
            continue
        for name in _character_name_candidates(text):
            if name not in candidates:
                candidates[name] = {
                    "name": name,
                    "aliases": [],
                    "role_type": "unknown",
                    "source_chapters": [],
                    "evidence": [],
                    "confidence": "low",
                    "status": "pending_review",
                }
            item = candidates[name]
            if chapter_row["id"] not in item["source_chapters"]:
                item["source_chapters"].append(chapter_row["id"])
            if len(item["evidence"]) < 3:
                item["evidence"].append(
                    {
                        "chapter_id": chapter_row["id"],
                        "chapter_order": chapter_row["chapter_order"],
                        "chapter_title": chapter_row["title"],
                        "source_quote": _source_quote(text, [name], quote_chars=500),
                    }
                )
        if len(candidates) >= limit:
            break

    # Filter to characters appearing in at least 2 chapters
    min_chapters = 2 if len(chapter_rows) > 1 else 1
    characters = [c for c in candidates.values() if len(c["source_chapters"]) >= min_chapters][:limit]
    if len(characters) < 5:
        characters = list(candidates.values())[:limit]
    return {
        "status": "local_fallback",
        "task_type": "character_extraction",
        "characters": characters,
        "evidence": [evidence for character in characters for evidence in character["evidence"][:1]],
        "suggestions": [
            "Set an API key for model-generated character extraction.",
            "Review fallback character names manually; they are lightweight text matches.",
        ],
        "evidence_required": True,
    }


def _character_name_candidates(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b", text):
        name = match.group(0)
        if name in {"First", "Chapter", "North", "South", "East", "West", "One", "Two"}:
            continue
        if re.search(r"^[A-Z][a-z]{0,2}$", name):
            continue
        if name not in names:
            names.append(name)
    action_words = (
        "来到|进入|发现|提醒|警告|赶到|说道|问道|回答|看见|遇见|带着|拿着|醒来|离开|返回|告诉|望向|跟随"
        "|开口|微微一笑|点头|摇头|叹了口气|沉默|望着|冷笑|淡淡|蹲下|低头"
        "|往前一步|转身|停下脚步|抬头|投去|在前|在后|在旁"
    )
    for match in re.finditer(rf"([\u4e00-\u9fff]{{2,4}})(?:{action_words})", text):
        name = match.group(1)
        if _is_name_stopword(name):
            continue
        for suffix in ("随后", "忽然", "再次", "已经", "正在", "却是", "却在", "却已"):
            if len(name) > 2 and name.endswith(suffix):
                name = name[: -len(suffix)]
        if len(name) < 2 or _is_name_stopword(name):
            continue
        if name not in names:
            names.append(name)
    for match in re.finditer(r"(?<![\u4e00-\u9fff])[\u4e00-\u9fff]{2,4}(?![\u4e00-\u9fff])", text):
        name = match.group(0)
        if len(name) < 2 or _is_name_stopword(name):
            continue
        if re.match(r"^[\u4e00-\u9fff]*[的了是在不和就也都要还能会可以这个那只那里吗呢嘛啊吧呀哟哎]+$", name):
            continue
        if name not in names:
            names.append(name)
    return names


def _is_name_stopword(name: str) -> bool:
    raw = (
        "作者 本书 小说 作品 版权 免费 转载 转贴 整理 更新 "
        "新书 完本 连载 收藏 推荐 投票 打赏 月票 "
        "手打 无广告 字数 字节 章节 总章节 目录 "
        "序言 后记 感言 前言 摘要 笔名 网名 原创 "
        "原著 原文 原作 源于 出品 品牌 标签 "
        "第一章 第二章 第三章 第四章 第五章 "
        "第六章 第七章 第八章 第九章 第十章 "
        "第十一 第十二 第十三 第十四 第十五 "
        "本章 下章 上章 前章 后章 全文 "
        "作者说 作者的话 作者感言 "
        "是因为 所以 然而 但是 不过 虽然 如果 "
        "因为 因此 于是 然后 接着 随后 "
        "没有 不是 不会 不能 不得 不在 不知 "
        "你还 你们 他们 她们 这些 那些 "
        "什么 怎么 为什么 怎样 如何 "
        "这个 那个 某个 各个 另一 另一个 "
        "还是 或者 可是 只是 其实 确实 "
        "却是 却在 却已 却不 却发现 "
        "已经 曾经 正在 即将 立刻 马上 "
        "他的 她的 它的 自己 的时候 的东西 "
        "一起 一般 一直 一时 一下 一点 "
        "很多 很快 很大 很小 很好 很久 "
        "其他 其中 其实 其余 其他人 "
        "大家 所有 任何 全部 所有人 各种 "
        "大部分 大多数 "
        "今天 昨天 明天 今晚 昨晚 今日 "
        "就在 这时 那时 此时 一时 "
        "这里 那里 哪里 这边 那边 "
        "外面 里面 前面 后面 上面 下面 "
        "一个 一种 这种 那种 某种 "
        "一件 这件 那件 每一 每个 "
        "一句 这句 那句 这些话 "
        "一阵 一声 一眼 一步 一拳 "
        "一刀 一剑 一步步 "
        "他们 他的 她们 她的 它们 "
        "我们 我的 你们 你的 您的 "
        "你和 你与 他和 他与 "
        "是否 可能 应该 一定 必须 "
        "不知道 不过是 不是说 不是吗 "
    )
    stopwords = frozenset(raw.split())
    return name.strip() in stopwords


_CHARACTER_ATTRIBUTE_LABELS = {
    "appearance": "外貌",
    "personality": "性格",
    "identity_background": "身份/背景",
    "abilities": "能力",
    "key_experiences": "重要经历",
    "affiliation": "所属势力",
}