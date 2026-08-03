"""Task-domain module: qa (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import json
import re
from ..database import get_cache
from ..database import get_chapter
from ..database import list_chapters
from ..database import list_extracted_facts
from .common import _source_quote
from .common import _source_quote_at
from .conflicts import _evidence_or_quote
from .conflicts import _fact_evidence
from typing import Any


QA_RETRIEVAL_VERSION = "qa-retrieval-v4"


# D2: 中文停用词（疑问句壳与提问套话），命中即不参与检索词与滑窗子词。
_QA_STOPWORDS = {
    "的事情", "的东西", "主人公", "主角", "是谁", "什么", "哪里", "哪些",
    "怎么样", "为什么", "是不是", "有没有", "怎样", "怎么", "如何", "为何",
    "哪儿", "何处", "何事", "何时", "何地", "多少", "几个", "是否", "事情",
    "东西", "时候", "原因", "干嘛", "咋样", "呢", "吧", "吗", "呀", "嘛", "啊",
}


def _qa_payload(question: str, evidence: list[dict[str, Any]]) -> str:
    _qa_fallback_reasons = {"fallback_sample", "two_level_fallback"}
    fallback_only = bool(evidence) and all(item.get("reason") in _qa_fallback_reasons for item in evidence)
    retrieval_status = "fallback_only" if fallback_only else "matched_evidence"
    fallback_rule = (
        "Retrieval status is fallback_only: no direct keyword or fuzzy quote match was found. "
        "Fallback samples are not whole-book evidence; do not conclude absence from the full novel based on them.\n"
        if fallback_only
        else "Retrieval status is matched_evidence: answer only from direct matched evidence below.\n"
    )
    return (
        f"retrieval_version:{QA_RETRIEVAL_VERSION}\n"
        "Answer only from the evidence JSON below. Output JSON with fact, inference, suggestion, "
        "and cite chapter-level evidence. If evidence is insufficient, say so.\n"
        + fallback_rule
        + f"retrieval_status:{retrieval_status}\n"
        + f"question:{question}\n\nevidence_json:\n"
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    )


def _expanded_query_terms(conn, novel_id: int, terms: list[str]) -> list[str]:
    """Expand query terms using the character alias table (PRD 6.2 level 1)."""
    expanded: list[str] = []
    lower_terms = {t.lower() for t in terms if t}
    if not lower_terms:
        return expanded
    for fact in list_extracted_facts(conn, novel_id, fact_type="character_profile"):
        ents = [str(e).strip() for e in (fact.get("entities") or []) if str(e).strip()]
        if len(ents) < 2:
            continue
        group_lower = {g.lower() for g in ents}
        if not (lower_terms & group_lower):
            continue
        for name in ents:
            if name and name not in expanded:
                expanded.append(name)
    return expanded


def _cached_chapter_summaries(conn, novel_id: int) -> dict[int, dict[str, Any]]:
    """Load cached chapter summaries so the summary index can score candidate chapters."""
    rows = conn.execute(
        "SELECT chapter_id, result_cache_key FROM analysis_jobs "
        "WHERE novel_id = ? AND task_type = 'chapter_summary' AND status = 'completed' "
        "AND result_cache_key != '' ORDER BY id DESC",
        (novel_id,),
    ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        cid = row["chapter_id"]
        if cid is None or int(cid) in out:
            continue
        cached = get_cache(conn, str(row["result_cache_key"]))
        if cached is None:
            continue
        out[int(cid)] = cached
    return out


def _summary_text(summary: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("short_summary", "chapter_title", "title"):
        value = summary.get(key)
        if isinstance(value, str):
            parts.append(value)
    key_events = summary.get("key_events")
    if isinstance(key_events, list):
        parts.extend(str(e) for e in key_events if e)
    elif isinstance(key_events, str):
        parts.append(key_events)
    characters = summary.get("characters")
    if isinstance(characters, list):
        parts.extend(str(c) for c in characters if c)
    return " ".join(parts).lower()


def _candidate_chapter_scores(conn, novel_id: int, terms: list[str]) -> dict[int, int]:
    """Level 1 retrieval: facts store hits + chapter summary index locate candidate chapters."""
    lower_terms = [t.lower() for t in terms if t]
    if not lower_terms:
        return {}
    scores: dict[int, int] = {}
    for fact in list_extracted_facts(conn, novel_id):
        chapter_id = fact.get("chapter_id")
        if chapter_id is None:
            continue
        entity_haystack = " ".join(str(e) for e in (fact.get("entities") or []) if e).lower()
        haystack = " ".join([
            str(fact.get("content") or ""),
            str(fact.get("source_quote") or ""),
            entity_haystack,
        ]).lower()
        matched = sum(1 + haystack.count(t) for t in lower_terms if t and t in haystack)
        entity_matched = sum(1 for t in lower_terms if t and t in entity_haystack)
        if matched > 0 or entity_matched > 0:
            cid = int(chapter_id)
            # D2: facts entities 命中加权（实体名比正文词更可信）。
            scores[cid] = scores.get(cid, 0) + matched * 3 + entity_matched * 5
    summaries = _cached_chapter_summaries(conn, novel_id)
    for cid, summary in summaries.items():
        text = _summary_text(summary)
        if not text:
            continue
        score = sum(text.count(t) for t in lower_terms if t and t in text)
        if score > 0:
            scores[cid] = scores.get(cid, 0) + score
    return scores


def _facts_evidence_for_question(conn, novel_id: int, terms: list[str], limit: int = 6) -> list[dict[str, Any]]:
    """Return evidence items drawn directly from persisted facts that match the query (level 1)."""
    lower_terms = [t.lower() for t in terms if t]
    if not lower_terms:
        return []
    chapter_meta = {}
    for r in conn.execute("SELECT id, chapter_order, title FROM chapters WHERE novel_id = ?", (novel_id,)).fetchall():
        chapter_meta[int(r["id"])] = (int(r["chapter_order"]), str(r["title"] or ""))
    items: list[dict[str, Any]] = []
    for fact in list_extracted_facts(conn, novel_id):
        entity_haystack = " ".join(str(e) for e in (fact.get("entities") or []) if e).lower()
        haystack = " ".join([
            str(fact.get("content") or ""),
            str(fact.get("source_quote") or ""),
            entity_haystack,
        ]).lower()
        matched = [t for t in lower_terms if t and t in haystack]
        if not matched:
            for ev in _fact_evidence(fact):
                q = str((ev or {}).get("source_quote") or "").lower()
                matched = [t for t in lower_terms if t and t in q]
                if matched:
                    break
        entity_matched = [t for t in lower_terms if t and t in entity_haystack]
        if not matched and not entity_matched:
            continue
        cid = fact.get("chapter_id")
        order, title = chapter_meta.get(int(cid), (None, "")) if cid is not None else (None, "")
        ev_list = _evidence_or_quote(fact)
        quote = str((ev_list[0] if ev_list else {}).get("source_quote") or fact.get("source_quote") or "")
        items.append({
            "chapter_id": int(cid) if cid is not None else None,
            "chapter_order": order,
            "chapter_title": title,
            "matched_terms": matched,
            "score": len(matched) + len(entity_matched) * 3,
            "reason": "fact_match",
            "source_quote": quote,
            "fact_type": str(fact.get("fact_type") or ""),
        })
    items.sort(key=lambda i: (-i.get("score", 0), i.get("chapter_order") or 0, -len(i.get("source_quote") or "")))
    return items[:limit]


def _merge_qa_evidence(facts_ev: list[dict[str, Any]], text_ev: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Combine fact-store evidence and level-2 chapter quotes, dedup by chapter."""
    merged: list[dict[str, Any]] = list(facts_ev)
    seen_ids = {item.get("chapter_id") for item in merged if item.get("chapter_id") is not None}
    for item in text_ev:
        cid = item.get("chapter_id")
        if cid is not None and cid in seen_ids and item.get("reason") == "fallback_sample":
            continue
        merged.append(item)
        if cid is not None:
            seen_ids.add(cid)
        if len(merged) >= limit:
            break
    return merged[:limit]


