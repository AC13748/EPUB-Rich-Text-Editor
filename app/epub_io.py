"""EPUB 3 读写。

导出：把内存中的 Book 串行化为标准 EPUB 3 包，使用 ebooklib + lxml 序列化 XHTML。
导入：解析现有 EPUB 为内存模型；正文按严格 XML（lxml.etree）解析，保留命名空间。
BeautifulSoup 仅用于脏 HTML 兜底清洗，不参与 EPUB 内部 XHTML 的核心解析。
"""
from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Optional

from ebooklib import epub, ITEM_DOCUMENT, ITEM_IMAGE, ITEM_COVER
from lxml import etree

from .book_model import Book, Chapter, Resource
from .theme import build_export_css
from .xhtml_utils import (
    parse_xhtml_bytes,
    get_body,
    serialize_body_inner,
    html_fragment_to_xhtml,
    XHTML_NS,
)


XHTML_TPL = """<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="styles/book.css" />
</head>
<body>
{body}
</body>
</html>
"""


def _wrap_xhtml(title: str, body: str, lang: str) -> str:
    # 通过 lxml.html 把可能不够规范的片段净化为合规 XHTML，再嵌入模板。
    body_xhtml = html_fragment_to_xhtml(body or "") or "<p><br/></p>"
    return XHTML_TPL.format(title=_escape(title or "未命名"), body=body_xhtml, lang=lang or "zh-CN")


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------- 导出 ----------------
_DATA_URL_RE = re.compile(r"^data:(image/[a-zA-Z0-9+.\-]+);base64,(.+)$", re.DOTALL)


def _extract_data_urls(book: Book) -> None:
    """扫描所有章节 HTML，把 data:image/* 替换为归集到 book.resources 的相对引用。

    使用 lxml.html 容错解析片段；保持 XHTML 序列化输出。
    """
    from lxml import html as lhtml

    for ch in book.chapters:
        if "data:image" not in ch.body_html:
            continue
        wrapped = f"<div>{ch.body_html}</div>"
        try:
            root = lhtml.fromstring(wrapped)
        except Exception:
            continue
        for img in root.iter("img"):
            src = img.get("src", "")
            m = _DATA_URL_RE.match(src)
            if not m:
                continue
            mime = m.group(1)
            try:
                data = base64.b64decode(m.group(2))
            except Exception:
                continue
            ext = mime.split("/", 1)[1]
            res = book.add_image(data, ext)
            img.set("src", res.href)
        # 重新拿到清洗后的片段（去掉外层 div）
        from .xhtml_utils import _serialize_html_to_xhtml, _escape_text
        parts: list[str] = []
        if root.text:
            parts.append(_escape_text(root.text))
        for c in root:
            parts.append(_serialize_html_to_xhtml(c))
            if c.tail:
                parts.append(_escape_text(c.tail))
        ch.body_html = "".join(parts).strip() or "<p><br/></p>"


