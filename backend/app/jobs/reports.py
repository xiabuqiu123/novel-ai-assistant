"""Task-domain module: reports (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

from ..database import get_cache
from .common import _as_int
from typing import Any


def _novel_markdown_export(
    novel: dict[str, Any],
    chapter_rows: list[dict[str, Any]],
    excerpt_chars: int = 1200,
) -> str:
    lines = [
        f"# {_markdown_heading_text(str(novel['title']))}",
        "",
        "## Chapters",
        "",
    ]
    for chapter_row in chapter_rows:
        lines.append(f"- {chapter_row['chapter_order']}. {_markdown_inline_text(str(chapter_row['title']))}")

    for chapter_row in chapter_rows:
        content = str(chapter_row["content"]).strip()
        excerpt = content[:excerpt_chars].strip()
        if len(content) > excerpt_chars:
            excerpt += "..."
        lines.extend(
            [
                "",
                f"## {chapter_row['chapter_order']}. {_markdown_heading_text(str(chapter_row['title']))}",
                "",
                excerpt,
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _latest_cached_book_outline(conn, novel_id: int) -> dict[str, Any] | None:
    """Return the most recent completed book_outline cached result, or None."""
    row = conn.execute(
        "SELECT result_cache_key FROM analysis_jobs "
        "WHERE novel_id = ? AND task_type = 'book_outline' AND status = 'completed' "
        "AND result_cache_key != '' ORDER BY id DESC LIMIT 1",
        (novel_id,),
    ).fetchone()
    if row is None:
        return None
    return get_cache(conn, str(row["result_cache_key"]))


def _report_chapter_title(chapter_rows: list[dict[str, Any]], chapter_id: Any) -> str:
    if chapter_id is None:
        return ""
    try:
        cid = int(chapter_id)
    except (TypeError, ValueError):
        return ""
    for row in chapter_rows:
        if int(row["id"]) == cid:
            order = row.get("chapter_order")
            title = str(row.get("title") or "").strip()
            if order is not None and title:
                return f"第{order}章 {title}"
            if title:
                return title
    return ""


def _report_fact_evidence_lines(fact: dict[str, Any], chapter_rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    evidence = fact.get("evidence")
    if isinstance(evidence, list):
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            quote = str(ev.get("source_quote") or ev.get("quote") or "").strip()
            if not quote:
                continue
            ref = _report_chapter_title(chapter_rows, ev.get("chapter_id"))
            tag = f"（{ref}）" if ref else ""
            lines.append(f"> {quote}{tag}")
    quote = str(fact.get("source_quote") or "").strip()
    if not lines and quote:
        ref = _report_chapter_title(chapter_rows, fact.get("chapter_id"))
        tag = f"（{ref}）" if ref else ""
        lines.append(f"> {quote}{tag}")
    return lines


def _report_section_header(title: str) -> list[str]:
    return ["", f"## {title}", ""]


def _full_report_markdown(
    novel: dict[str, Any],
    chapter_rows: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    outline: dict[str, Any] | None,
    *,
    chapters_with_content: list[dict[str, Any]] | None = None,
) -> str:
    title = _markdown_heading_text(str(novel.get("title") or "Untitled"))
    lines: list[str] = [f"# {title} - 分析报告", ""]

    total = len(chapter_rows)
    if total:
        lines.append(f"- 章节总数：{total}")
    gen_time = str(novel.get("created_at") or "").strip()
    if gen_time:
        lines.append(f"- 导入时间：{gen_time}")
    lines.append(f"- 已抽取事实：{len(facts)} 条")

    by_type: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        by_type.setdefault(str(fact.get("fact_type") or "other"), []).append(fact)

    # ---- Whole-book outline ----
    outline_added = False
    if outline and isinstance(outline.get("outline"), dict):
        ol = outline["outline"]
        ol_title = _markdown_heading_text(str(ol.get("title") or title))
        lines.extend(_report_section_header("全书大纲"))
        lines.append(f"**{ol_title}**")
        lines.append("")
        chapters = ol.get("chapters")
        if isinstance(chapters, list) and chapters:
            lines.append("| 章节 | 摘要 |")
            lines.append("| --- | --- |")
            for item in chapters:
                if not isinstance(item, dict):
                    continue
                order = item.get("chapter_order") or item.get("order") or ""
                ch_title = str(item.get("chapter_title") or item.get("title") or "").strip()
                brief = _markdown_inline_text(str(item.get("brief") or "")).strip()
                label = f"第{order}章" if order != "" else ""
                if ch_title:
                    label = f"{label} {ch_title}".strip()
                lines.append(f"| {label} | {brief} |")
        outline_added = True
    elif total:
        lines.extend(_report_section_header("全书大纲"))
        lines.append("> 暂无全书大纲（请先运行全书大纲分析）。")
        outline_added = True

    # ---- Characters (grouped by name + attribute) ----
    character_facts = by_type.get("character_profile", [])
    if character_facts:
        lines.extend(_report_section_header("人物档案"))
        profiles: dict[str, list[dict[str, Any]]] = {}
        for fact in character_facts:
            content = str(fact.get("content") or "")
            name = ""
            attribute = ""
            value = ""
            if " · " in content and ": " in content:
                head, value = content.split(": ", 1)
                if " · " in head:
                    name, attribute = head.split(" · ", 1)
            else:
                value = content
                ents = fact.get("entities") or []
                if isinstance(ents, list) and ents:
                    name = str(ents[0]).strip()
            name = name.strip()
            attribute = attribute.strip() or "档案"
            value = value.strip()
            if not name and not value:
                continue
            profiles.setdefault(name or "未命名", []).append({"attribute": attribute, "value": value, "fact": fact})
        for name in sorted(profiles.keys()):
            lines.append(f"### {name}")
            lines.append("")
            attrs = profiles[name]
            seen: set[str] = set()
            for attr in sorted(attrs, key=lambda a: a["attribute"]):
                key = attr["attribute"]
                if key in seen:
                    continue
                seen.add(key)
                value = attr["value"]
                label = value if value and value != "未提及" else "未提及"
                lines.append(f"- **{key}**：{label}")
                for ev_line in _report_fact_evidence_lines(attr["fact"], chapter_rows):
                    lines.append(f"  {ev_line}")
            lines.append("")

    # ---- Relationships ----
    relationship_facts = by_type.get("character_relationship", [])
    if relationship_facts:
        lines.extend(_report_section_header("人物关系"))
        for fact in relationship_facts:
            ents = fact.get("entities") or []
            label = " - ".join(str(e) for e in ents) if isinstance(ents, list) and ents else str(fact.get("content") or "")
            content = str(fact.get("content") or "").strip()
            lines.append(f"- {label}")
            if content:
                lines.append(f"  - {content}")
            for ev_line in _report_fact_evidence_lines(fact, chapter_rows):
                lines.append(f"  {ev_line}")

    # ---- Settings: world rules / factions / locations / setting facts ----
    setting_types = [
        ("world_rule", "世界规则"),
        ("faction", "势力"),
        ("location", "地点"),
        ("setting_fact", "设定事实"),
    ]
    for ftype, heading in setting_types:
        items = by_type.get(ftype, [])
        if not items:
            continue
        lines.extend(_report_section_header(heading))
        for fact in items:
            content = str(fact.get("content") or "").strip() or "（未命名）"
            ents = fact.get("entities") or []
            ent_tag = ""
            if isinstance(ents, list) and ents:
                ent_tag = f"（{' / '.join(str(e) for e in ents)}）"
            lines.append(f"- {content}{ent_tag}")
            for ev_line in _report_fact_evidence_lines(fact, chapter_rows):
                lines.append(f"  {ev_line}")

    # ---- Timeline: events ----
    event_facts = by_type.get("event", [])
    if event_facts:
        lines.extend(_report_section_header("事件时间线"))

        def event_sort_key(fact: dict[str, Any]) -> tuple:
            extra = fact.get("extra") if isinstance(fact.get("extra"), dict) else {}
            era = str(extra.get("era") or "").strip()
            story_order = _as_int(extra.get("story_time_order")) or 0
            has_story_time = bool(era) and story_order > 0
            ch_order = _as_int(extra.get("chapter_order")) or 0
            ev_order = _as_int(extra.get("event_order")) or 0
            fact_id = _as_int(fact.get("id")) or 0
            return (
                0 if has_story_time else 1,
                story_order if has_story_time else 0,
                ch_order,
                ev_order,
                fact_id,
            )

        def _event_line(fact: dict[str, Any]) -> None:
            content = str(fact.get("content") or "").strip() or "（未命名事件）"
            extra = fact.get("extra") if isinstance(fact.get("extra"), dict) else {}
            context = str(extra.get("time_context") or "").strip()
            ref = _report_chapter_title(chapter_rows, fact.get("chapter_id"))
            tag = f"（{ref}）" if ref else ""
            ctx_tag = f"（{context}）" if context else ""
            lines.append(f"- {content}{tag}{ctx_tag}")
            for ev_line in _report_fact_evidence_lines(fact, chapter_rows):
                lines.append(f"  {ev_line}")

        # D1 对齐前端时间线页: 先按 era 分组, 组内按 story_time_order 排序;
        # 无法判断时序的事件归入「时序不明」组并排在最后。
        era_groups: dict[str, list[dict[str, Any]]] = {}
        unknown_group: list[dict[str, Any]] = []
        for fact in sorted(event_facts, key=event_sort_key):
            extra = fact.get("extra") if isinstance(fact.get("extra"), dict) else {}
            era = str(extra.get("era") or "").strip()
            story_order = _as_int(extra.get("story_time_order")) or 0
            if era and story_order > 0:
                era_groups.setdefault(era, []).append(fact)
            else:
                unknown_group.append(fact)
        for era, facts in era_groups.items():
            lines.append("")
            lines.append(f"### {era}")
            lines.append("")
            for fact in facts:
                _event_line(fact)
        if unknown_group:
            lines.append("")
            lines.append("### 时序不明（AI 推断时序，供参考）")
            lines.append("")
            for fact in unknown_group:
                _event_line(fact)

    # ---- Conflicts (with review status) ----
    conflict_facts = by_type.get("setting_conflict", [])
    if conflict_facts:
        lines.extend(_report_section_header("设定冲突（待人工复核）"))
        sev_order = {"high": 0, "medium": 1, "low": 2}

        def conflict_sort_key(fact: dict[str, Any]) -> tuple:
            extra = fact.get("extra") if isinstance(fact.get("extra"), dict) else {}
            sev = str(extra.get("severity") or "low")
            return (sev_order.get(sev, 3),)

        for fact in sorted(conflict_facts, key=conflict_sort_key):
            extra = fact.get("extra") if isinstance(fact.get("extra"), dict) else {}
            sev = str(extra.get("severity") or "low")
            status = str(fact.get("status") or "pending_review")
            ctitle = str(fact.get("content") or extra.get("title") or "").strip() or "（未命名冲突）"
            ctype = str(extra.get("type") or "").strip()
            head = f"- [{sev}/{status}] {ctitle}"
            if ctype:
                head += f"（{ctype}）"
            lines.append(head)
            earlier = extra.get("earlier_evidence")
            if isinstance(earlier, list) and earlier:
                for ev in earlier:
                    if isinstance(ev, dict):
                        q = str(ev.get("source_quote") or "").strip()
                        if q:
                            ref = _report_chapter_title(chapter_rows, ev.get("chapter_id"))
                            tag = f"（{ref}）" if ref else ""
                            lines.append(f"  - 早期证据：{q}{tag}")
            later = extra.get("later_evidence")
            if isinstance(later, list) and later:
                for ev in later:
                    if isinstance(ev, dict):
                        q = str(ev.get("source_quote") or "").strip()
                        if q:
                            ref = _report_chapter_title(chapter_rows, ev.get("chapter_id"))
                            tag = f"（{ref}）" if ref else ""
                            lines.append(f"  - 后续证据：{q}{tag}")
            explanation = str(extra.get("possible_explanation") or "").strip()
            if explanation:
                lines.append(f"  - 可能解释：{explanation}")
            judgment = str(extra.get("model_judgment") or "").strip()
            if judgment:
                lines.append(f"  - 模型判断：{judgment}")
            lines.append("")
        lines.append("> 冲突结论仅为模型基于证据的判断，须经人工复核确认。")

    # ---- Chapter text (optional, full book) ----
    if chapters_with_content:
        lines.extend(_report_section_header("章节原文"))
        for chapter_row in chapters_with_content:
            content = str(chapter_row.get("content") or "").strip()
            order = chapter_row.get("chapter_order")
            ch_title = _markdown_heading_text(str(chapter_row.get("title") or ""))
            label = f"第{order}章 {ch_title}" if order is not None else ch_title
            lines.extend(["", f"### {label}", "", content])

    return "\n".join(lines).strip() + "\n"


def _markdown_heading_text(text: str) -> str:
    cleaned = text.replace("\r", " ").replace("\n", " ").strip()
    return cleaned.lstrip("#").strip() or "Untitled"


def _markdown_inline_text(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").strip() or "Untitled"