def _retrieve_qa_evidence(conn, novel_id: int, question: str, limit: int = 6) -> list[dict[str, Any]]:
    """Two-level retrieval for Q&A (PRD 6.2).

    Level 1: facts store + chapter summary index locate candidate chapters; query
    terms are expanded with the character alias table. Level 2: only the
    candidate chapters' original text is searched for quotes. When no facts or
    summaries exist yet, falls back to the whole-book scan so recall is preserved.
    """
    all_rows = list_chapters(conn, novel_id)
    terms = _question_terms(question)
    expanded = _expanded_query_terms(conn, novel_id, terms)
    search_terms = terms + [t for t in expanded if t not in terms]
    candidates = _candidate_chapter_scores(conn, novel_id, search_terms)
    if not candidates:
        return _retrieve_evidence(conn, all_rows, question, limit)
    ordered = sorted(candidates.items(), key=lambda kv: (-kv[1], kv[0]))
    row_by_id = {int(r["id"]): r for r in all_rows}
    cand_rows = [row_by_id[int(cid)] for cid, _ in ordered if int(cid) in row_by_id]
    if not cand_rows:
        return _retrieve_evidence(conn, all_rows, question, limit)
    facts_ev = _facts_evidence_for_question(conn, novel_id, search_terms, limit)
    # D2: 只有问句带引文溯源意图词（这句话/出自/哪一章/原文等）且含 ≥6 字引文
    # 候选时才强制全文行级扫描；普通提问即使含长串也只在候选章节内模糊匹配。
    force_full_scan = any(marker in question for marker in _QUOTE_INTENT_MARKERS) and bool(
        _fuzzy_quote_candidates(question)
    )
    text_rows = all_rows if force_full_scan else cand_rows
    text_ev = _retrieve_evidence(conn, text_rows, question, limit)
    merged = _merge_qa_evidence(facts_ev, text_ev, limit)
    if merged:
        return merged
    fallback: list[dict[str, Any]] = []
    for r in cand_rows[:limit]:
        chapter_row = get_chapter(conn, int(r["id"]))
        fallback.append(_evidence_item(chapter_row, [], 0, "fallback_sample", retrieval_status="fallback_only"))
    return fallback


