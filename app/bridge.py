"""QWebChannel JS-Python 桥。

JS 侧通过 `qt.webChannelTransport` + `qwebchannel.js` 拿到名为 `bridge` 的对象，
所有 @Slot 装饰的方法都可被 JS 调用；signals 也可在 JS 端 `connect`。

为了规避大字符串经 QWebChannel 的开销，图片数据采用 data URL（编辑期内存内引用）。
"""
from __future__ import annotations

import base64
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot


class EditorBridge(QObject):
    # JS -> Python: 章节正文 HTML 已变更
    htmlChanged = Signal(str, str)  # chapter_id, html
    # JS -> Python: 当前章节内提取出的标题列表（大纲）
    outlineChanged = Signal(str)  # JSON 字符串
    # JS -> Python: 用户在编辑器内插入了图片（base64 dataURL），希望 Python 把它落地为资源
    imageDropped = Signal(str, str)  # dataURL, suggested_filename
    # JS -> Python: 由此分章 —— 把当前章节切分为两段
    splitRequested = Signal(str, str, str)  # chapter_id, before_html, after_html
    # JS -> Python: 一张图片被从章节中删除（src 是落地后的相对路径，例如 images/x.png）
    imageRemoved = Signal(str)
    # Python -> JS: 加载某章节内容
    loadChapter = Signal(str, str, str)  # chapter_id, title, body_html
    # Python -> JS: 切换编辑器主题
    setEditorTheme = Signal(str)  # light | sepia | dark
    # Python -> JS: 切换聚焦/打字机模式
    setFocusMode = Signal(bool)
    setTypewriterMode = Signal(bool)
    # Python -> JS: 通知保存完成、清除 dirty
    savedOk = Signal()
    # Python -> JS: 在当前位置插入一张图片（src 直接传可被 WebEngine 解析的 URL，如 book://res/x.png）
    insertImageAt = Signal(str, str)  # src, alt
    # Python -> JS: 注入自定义字体的 @font-face
    addFontFace = Signal(str, str)  # font_family, src_url
    # Python -> JS: 排版参数（行距 / 上下左右页边距 mm）
    setTypography = Signal(float, int, int, int, int)

    def __init__(self) -> None:
        super().__init__()

    # ============ 由 JS 调用的槽 ============
    @Slot(str, str)
    def reportHtml(self, chapter_id: str, html: str) -> None:
        self.htmlChanged.emit(chapter_id, html)

    @Slot(str)
    def reportOutline(self, outline_json: str) -> None:
        self.outlineChanged.emit(outline_json)

    @Slot(str, str)
    def reportImageDropped(self, data_url: str, suggested_filename: str) -> None:
        self.imageDropped.emit(data_url, suggested_filename)

    @Slot(str, str, str)
    def reportSplit(self, chapter_id: str, before_html: str, after_html: str) -> None:
        self.splitRequested.emit(chapter_id, before_html, after_html)

    @Slot(str)
    def reportImageRemoved(self, src: str) -> None:
        self.imageRemoved.emit(src)


def split_data_url(data_url: str):
    """把 'data:image/png;base64,xxx' 解析为 (bytes, ext)。"""
    if not data_url.startswith("data:"):
        return None
    try:
        header, b64 = data_url.split(",", 1)
        mime = header.split(";")[0]
        if mime.startswith("data:"):
            mime = mime[len("data:"):]
        if not mime.startswith("image/"):
            return None
        ext = mime.split("/", 1)[1]
        return base64.b64decode(b64), ext
    except Exception:
        return None
