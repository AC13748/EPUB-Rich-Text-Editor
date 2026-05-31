"""自动保存与崩溃恢复。

策略：
- 把 Book 序列化为单个 JSON 文件（含所有章节 HTML、元数据），二进制资源单独写入。
- 默认每隔 5 秒检查 dirty 标志，dirty 则落盘。
- 启动时若发现 .autosave/state.json 存在，提示恢复。
"""
from __future__ import annotations

import base64
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .book_model import Book, Chapter, Resource


AUTOSAVE_DIR = ".autosave"
STATE_FILE = "state.json"


def _autosave_root(work_path: Optional[Path]) -> Path:
    if work_path is not None:
        return work_path.parent / AUTOSAVE_DIR
    return Path.home() / ".pysave_epub" / AUTOSAVE_DIR


def dump_book(book: Book, dst_dir: Optional[Path] = None) -> Path:
    target_dir = dst_dir if dst_dir is not None else _autosave_root(book.work_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "title": book.title,
        "author": book.author,
        "language": book.language,
        "identifier": book.identifier,
        "publisher": book.publisher,
        "description": book.description,
        "export_theme": book.export_theme,
        "font_family": book.font_family,
        "line_height": book.line_height,
        "page_margin": book.page_margin,
        "margin_top_mm": getattr(book, "margin_top_mm", 18),
        "margin_bottom_mm": getattr(book, "margin_bottom_mm", 18),
        "margin_left_mm": getattr(book, "margin_left_mm", 22),
        "margin_right_mm": getattr(book, "margin_right_mm", 22),
        "work_path": str(book.work_path) if book.work_path else None,
        "cover": _resource_to_dict(book.cover) if book.cover else None,
        "chapters": [asdict(c) for c in book.chapters],
        "resources": [_resource_to_dict(r) for r in book.resources],
        "custom_fonts": [_font_to_dict(f) for f in getattr(book, "custom_fonts", []) or []],
    }
    state_path = target_dir / STATE_FILE
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return state_path


def load_book(state_path: Path) -> Book:
    data = json.loads(state_path.read_text(encoding="utf-8"))
    book = Book(
        title=data.get("title", "未命名书籍"),
        author=data.get("author", "佚名"),
        language=data.get("language", "zh-CN"),
        identifier=data.get("identifier", ""),
        publisher=data.get("publisher", ""),
        description=data.get("description", ""),
        export_theme=data.get("export_theme", "classic"),
        font_family=data.get("font_family", "Source Han Serif, serif"),
        line_height=data.get("line_height", 1.8),
        page_margin=data.get("page_margin", "2.2em"),
        margin_top_mm=int(data.get("margin_top_mm", 18)),
        margin_bottom_mm=int(data.get("margin_bottom_mm", 18)),
        margin_left_mm=int(data.get("margin_left_mm", 22)),
        margin_right_mm=int(data.get("margin_right_mm", 22)),
    )
    wp = data.get("work_path")
    book.work_path = Path(wp) if wp else None
    if data.get("cover"):
        book.cover = _resource_from_dict(data["cover"])
    book.chapters = [Chapter(**c) for c in data.get("chapters", [])]
    book.resources = [_resource_from_dict(r) for r in data.get("resources", [])]
    book.custom_fonts = [_font_from_dict(f) for f in data.get("custom_fonts", []) or []]
    return book


def has_autosave(work_path: Optional[Path]) -> Optional[Path]:
    p = _autosave_root(work_path) / STATE_FILE
    return p if p.exists() else None


def clear_autosave(book: Book) -> None:
    p = _autosave_root(book.work_path) / STATE_FILE
    if p.exists():
        p.unlink()


def _resource_to_dict(r: Resource) -> dict:
    return {
        "media_type": r.media_type,
        "filename": r.filename,
        "data_b64": base64.b64encode(r.data).decode("ascii"),
    }


def _resource_from_dict(d: dict) -> Resource:
    return Resource(
        media_type=d["media_type"],
        filename=d["filename"],
        data=base64.b64decode(d["data_b64"]),
    )


def _font_to_dict(f: dict) -> dict:
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "filename": f.get("filename"),
        "media_type": f.get("media_type"),
        "data_b64": base64.b64encode(f.get("data") or b"").decode("ascii"),
        "sha1": f.get("sha1"),
    }


def _font_from_dict(d: dict) -> dict:
    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "filename": d.get("filename"),
        "media_type": d.get("media_type"),
        "data": base64.b64decode(d.get("data_b64", "")),
        "sha1": d.get("sha1"),
    }