def _retrieve_evidence(conn, chapter_rows: list[dict[str, Any]], question: str, limit: int = 6) -> list[dict[str, Any]]:
    terms = _question_terms(question)
    fuzzy_candidates = _fuzzy_quote_candidates(question)
    scored: list[tuple[int, int, dict[str, Any], list[str]]] = []
    fuzzy_scored: list[tuple[int, int, dict[str, Any], list[str], int]] = []
    fallback: list[dict[str, Any]] = []
    for index, row in enumerate(chapter_rows):
        chapter_row = get_chapter(conn, int(row["id"]))
        text = chapter_row["content"]
        title = chapter_row["title"]
        searchable = f"{title}\n{text}".lower()
        matched_terms = [term for term in terms if term in searchable]
        score = sum(searchable.count(term) for term in matched_terms)
        if fuzzy_candidates:
            normalized, index_map = _normalized_fuzzy_text(f"{title}\n{text}")
            fuzzy_matches = [candidate for candidate in fuzzy_candidates if candidate in normalized]
            if fuzzy_matches:
                best = max(fuzzy_matches, key=len)
                normalized_index = normalized.find(best)
                original_index = index_map[normalized_index] if normalized_index >= 0 and index_map else 0
                text_start = max(0, original_index - len(title) - 1)
                # D2: 引文级匹配（≥6 字）优先于短词关键词命中，引文锚点更可信。
                if len(best) >= 6:
                    fuzzy_scored.append((len(best) + score, index, chapter_row, matched_terms or [best], text_start))
                    continue
                fuzzy_scored.append((len(best), index, chapter_row, [best], text_start))
                continue
        if score > 0:
            scored.append((score, index, chapter_row, matched_terms))
            continue
        if len(fallback) < limit:
            fallback.append(_evidence_item(chapter_row, [], 0, "fallback_sample", retrieval_status="fallback_only"))

    fuzzy_scored.sort(key=lambda item: (-item[0], item[1]))
    if fuzzy_scored and fuzzy_scored[0][0] >= 6:
        return [
            _evidence_item(chapter_row, matched_terms, score, "fuzzy_quote_match", quote_start=quote_start)
            for score, _, chapter_row, matched_terms, quote_start in fuzzy_scored[:limit]
        ]
    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            _evidence_item(chapter_row, matched_terms, score, "keyword_match")
            for score, _, chapter_row, matched_terms in scored[:limit]
        ]
    if fuzzy_scored:
        return [
            _evidence_item(chapter_row, matched_terms, score, "fuzzy_quote_match", quote_start=quote_start)
            for score, _, chapter_row, matched_terms, quote_start in fuzzy_scored[:limit]
        ]
    return fallback