def export_epub(book: Book, dst: Path) -> None:
    _extract_data_urls(book)

    eb = epub.EpubBook()
    eb.set_identifier(book.identifier or f"urn:uuid:{uuid.uuid4()}")
    eb.set_title(book.title or "未命名书籍")
    eb.set_language(book.language or "zh-CN")
    eb.add_author(book.author or "佚名")
    if book.publisher:
        eb.add_metadata("DC", "publisher", book.publisher)
    if book.description:
        eb.add_metadata("DC", "description", book.description)

    # 主样式
    css = epub.EpubItem(
        uid="book_css",
        file_name="styles/book.css",
        media_type="text/css",
        content=build_export_css(book).encode("utf-8"),
    )
    eb.add_item(css)

    # 封面
    if book.cover is not None:
        eb.set_cover(book.cover.filename, book.cover.data)

    # 资源
    for res in book.resources:
        item = epub.EpubItem(
            uid=f"img_{res.filename}",
            file_name=f"images/{res.filename}",
            media_type=res.media_type,
            content=res.data,
        )
        eb.add_item(item)

    # 自定义字体
    for f in getattr(book, "custom_fonts", []) or []:
        item = epub.EpubItem(
            uid=f"font_{f.get('filename', '')}",
            file_name=f"fonts/{f.get('filename', '')}",
            media_type=f.get("media_type", "font/ttf"),
            content=f.get("data") or b"",
        )
        eb.add_item(item)

    # 章节
    spine: list = ["nav"]
    items_by_id: dict[str, epub.EpubHtml] = {}
    for ch in book.chapters:
        xhtml = _wrap_xhtml(ch.title, ch.body_html, book.language or "zh-CN")
        item = epub.EpubHtml(
            title=ch.title,
            file_name=ch.filename,
            lang=book.language or "zh-CN",
            content=xhtml,
        )
        item.add_item(css)
        eb.add_item(item)
        spine.append(item)
        items_by_id[ch.chapter_id] = item

    eb.toc = tuple(_build_nested_toc(book, items_by_id))
    eb.add_item(epub.EpubNcx())
    eb.add_item(epub.EpubNav())
    eb.spine = spine

    dst.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(dst), eb)


def _build_nested_toc(book: Book, items_by_id: dict) -> list:
    """根据 Chapter.parent_id 构建嵌套 TOC：
       根章节作为顶层；有子节点的章节用 (Section, [children]) 元组。"""
    children_of: dict = {}
    for ch in book.chapters:
        children_of.setdefault(ch.parent_id, []).append(ch)

    def build(parent_id):
        out: list = []
        for ch in children_of.get(parent_id, []):
            kids = build(ch.chapter_id)
            link = epub.Link(ch.filename, ch.title, ch.chapter_id)
            if kids:
                out.append((link, kids))
            else:
                out.append(link)
        return out

    return build(None)


# ---------------- 导入 ----------------
_HREF_PREFIX_RE = re.compile(r"^(?:\.\./)+")


