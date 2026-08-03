"""Task-domain module: common (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import re
from typing import Any


def _chapter_lookup_for_novel(conn, novel_id: int) -> dict[str, dict[int | str, int]]:
    rows = conn.execute(
        "SELECT id, chapter_order, title FROM chapters WHERE novel_id = ?",
        (novel_id,),
    ).fetchall()
    by_id: dict[int | str, int] = {}
    by_order: dict[int | str, int] = {}
    by_title: dict[int | str, int] = {}
    for row in rows:
        chapter_id = int(row["id"])
        order = int(row["chapter_order"])
        title = str(row["title"] or "").strip()
        by_id[chapter_id] = chapter_id
        by_order[order] = chapter_id
        if title:
            by_title[title] = chapter_id
    return {"id": by_id, "order": by_order, "title": by_title}


def _resolve_character_chapter_id(
    character: dict[str, Any],
    evidence: dict[str, Any],
    lookup: dict[str, dict[int | str, int]],
) -> int | None:
    raw_id = _optional_int(evidence.get("chapter_id"))
    if raw_id is not None and raw_id in lookup["id"]:
        return lookup["id"][raw_id]
    raw_order = _optional_int(evidence.get("chapter_order"))
    if raw_order is not None and raw_order in lookup["order"]:
        return lookup["order"][raw_order]
    title = str(evidence.get("chapter_title") or "").strip()
    if title and title in lookup["title"]:
        return lookup["title"][title]
    source_chapters = character.get("source_chapters")
    if isinstance(source_chapters, list):
        for value in source_chapters:
            order = _optional_int(value)
            if order is not None and order in lookup["order"]:
                return lookup["order"][order]
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_PARTIAL_HINT = "部分批次走了本地兜底，建议重跑"


def _normalize_stage(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    raw_characters = item.get("characters")
    characters: list[str] = []
    if isinstance(raw_characters, list):
        for value in raw_characters:
            name = str(value).strip()
            if name and name not in characters:
                characters.append(name)
    return {
        "stage_index": _as_int(item.get("stage_index") or item.get("index")),
        "title": _text_from_aliases(item, ("title", "stage_title")),
        "chapter_start": _as_int(item.get("chapter_start")),
        "chapter_end": _as_int(item.get("chapter_end")),
        "location": str(item.get("location") or "").strip(),
        "characters": characters,
        "event": str(item.get("event") or "").strip(),
        "resolution": str(item.get("resolution") or "").strip(),
        "outcome": str(item.get("outcome") or "").strip(),
    }


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group(0))
    return 0


def _text_from_aliases(item: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = item.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_outline_chapter(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    order = _as_int(item.get("chapter_order") or item.get("order") or item.get("chapter") or item.get("chapter_number"))
    title = _text_from_aliases(item, ("chapter_title", "title", "name"))
    brief = _text_from_aliases(item, ("brief", "summary", "description", "content"))
    if order == 0 and not title and not brief:
        return None
    return {"chapter_order": order, "chapter_title": title, "brief": brief}


def _normalize_book_outline(parsed: dict[str, Any]) -> dict[str, Any] | None:
    raw_outline = parsed.get("outline")
    if isinstance(raw_outline, list):
        source = {"title": parsed.get("title") or "Book outline", "chapters": raw_outline}
    elif isinstance(raw_outline, dict):
        source = raw_outline
    elif isinstance(parsed.get("chapters"), list):
        source = parsed
    else:
        return None

    raw_chapters = source.get("chapters")
    if not isinstance(raw_chapters, list):
        return None
    chapters = [chapter for chapter in (_normalize_outline_chapter(item) for item in raw_chapters) if chapter is not None]
    if not chapters:
        return None
    title = str(source.get("title") or parsed.get("title") or "Book outline").strip() or "Book outline"
    return {"title": title, "chapters": chapters}


def _normalize_model_output(output: dict[str, Any], task_type: str) -> dict[str, Any]:
    """Normalize model output: if parsed_json has task-specific fields, promote them to top level."""
    parsed = output.get("parsed_json")
    if not isinstance(parsed, dict):
        return output
    if task_type == "character_extraction" and "characters" in parsed and isinstance(parsed["characters"], list):
        output["characters"] = parsed["characters"]
    if task_type == "relationship_extraction" and "relationships" in parsed and isinstance(parsed["relationships"], list):
        output["relationships"] = parsed["relationships"]
    if task_type == "setting_extraction" and "settings" in parsed and isinstance(parsed["settings"], list):
        output["settings"] = parsed["settings"]
    if task_type == "event_extraction" and "events" in parsed and isinstance(parsed["events"], list):
        output["events"] = parsed["events"]
    if task_type == "conflict_detection" and "conflicts" in parsed and isinstance(parsed["conflicts"], list):
        output["conflicts"] = parsed["conflicts"]
    if task_type == "book_outline":
        outline = _normalize_book_outline(parsed)
        if outline is not None:
            output["outline"] = outline
            output["status"] = str(parsed.get("status") or output.get("status") or "ok")
        else:
            output["status"] = "parse_error"
            output["model_error"] = "Model response did not contain a usable outline.chapters list."
    if task_type == "book_stage_outline":
        stages = parsed.get("stages")
        if isinstance(stages, list):
            normalized_stages = [
                stage for stage in (_normalize_stage(item) for item in stages) if stage is not None
            ]
            output["stages"] = normalized_stages
            output["status"] = str(parsed.get("status") or output.get("status") or "ok")
        else:
            output["status"] = "parse_error"
            output["model_error"] = "Model response did not contain a usable stages list for book_stage_outline."
        if isinstance(parsed.get("evidence"), list):
            output["evidence"] = parsed["evidence"]


    if task_type == "chapter_summary":
        for field in ("short_summary", "key_events", "characters", "evidence"):
            if field in parsed:
                output[field] = parsed[field]
        if "status" in parsed:
            output["status"] = str(parsed.get("status") or output.get("status") or "ok")
    if task_type == "evidence_qa":
        for field in ("answer", "fact", "inference", "suggestion", "evidence", "uncertainty"):
            if field in parsed:
                output[field] = parsed[field]
    return output


def _summary_snippets(text: str, limit: int = 5) -> list[str]:
    candidates = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s*|\n+", text) if part.strip()]
    snippets: list[str] = []
    for candidate in candidates:
        if len(candidate) < 8:
            continue
        snippets.append(candidate[:240])
        if len(snippets) >= limit:
            break
    if snippets:
        return snippets
    return [text[:240]] if text else []


def _source_quote_at(text: str, quote_start: int | None, quote_chars: int = 900) -> str:
    if quote_start is None or quote_start < 0:
        return text[:quote_chars]
    start = max(0, quote_start - quote_chars // 3)
    end = min(len(text), start + quote_chars)
    return text[start:end]


def _source_quote(text: str, matched_terms: list[str], quote_chars: int = 900) -> str:
    if not matched_terms:
        return text[:quote_chars]
    lower_text = text.lower()
    first_hit = min((lower_text.find(term) for term in matched_terms if term in lower_text), default=-1)
    if first_hit < 0:
        return text[:quote_chars]
    start = max(0, first_hit - quote_chars // 3)
    end = min(len(text), start + quote_chars)
    return text[start:end]


def _norm_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]