def _local_qa_answer(question: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    matched = [item for item in evidence if item.get("reason") in {"keyword_match", "fuzzy_quote_match", "fact_match"}]
    selected = matched or evidence[:3]
    if matched:
        answer = "Relevant evidence was found in the imported text. Review the cited quotes before treating it as a fact."
        needs_more_context = False
    else:
        answer = "No direct keyword or fuzzy quote match was found. The fallback samples are not whole-book evidence and cannot prove absence from the novel."
        needs_more_context = True
    return {
        "status": "local_fallback",
        "task_type": "evidence_qa",
        "question": question,
        "answer": answer,
        "fact": answer,
        "inference": "Local fallback did not call a model; it only selected text evidence.",
        "suggestion": "Configure and test an API model for evidence-based narrative reasoning.",
        "evidence": selected,
        "needs_more_context": needs_more_context,
        "uncertainty": "High; model reasoning was not used.",
        "evidence_required": True,
    }


def _is_stopword(term: str) -> bool:
    return term.lower() in _QA_STOPWORDS


def _question_terms(question: str) -> list[str]:
    raw_terms = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff\ue000-\uf8ff]{2,}", _strip_question_intent(question.lower()))
    terms: list[str] = []
    for term in raw_terms:
        normalized = term.strip()
        if len(normalized) < 2 or normalized in terms or _is_stopword(normalized):
            continue
        terms.append(normalized)
        # D2: ≥4 字 CJK 串生成 2–4 字滑窗子词（优先长词），停用词子词剔除。
        if re.fullmatch(r"[\u3400-\u9fff\ue000-\uf8ff]{4,}", normalized):
            for size in (4, 3, 2):
                for start in range(0, len(normalized) - size + 1):
                    sub = normalized[start : start + size]
                    if sub not in terms and not _is_stopword(sub):
                        terms.append(sub)
    return terms


def _strip_question_intent(question: str) -> str:
    cleaned = question
    intent_phrases = (
        "这句话出自哪里",
        "这句出自哪里",
        "出自哪里",
        "出现在哪里",
        "在哪里",
        "哪一章",
        "哪章",
        "什么时候",
        "全书中",
        "原文",
        "请问",
        "告诉我",
    )
    for phrase in intent_phrases:
        cleaned = cleaned.replace(phrase, " ")
    return cleaned


# D2 fix: only questions with an explicit quote-tracing intent trigger the
# whole-book line scan; ordinary questions with long CJK runs (e.g. "大闹天宫
# 发生在什么时候") still fuzzy-match, but only inside candidate chapters.
_QUOTE_INTENT_MARKERS = ("这句话", "这句", "出自", "哪一章", "哪章", "原文", "全书")


def _fuzzy_quote_candidates(question: str) -> list[str]:
    cleaned = _strip_question_intent(question)
    candidates: list[str] = []
    for phrase in re.findall(r"[\u3400-\u9fff\ue000-\uf8ff]{6,}", cleaned):
        normalized = _normalized_fuzzy_text(phrase)[0]
        if len(normalized) >= 6 and normalized not in candidates:
            candidates.append(normalized)
        if len(normalized) >= 12:
            for size in (12, 10, 8, 6):
                for start in range(0, len(normalized) - size + 1, max(1, size // 2)):
                    candidate = normalized[start : start + size]
                    if candidate not in candidates:
                        candidates.append(candidate)
    candidates.sort(key=len, reverse=True)
    return candidates


def _normalized_fuzzy_text(text: str) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    index_map: list[int] = []
    ignored_particles = {"的", "也"}
    for index, char in enumerate(text.lower()):
        if char in ignored_particles:
            continue
        if re.match(r"[a-z0-9\u3400-\u9fff\ue000-\uf8ff]", char):
            normalized_chars.append(char)
            index_map.append(index)
    return "".join(normalized_chars), index_map


def _evidence_item(
    chapter_row: dict[str, Any],
    matched_terms: list[str],
    score: int,
    reason: str,
    retrieval_status: str | None = None,
    quote_start: int | None = None,
) -> dict[str, Any]:
    quote = _source_quote_at(chapter_row["content"], quote_start) if quote_start is not None else _source_quote(chapter_row["content"], matched_terms)
    item = {
        "chapter_id": chapter_row["id"],
        "chapter_order": chapter_row["chapter_order"],
        "chapter_title": chapter_row["title"],
        "matched_terms": matched_terms,
        "score": score,
        "reason": reason,
        "source_quote": quote,
    }
    if retrieval_status is not None:
        item["retrieval_status"] = retrieval_status
    return item