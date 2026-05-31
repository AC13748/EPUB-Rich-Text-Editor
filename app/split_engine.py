"""按正则把章节正文拆成多章的工具。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from lxml import html as lhtml

from .book_model import Book, Chapter
from .xhtml_utils import _serialize_html_to_xhtml, _escape_text


DEFAULT_PATTERNS: list[str] = [
    r"^第[一二三四五六七八九十百零\d]+章[：:、\s]+.*$",
    r"^Chapter\s+\d+[：:.\s]+.*$",
    r"^第[一二三四五六七八九十百零\d]+节[：:、\s]+.*$",
    r"^[\d]+\.[：:、\s]+.*$",
    r"^\d+\s+.*$",
]


@dataclass
class SplitChunk:
    title: str
    body_html: str


def compile_patterns(patterns: Iterable[str]) -> list[re.Pattern]:
    out: list[re.Pattern] = []
    for p in patterns:
        p = (p or "").strip()
        if not p:
            continue
        try:
            out.append(re.compile(p, re.MULTILINE))
        except re.error:
            continue
    return out


def _block_matches_any(text: str, regexes: list[re.Pattern]) -> bool:
    text = text.strip()
    if not text:
        return False
    return any(rx.match(text) for rx in regexes)


def _truncate_title(text: str, limit: int = 40) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def split_chapter(ch: Chapter, regexes: list[re.Pattern]) -> list[SplitChunk]:
    """对单个章节按正则切分；每个匹配 block 起一段。"""
    if not regexes:
        return [SplitChunk(ch.title, ch.body_html)]
    body_html = ch.body_html or ""
    if not body_html.strip():
        return [SplitChunk(ch.title, ch.body_html)]
    wrapped = f"<div>{body_html}</div>"
    try:
        root = lhtml.fromstring(wrapped)
    except Exception:
        return [SplitChunk(ch.title, ch.body_html)]
    blocks = list(root)
    if not blocks:
        return [SplitChunk(ch.title, ch.body_html)]

    def serialize(blks) -> str:
        return "".join(_serialize_html_to_xhtml(b) for b in blks) or "<p><br/></p>"

    chunks: list[SplitChunk] = []
    current_title = ch.title
    current_blocks: list = []
    started = False
    for blk in blocks:
        text = (blk.text_content() or "").strip()
        if _block_matches_any(text, regexes):
            if started or current_blocks:
                chunks.append(SplitChunk(current_title, serialize(current_blocks)))
            current_title = _truncate_title(text)
            current_blocks = [blk]
            started = True
        else:
            current_blocks.append(blk)
    chunks.append(SplitChunk(current_title, serialize(current_blocks)))
    if len(chunks) == 1 and chunks[0].title == ch.title:
        return chunks
    return chunks


def preview_split(book: Book, regex_strs: list[str], scope_chapter_ids: list[str]) -> list[tuple[str, list[SplitChunk]]]:
    regexes = compile_patterns(regex_strs)
    out: list[tuple[str, list[SplitChunk]]] = []
    for ch in book.chapters:
        if ch.chapter_id not in scope_chapter_ids:
            continue
        chunks = split_chapter(ch, regexes)
        if len(chunks) > 1:
            out.append((ch.chapter_id, chunks))
    return out


def apply_split(book: Book, regex_strs: list[str], scope_chapter_ids: list[str]) -> int:
    """执行拆分，返回新增章节数。"""
    plan = preview_split(book, regex_strs, scope_chapter_ids)
    added = 0
    for orig_id, chunks in plan:
        orig = book.find_chapter(orig_id)
        if orig is None or len(chunks) <= 1:
            continue
        idx = book.index_of(orig_id)
        # 第一块保留在原章节
        orig.title = chunks[0].title
        orig.body_html = chunks[0].body_html
        # 其余块作为新章节按顺序插入到原章节之后（同级）
        for i, ck in enumerate(chunks[1:], start=1):
            new_ch = Chapter(
                title=ck.title,
                body_html=ck.body_html,
                parent_id=orig.parent_id,
                level=orig.level,
            )
            book.chapters.insert(idx + i, new_ch)
            added += 1
    return added
