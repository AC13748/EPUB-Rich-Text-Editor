"""XHTML 解析/序列化辅助。

策略：
- EPUB 包内的 XHTML 文件按严格 XML 解析（lxml.etree），保留命名空间，自闭合标签合规；
- 用户粘贴/编辑器输出的 HTML 片段先按 lxml.html 容错解析，再转成 XHTML 序列化；
- BeautifulSoup 仅用于脏 HTML 的兜底清洗。
"""
from __future__ import annotations

from typing import Optional

from lxml import etree, html as lhtml


XHTML_NS = "http://www.w3.org/1999/xhtml"
NSMAP_XHTML = {None: XHTML_NS}

# HTML5 中的 void 元素：序列化时统一自闭合
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def parse_xhtml_bytes(data: bytes) -> etree._ElementTree:
    """严格按 XML 解析 EPUB 中的 XHTML 文件。"""
    parser = etree.XMLParser(
        recover=True, remove_blank_text=False, resolve_entities=False, ns_clean=False, huge_tree=True
    )
    return etree.ElementTree(etree.fromstring(data, parser))


def get_body(tree: etree._ElementTree) -> Optional[etree._Element]:
    root = tree.getroot()
    if root is None:
        return None
    body = root.find(f"{{{XHTML_NS}}}body")
    if body is None:
        # 某些 EPUB 文件不带命名空间
        body = root.find("body")
    return body


def localname(el: etree._Element) -> str:
    tag = el.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def serialize_body_inner(body: etree._Element) -> str:
    """把 body 的子节点序列化为 XHTML 片段字符串（去除命名空间前缀）。"""
    parts: list[str] = []
    if body.text:
        parts.append(_escape_text(body.text))
    for child in body:
        parts.append(_serialize_element(child))
        if child.tail:
            parts.append(_escape_text(child.tail))
    return "".join(parts).strip()


def _serialize_element(el: etree._Element) -> str:
    # 跳过注释、处理指令、CDATA 等非元素节点
    if not isinstance(el.tag, str):
        return ""
    name = localname(el)
    if not name:
        return ""
    # 属性：保留原样，剥离命名空间前缀
    attrs: list[str] = []
    for k, v in el.attrib.items():
        ak = k.split("}", 1)[1] if isinstance(k, str) and k.startswith("{") else k
        if not isinstance(ak, str) or ak.startswith("xmlns"):
            continue
        attrs.append(f' {ak}="{_escape_attr(v if v is not None else "")}"')
    attr_str = "".join(attrs)
    # 子内容
    inner: list[str] = []
    if el.text:
        inner.append(_escape_text(el.text))
    for c in el:
        inner.append(_serialize_element(c))
        if c.tail:
            inner.append(_escape_text(c.tail))
    inner_str = "".join(inner)

    if not inner_str and name in VOID_ELEMENTS:
        return f"<{name}{attr_str}/>"
    return f"<{name}{attr_str}>{inner_str}</{name}>"


def _escape_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(s: Optional[str]) -> str:
    return _escape_text(s).replace('"', "&quot;")


def html_fragment_to_xhtml(fragment: str) -> str:
    """把可能不够规范的 HTML 片段（编辑器输出）转成合规 XHTML 片段。"""
    if not fragment or not fragment.strip():
        return ""
    # lxml.html 容错解析；包一层 div 以便统一拿到 children
    wrapped = f"<div>{fragment}</div>"
    try:
        root = lhtml.fromstring(wrapped)
    except Exception:
        return fragment
    parts: list[str] = []
    if root.text:
        parts.append(_escape_text(root.text))
    for c in root:
        parts.append(_serialize_html_to_xhtml(c))
        if c.tail:
            parts.append(_escape_text(c.tail))
    return "".join(parts)


def _serialize_html_to_xhtml(el) -> str:
    # 跳过注释 / 处理指令 / 非元素节点
    if not isinstance(el.tag, str):
        return ""
    name = el.tag
    if not name:
        return ""
    attrs: list[str] = []
    for k, v in el.attrib.items():
        if not isinstance(k, str):
            continue
        attrs.append(f' {k}="{_escape_attr(v if v is not None else "")}"')
    attr_str = "".join(attrs)
    inner: list[str] = []
    if el.text:
        inner.append(_escape_text(el.text))
    for c in el:
        inner.append(_serialize_html_to_xhtml(c))
        if c.tail:
            inner.append(_escape_text(c.tail))
    inner_str = "".join(inner)
    if not inner_str and name in VOID_ELEMENTS:
        return f"<{name}{attr_str}/>"
    return f"<{name}{attr_str}>{inner_str}</{name}>"
