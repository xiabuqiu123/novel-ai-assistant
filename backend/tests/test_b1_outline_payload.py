"""B1: book-outline payload feeds chapter summaries, not just title+char_count.

Tests assert:
  * payload contains ``summary:`` text (local snippet fallback when no cache)
  * cached chapter_summary is preferred over local snippets
  * _invalid_output_reason rejects book_outline outputs with >30% empty briefs
"""
from pathlib import Path

from app.cache import cache_key, input_hash
from app.database import (
    connect,
    get_chapter,
    get_chunks_for_chapter,
    import_novel,
    list_chapters,
    put_cache,
)
from app.text_processing import sha256_text, split_chapters

from app import main


MODEL = "gpt-test"


def _import_two_chapter_novel(tmp_path: Path):
    conn = connect(tmp_path / "b1.sqlite3")
    text = "第一章 初入江湖\n少年醒来，掌柜提醒他不要靠近北山。\n第二章 风波起\n少年带着玉牌离开小镇。"
    chapters = split_chapters(text)
    novel = import_novel(
        conn,
        title="B1章纲测试",
        source_filename="b1.txt",
        encoding="utf-8",
        text_hash=sha256_text(text),
        chapters=chapters,
        chunk_size=20,
    )
    return conn, novel


def test_book_outline_payload_includes_summary_not_chars(tmp_path: Path):
    conn, novel = _import_two_chapter_novel(tmp_path)
    rows = list_chapters(conn, novel["id"])
    payload = main._book_outline_payload(conn, rows, MODEL)

    # New format: per-chapter summary block instead of title+chars only.
    assert "summary:" in payload
    assert "chars:" not in payload
    assert "chapter_order:" in payload
    # No cached chapter_summary -> local snippet fallback, content visible.
    assert "source:local_snippet_fallback" in payload
    assert "掌柜" in payload
    conn.close()


def test_book_outline_payload_prefers_cached_chapter_summary(tmp_path: Path):
    conn, novel = _import_two_chapter_novel(tmp_path)
    rows = list_chapters(conn, novel["id"])

    # Store a cached chapter_summary for the first chapter.
    ch1 = get_chapter(conn, int(rows[0]["id"]))
    chunks = get_chunks_for_chapter(conn, int(rows[0]["id"]))
    payload_cs = main._chapter_summary_payload(ch1, chunks)
    h = input_hash("chapter_summary", payload_cs)
    key = cache_key(model=MODEL, task_type="chapter_summary", input_hash_value=h)
    put_cache(
        conn,
        key=key,
        model=MODEL,
        task_type="chapter_summary",
        input_hash_value=h,
        output={
            "status": "ok",
            "task_type": "chapter_summary",
            "parsed_json": {
                "short_summary": "缓存章节摘要：少年在客栈醒来",
                "key_events": ["醒来", "掌柜提醒"],
            },
            "short_summary": "缓存章节摘要：少年在客栈醒来",
            "key_events": ["醒来", "掌柜提醒"],
        },
    )

    payload = main._book_outline_payload(conn, rows, MODEL)

    # Chapter 1 brief came from cache; chapter 2 still local.
    assert "缓存章节摘要" in payload
    assert "source:chapter_summary_cache" in payload
    assert "醒来" in payload  # key event surfaced
    assert "source:local_snippet_fallback" in payload  # chapter 2 has no cache
    conn.close()


def test_invalid_output_reason_rejects_book_outline_many_empty_briefs():
    # 4 chapters, 2 empty briefs -> 50% > 30% -> invalid.
    out = {
        "status": "ok",
        "outline": {
            "chapters": [
                {"chapter_order": 1, "chapter_title": "一", "brief": "有内容"},
                {"chapter_order": 2, "chapter_title": "二", "brief": ""},
                {"chapter_order": 3, "chapter_title": "三", "brief": "有内容"},
                {"chapter_order": 4, "chapter_title": "四", "brief": ""},
            ]
        },
    }
    reason = main._invalid_output_reason(out, "book_outline")
    assert reason is not None
    assert "empty" in reason


def test_invalid_output_reason_accepts_book_outline_few_empty_briefs():
    # 4 chapters, 1 empty brief -> 25% <= 30% -> valid.
    out = {
        "status": "ok",
        "outline": {
            "chapters": [
                {"chapter_order": 1, "chapter_title": "一", "brief": "有内容"},
                {"chapter_order": 2, "chapter_title": "二", "brief": ""},
                {"chapter_order": 3, "chapter_title": "三", "brief": "有内容"},
                {"chapter_order": 4, "chapter_title": "四", "brief": "有内容"},
            ]
        },
    }
    assert main._invalid_output_reason(out, "book_outline") is None


def test_invalid_output_reason_accepts_book_outline_all_briefs_nonempty():
    out = {
        "status": "ok",
        "outline": {
            "chapters": [
                {"chapter_order": 1, "chapter_title": "一", "brief": "有内容"},
                {"chapter_order": 2, "chapter_title": "二", "brief": "有内容"},
            ]
        },
    }
    assert main._invalid_output_reason(out, "book_outline") is None