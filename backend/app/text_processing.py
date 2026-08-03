from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


CHAPTER_PATTERN = re.compile(
    r"^\s*(第\s*[零〇一二三四五六七八九十百千万两0-9]+\s*[章节卷回部集].{0,40}|"
    r"序章|楔子|番外.{0,30}|尾声)\s*$"
)


@dataclass(frozen=True)
class ChapterDraft:
    order: int
    title: str
    content: str


@dataclass(frozen=True)
class ChunkDraft:
    order: int
    content: str
    text_hash: str


def sha256_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def detect_and_decode(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"

    candidates = ("utf-8", "gb18030", "gbk", "big5")
    best_text = ""
    best_encoding = "utf-8"
    best_score = -1
    for encoding in candidates:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        score = _chinese_text_score(text)
        if score > best_score:
            best_text = text
            best_encoding = encoding
            best_score = score

    if best_text:
        return best_text, best_encoding
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _chinese_text_score(text: str) -> int:
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    replacement_penalty = text.count("\ufffd") * 20
    mojibake_penalty = sum(text.count(token) * 5 for token in ("锛", "涓", "涔", "銆", "€"))
    return chinese - replacement_penalty - mojibake_penalty


def split_chapters(text: str) -> list[ChapterDraft]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    chapters: list[ChapterDraft] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if CHAPTER_PATTERN.match(line):
            if current_title is not None:
                chapters.append(_make_chapter(len(chapters) + 1, current_title, current_lines))
            elif current_lines and "".join(current_lines).strip():
                chapters.append(_make_chapter(len(chapters) + 1, "正文前内容", current_lines))
            current_title = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title is not None:
        chapters.append(_make_chapter(len(chapters) + 1, current_title, current_lines))
    elif text.strip():
        chapters.append(ChapterDraft(order=1, title="全文", content=text.strip()))

    return chapters


def _make_chapter(order: int, title: str, lines: list[str]) -> ChapterDraft:
    return ChapterDraft(order=order, title=title, content="\n".join(lines).strip())


def split_chunks(text: str, max_chars: int = 6000, overlap: int = 300) -> list[ChunkDraft]:
    clean = text.strip()
    if not clean:
        return []
    if max_chars <= overlap:
        raise ValueError("max_chars must be greater than overlap")

    chunks: list[ChunkDraft] = []
    start = 0
    while start < len(clean):
        end = min(start + max_chars, len(clean))
        if end < len(clean):
            boundary = max(clean.rfind("\n", start, end), clean.rfind("。", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        content = clean[start:end].strip()
        if content:
            chunks.append(ChunkDraft(order=len(chunks) + 1, content=content, text_hash=sha256_text(content)))
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return chunks


def read_txt(path: Path) -> tuple[str, str, str]:
    raw = path.read_bytes()
    text, encoding = detect_and_decode(raw)
    return text, encoding, sha256_text(text)
