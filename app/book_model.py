"""书籍与章节的内存模型。

EPUB 编辑期间所有数据驻留内存；保存/导出时再串行化为标准 EPUB 包。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Resource:
    """归集到 EPUB/images/ 的二进制资源。"""

    media_type: str
    data: bytes
    filename: str  # 形如 image_001.png，相对 EPUB/images/

    @property
    def href(self) -> str:
        return f"images/{self.filename}"


@dataclass
class Chapter:
    """一个 XHTML 文件 = 一章。

    `parent_id` 与 `level` 仅用于章节树的视觉层级（导出为 EPUB nav 的嵌套），
    每个 Chapter 始终对应一份真实正文（`body_html`），没有"纯目录节点"。
    """

    chapter_id: str = field(default_factory=lambda: f"ch_{uuid.uuid4().hex[:10]}")
    title: str = "未命名章节"
    # 编辑器持有的 HTML body 片段（不含 <html>/<head>/<body>）。
    body_html: str = "<p><br></p>"
    # 视觉父子关系：根章节 parent_id=None，level=0。
    parent_id: Optional[str] = None
    level: int = 0

    @property
    def filename(self) -> str:
        return f"{self.chapter_id}.xhtml"

    def is_effectively_empty(self) -> bool:
        """正文是否仅含一个空段落（无可见文本）。"""
        import re
        text = re.sub(r"<[^>]+>", "", self.body_html or "")
        return text.replace("​", "").strip() == ""


@dataclass
class Book:
    """整本书。"""

    title: str = "未命名书籍"
    author: str = "佚名"
    language: str = "zh-CN"
    identifier: str = field(default_factory=lambda: f"urn:uuid:{uuid.uuid4()}")
    publisher: str = ""
    description: str = ""
    cover: Optional[Resource] = None
    chapters: list[Chapter] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    # 自定义字体：[{id, name, filename, media_type, data: bytes}]
    custom_fonts: list[dict] = field(default_factory=list)
    export_theme: str = "classic"  # classic | modern | academic
    font_family: str = "Source Han Serif, Noto Serif CJK SC, serif"
    line_height: float = 1.8
    page_margin: str = "2.2em"
    # 精细页边距（mm）— 编辑期 + 导出期共享
    margin_top_mm: int = 18
    margin_bottom_mm: int = 18
    margin_left_mm: int = 22
    margin_right_mm: int = 22

    # 关联到磁盘的工作路径（用于自动保存与"另存"判断）。
    work_path: Optional[Path] = None

    # ---------- 章节操作 ----------
    def add_chapter(self, title: str = "新章节", index: Optional[int] = None,
                    parent_id: Optional[str] = None, level: int = 0) -> Chapter:
        ch = Chapter(title=title, parent_id=parent_id, level=level)
        if index is None:
            self.chapters.append(ch)
        else:
            self.chapters.insert(index, ch)
        return ch

    def index_of(self, chapter_id: str) -> int:
        for i, c in enumerate(self.chapters):
            if c.chapter_id == chapter_id:
                return i
        return -1

    def remove_chapter(self, chapter_id: str) -> None:
        # 删除时，其子节点全部上移一级（保持顺序，不留孤儿）。
        removed = self.find_chapter(chapter_id)
        if removed is None:
            return
        new_parent = removed.parent_id
        for c in self.chapters:
            if c.parent_id == chapter_id:
                c.parent_id = new_parent
                c.level = max(0, c.level - 1)
        self.chapters = [c for c in self.chapters if c.chapter_id != chapter_id]

    def find_chapter(self, chapter_id: str) -> Optional[Chapter]:
        for c in self.chapters:
            if c.chapter_id == chapter_id:
                return c
        return None

    def reorder(self, ordered_ids: list[str]) -> None:
        idx = {cid: i for i, cid in enumerate(ordered_ids)}
        self.chapters.sort(key=lambda c: idx.get(c.chapter_id, len(ordered_ids)))

    @property
    def spine_order(self) -> list[str]:
        """章节的 spine 顺序 = chapters 当前的扁平顺序。"""
        return [c.chapter_id for c in self.chapters]

    def descendants_of(self, chapter_id: str) -> list[str]:
        """返回某章节的全部后代 id（递归）。用于拖拽防循环。"""
        result: list[str] = []
        stack = [chapter_id]
        while stack:
            pid = stack.pop()
            for c in self.chapters:
                if c.parent_id == pid:
                    result.append(c.chapter_id)
                    stack.append(c.chapter_id)
        return result

    # ---------- 资源 ----------
    def add_custom_font(self, data: bytes, original_name: str, display_name: str | None = None) -> dict:
        """把字体文件登记到 book.custom_fonts；按字节去重。"""
        import hashlib as _hl
        digest = _hl.sha1(data).hexdigest()
        for f in self.custom_fonts:
            if f.get("sha1") == digest:
                return f
        suffix = Path(original_name).suffix.lower().lstrip(".") or "ttf"
        # 文件名冲突自动改名
        base_stem = Path(original_name).stem or f"font_{len(self.custom_fonts)+1}"
        stem = base_stem
        i = 1
        existing_names = {f.get("filename", "") for f in self.custom_fonts}
        while f"{stem}.{suffix}" in existing_names:
            i += 1
            stem = f"{base_stem}_{i}"
        filename = f"{stem}.{suffix}"
        media_map = {
            "ttf": "font/ttf", "otf": "font/otf",
            "woff": "font/woff", "woff2": "font/woff2",
        }
        rec = {
            "id": f"font_{len(self.custom_fonts)+1}",
            "name": display_name or base_stem,
            "filename": filename,
            "media_type": media_map.get(suffix, "application/octet-stream"),
            "data": data,
            "sha1": digest,
        }
        self.custom_fonts.append(rec)
        return rec

    def add_image(self, data: bytes, suffix: str) -> Resource:
        suffix = suffix.lower().lstrip(".")
        if suffix == "jpg":
            suffix = "jpeg"
        media_type = f"image/{suffix}"
        # 按字节内容去重：相同图片复用同一资源
        import hashlib as _hl
        digest = _hl.sha1(data).hexdigest()
        for r in self.resources:
            if getattr(r, "sha1", None) == digest:
                return r
        n = len(self.resources) + 1
        ext = "jpg" if suffix == "jpeg" else suffix
        filename = f"image_{n:04d}.{ext}"
        res = Resource(media_type=media_type, data=data, filename=filename)
        # 用动态属性记录摘要，向后兼容（不破坏 Resource dataclass）
        try:
            object.__setattr__(res, "sha1", digest)
        except Exception:
            pass
        self.resources.append(res)
        return res


def new_book_with_template() -> Book:
    """新建书籍：自动塞入封面页 + 第一章，降低冷启动成本。"""
    book = Book(title="新书籍", author="佚名")
    cover_page = Chapter(
        title="封面",
        body_html=(
            '<h1 style="text-align:center;margin-top:30vh">新书籍</h1>'
            '<p style="text-align:center;color:#888">作者：佚名</p>'
        ),
    )
    cover_page.chapter_id = "ch_cover"
    first = Chapter(title="第一章", body_html="<h1>第一章</h1><p>从这里开始书写……</p>")
    book.chapters = [cover_page, first]
    return book