def _read_epub_without_ncx(src: Path):
    """OPF manifest 里声明的某些文件在 zip 包内不存在，导致 read_epub 抛 KeyError。
    策略：
    1) 优先尝试**修复 href**：在 zip 里找同名 basename 的真实文件，把 manifest 的 href 改过去；
    2) 仅当确实找不到任何同名候选时，才把 item 从 manifest / spine 里剥掉；
    3) 这样能尽可能保住 nav.xhtml / toc.ncx 的目录信息。
    """
    import zipfile
    import tempfile
    import posixpath
    from urllib.parse import unquote
    from lxml import etree as _et

    with zipfile.ZipFile(str(src), "r") as zin:
        names = zin.namelist()
        name_set = set(names)
        # 索引：basename(小写) -> [完整 zip 路径...]
        by_base: dict[str, list[str]] = {}
        for n in names:
            by_base.setdefault(posixpath.basename(n).lower(), []).append(n)

        opf_name = next((n for n in names if n.lower().endswith(".opf")), None)
        if opf_name is None:
            raise FileNotFoundError("EPUB 包内未找到 .opf 文件")
        opf_bytes = zin.read(opf_name)
        opf_dir = posixpath.dirname(opf_name)

        try:
            tree = _et.fromstring(opf_bytes)
            ns = {"opf": "http://www.idpf.org/2007/opf"}
            removed_ids: set[str] = set()
            for item in list(tree.findall(".//opf:manifest/opf:item", ns)):
                hr = unquote(item.get("href") or "")
                if not hr:
                    continue
                full = posixpath.normpath(posixpath.join(opf_dir, hr)) if opf_dir else hr
                if full in name_set or hr in name_set:
                    continue
                # 缺失：尝试按 basename 重定向
                cands = by_base.get(posixpath.basename(hr).lower(), [])
                if len(cands) == 1:
                    real = cands[0]
                    # 计算 real 相对 opf_dir 的相对路径
                    if opf_dir:
                        try:
                            new_href = posixpath.relpath(real, opf_dir)
                        except ValueError:
                            new_href = real
                    else:
                        new_href = real
                    item.set("href", new_href)
                    continue
                # 没有可用候选 -> 剥离
                iid = item.get("id") or ""
                if iid:
                    removed_ids.add(iid)
                parent = item.getparent()
                if parent is not None:
                    parent.remove(item)
            for spine in tree.findall(".//opf:spine", ns):
                # 只在被删除的 idref 真不在 manifest 里时清理 itemref
                for ir in list(spine.findall("opf:itemref", ns)):
                    if (ir.get("idref") or "") in removed_ids:
                        spine.remove(ir)
                # spine.toc 属性指向的 idref 若被删则一并去掉
                toc_ref = spine.attrib.get("toc")
                if toc_ref and toc_ref in removed_ids:
                    del spine.attrib["toc"]
            opf_bytes = _et.tostring(tree, xml_declaration=True, encoding="utf-8")
        except Exception:
            pass

        tmp = tempfile.NamedTemporaryFile(suffix=".epub", delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zout:
            for name in names:
                if name == opf_name:
                    zout.writestr(name, opf_bytes)
                else:
                    zout.writestr(name, zin.read(name))

    try:
        return epub.read_epub(tmp.name, options={"ignore_ncx": True})
    except TypeError:
        return epub.read_epub(tmp.name)


def import_epub(src: Path) -> Book:
    try:
        eb = epub.read_epub(str(src), options={"ignore_ncx": True})
    except TypeError:
        # 老版 ebooklib 不接受 options 参数
        try:
            eb = epub.read_epub(str(src))
        except KeyError:
            eb = _read_epub_without_ncx(src)
    except KeyError:
        # manifest 声明了某文件但包内不存在（toc.ncx / nav.xhtml 等都可能）
        eb = _read_epub_without_ncx(src)

    book = Book()
    book.title = (eb.get_metadata("DC", "title") or [("未命名书籍", {})])[0][0] or "未命名书籍"
    creators = eb.get_metadata("DC", "creator")
    book.author = (creators[0][0] if creators else "佚名") or "佚名"
    langs = eb.get_metadata("DC", "language")
    book.language = (langs[0][0] if langs else "zh-CN") or "zh-CN"
    ids = eb.get_metadata("DC", "identifier")
    if ids and ids[0][0]:
        book.identifier = ids[0][0]
    pubs = eb.get_metadata("DC", "publisher")
    if pubs and pubs[0][0]:
        book.publisher = pubs[0][0]
    descs = eb.get_metadata("DC", "description")
    if descs and descs[0][0]:
        book.description = descs[0][0]

    # 资源（图片）
    href_to_filename: dict[str, str] = {}
    for it in eb.get_items():
        if it.get_type() in (ITEM_IMAGE, ITEM_COVER):
            data = it.get_content()
            orig_name = Path(it.file_name).name
            ext = Path(orig_name).suffix.lstrip(".") or "png"
            res = book.add_image(data, ext)
            href_to_filename[it.file_name] = res.href
            href_to_filename[orig_name] = res.href
            if it.get_type() == ITEM_COVER and book.cover is None:
                book.cover = res

    # ---------- 章节切分 ----------
    # 1) 按 spine 顺序拿到所有 XHTML 文档；
    # 2) 解析 TOC（NCX / Nav）拿到目录条目（href -> 标题，含层级）；
    # 3) 若 TOC 为空或只有 1 条根项 -> 全书合并成单章；
    #    否则 -> 按 TOC 切分，未在 TOC 中的 XHTML 内容并入"前一个 TOC 入口"对应的章节。

    spine_docs = _collect_spine_docs(eb)
    toc_entries = _flatten_toc(eb.toc, base_level=0)

    if len(toc_entries) <= 1:
        # 没拿到 TOC：按 spine 逐篇切章；跳过疑似"目录页"
        spine_names = {Path(d.file_name).name for d in spine_docs}
        for idx, doc in enumerate(spine_docs):
            body_html = _extract_body_html(doc, href_to_filename)
            if idx == 0 and _looks_like_toc_page(doc, spine_names):
                continue
            title = _doc_title(doc) or f"第 {len(book.chapters) + 1} 章"
            ch = Chapter(title=title, body_html=body_html or "<p><br></p>")
            book.chapters.append(ch)
        if not book.chapters:
            ch = Chapter(title=book.title or "正文",
                         body_html="\n".join(
                             _extract_body_html(d, href_to_filename) for d in spine_docs
                         ).strip() or "<p><br></p>")
            book.chapters.append(ch)
    else:
        # 把每个 spine 文档（按其文件名）与 TOC 关联：
        # - 如果文件名出现在 TOC 中：开一个新章节
        # - 否则：把内容追加到「最近开过的章节」
        toc_by_name: dict[str, tuple[str, int]] = {}
        for entry in toc_entries:
            name = _normalize_href(entry["href"])
            toc_by_name.setdefault(name, (entry["title"], entry["level"]))

        # 维护章节 id 栈，便于设置 parent_id
        stack: list[tuple[int, str]] = []  # (level, chapter_id)
        current_chapter: Optional[Chapter] = None
        for doc in spine_docs:
            doc_key = Path(doc.file_name).name
            body_html = _extract_body_html(doc, href_to_filename)
            if doc_key in toc_by_name:
                title, lvl = toc_by_name[doc_key]
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                parent_id = stack[-1][1] if stack else None
                ch = Chapter(title=title, body_html=body_html or "<p><br></p>",
                             parent_id=parent_id, level=lvl)
                book.chapters.append(ch)
                stack.append((lvl, ch.chapter_id))
                current_chapter = ch
            else:
                # 内容附加到当前章节；无章节则开一个"正文"
                if current_chapter is None:
                    current_chapter = Chapter(title=book.title or "正文",
                                              body_html=body_html or "<p><br></p>")
                    book.chapters.append(current_chapter)
                    stack.append((0, current_chapter.chapter_id))
                elif body_html.strip():
                    current_chapter.body_html = (current_chapter.body_html + "\n" + body_html).strip()

    if not book.chapters:
        book.chapters.append(Chapter(title="第一章", body_html="<h1>第一章</h1><p><br></p>"))
    # 保险：每章至少含一个空段落
    for c in book.chapters:
        if not c.body_html or not c.body_html.strip():
            c.body_html = "<p><br></p>"
    return book


def _collect_spine_docs(eb: "epub.EpubBook") -> list:
    """返回按 spine 顺序排列的 XHTML 文档（去掉 nav）。"""
    docs: list = []
    seen = set()
    # spine 是 [(idref, linear), ...]
    for entry in (eb.spine or []):
        if isinstance(entry, tuple):
            idref = entry[0]
        else:
            idref = entry
        it = eb.get_item_with_id(idref)
        if it is None:
            continue
        if it.get_type() != ITEM_DOCUMENT:
            continue
        if isinstance(it, epub.EpubNav):
            continue
        if it.file_name in seen:
            continue
        seen.add(it.file_name)
        docs.append(it)
    # 兜底：若 spine 为空，使用所有 ITEM_DOCUMENT
    if not docs:
        for it in eb.get_items():
            if it.get_type() == ITEM_DOCUMENT and not isinstance(it, epub.EpubNav):
                if it.file_name not in seen:
                    seen.add(it.file_name)
                    docs.append(it)
    return docs


def _doc_title(doc) -> str:
    """从 XHTML 文档中提取标题：优先 <title>，回退到首个 h1/h2/h3 文本。"""
    try:
        from lxml import html as _lhtml
        root = _lhtml.fromstring(doc.get_content())
    except Exception:
        return ""
    for tag in ("title", "h1", "h2", "h3"):
        el = root.find(f".//{{*}}{tag}")
        if el is None:
            el = root.find(f".//{tag}")
        if el is not None:
            text = " ".join((el.text_content() or "").split()).strip()
            if text:
                return text
    return ""


def _looks_like_toc_page(doc, spine_names: set[str]) -> bool:
    """启发式判定文档是否是目录页：大部分 <a> 链接都指向其他 spine 文档。"""
    try:
        from lxml import html as _lhtml
        root = _lhtml.fromstring(doc.get_content())
    except Exception:
        return False
    anchors = root.findall(".//a") + root.findall(".//{*}a")
    if len(anchors) < 3:
        return False
    hits = 0
    for a in anchors:
        href = (a.get("href") or "").split("#", 1)[0]
        if not href:
            continue
        name = Path(href).name
        if name and name != Path(doc.file_name).name and name in spine_names:
            hits += 1
    return hits >= max(3, len(anchors) // 2)


def _flatten_toc(toc, base_level: int = 0) -> list[dict]:
    """把 ebooklib 的 toc 元组结构拍平成 [{href, title, level}, ...]。"""
    out: list[dict] = []
    if not toc:
        return out
    for entry in toc:
        if isinstance(entry, tuple):
            # (Section/Link, [children])
            head, children = entry[0], entry[1]
            if isinstance(head, epub.Link):
                out.append({"href": head.href, "title": head.title, "level": base_level})
            elif isinstance(head, epub.Section):
                out.append({"href": getattr(head, "href", "") or "", "title": head.title, "level": base_level})
            out.extend(_flatten_toc(children, base_level + 1))
        elif isinstance(entry, epub.Link):
            out.append({"href": entry.href, "title": entry.title, "level": base_level})
        elif isinstance(entry, epub.Section):
            out.append({"href": getattr(entry, "href", "") or "", "title": entry.title, "level": base_level})
    return out


def _normalize_href(href: str) -> str:
    if not href:
        return ""
    # 去掉 fragment，仅留文件名
    href = href.split("#", 1)[0]
    return Path(href).name


def _extract_body_html(it, href_to_filename: dict[str, str]) -> str:
    """读取 EPUB 中一个 XHTML 文档的 body 子节点 HTML 片段。

    用 lxml.etree 严格解析；图片 src 重写为归集后的相对路径；剥掉 <link rel=stylesheet>。
    """
    try:
        tree = parse_xhtml_bytes(it.get_content())
    except Exception:
        # 个别 EPUB 内 XHTML 不严格 -> 退化到 lxml.html 容错解析
        from lxml import html as lhtml
        root = lhtml.fromstring(it.get_content())
        body = root.find("body") if root.tag != "body" else root
        if body is None:
            return ""
        for link in list(body.iter("link")):
            link.getparent().remove(link)
        for img in body.iter("img"):
            src_attr = img.get("src", "")
            key = _HREF_PREFIX_RE.sub("", src_attr)
            target = href_to_filename.get(key) or href_to_filename.get(Path(key).name)
            if target:
                img.set("src", target)
        from .xhtml_utils import _serialize_html_to_xhtml, _escape_text
        parts: list[str] = []
        if body.text:
            parts.append(_escape_text(body.text))
        for c in body:
            parts.append(_serialize_html_to_xhtml(c))
            if c.tail:
                parts.append(_escape_text(c.tail))
        return "".join(parts).strip()

    body = get_body(tree)
    if body is None:
        return ""
    # 移除外部样式表链接
    for link in list(body.iter(f"{{{XHTML_NS}}}link")) + list(body.iter("link")):
        parent = link.getparent()
        if parent is not None:
            parent.remove(link)
    # 重写图片 src
    for img in list(body.iter(f"{{{XHTML_NS}}}img")) + list(body.iter("img")):
        src_attr = img.get("src", "")
        key = _HREF_PREFIX_RE.sub("", src_attr)
        target = href_to_filename.get(key) or href_to_filename.get(Path(key).name)
        if target:
            img.set("src", target)
    return serialize_body_inner(body)


def _first_heading(_root) -> Optional[str]:
    return None
