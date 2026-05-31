"""自定义 URL scheme：book:// ，把 Book 的资源（图片/字体）暴露给 QWebEngine。

约定：
- `book://res/<filename>` → Book.resources 里 filename 匹配的项
- `book://font/<filename>` → Book.custom_fonts 里 filename 匹配的项

资源由 Python 内存提供，避免落盘；切换书籍只需替换当前 Book 引用。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
from PySide6.QtWebEngineCore import (
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
    QWebEngineUrlRequestJob,
)


SCHEME_NAME = b"book"


def _sniff_image_mime(data: bytes) -> bytes:
    """从字节魔数判断图片真实 MIME，避免 media_type 错配导致解码失败。"""
    if not data:
        return b"application/octet-stream"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return b"image/png"
    if data[:3] == b"\xff\xd8\xff":
        return b"image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return b"image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return b"image/webp"
    if data[:4] == b"<svg" or data[:5] == b"<?xml":
        return b"image/svg+xml"
    if data[:2] == b"BM":
        return b"image/bmp"
    return b""


def register_scheme() -> None:
    """必须在 QApplication 构造前调用一次。"""
    if QWebEngineUrlScheme.schemeByName(SCHEME_NAME).name():
        return  # 已注册
    s = QWebEngineUrlScheme(SCHEME_NAME)
    # Host 语法：book://res/file.png 解析出 host='res', path='/file.png'
    s.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    s.setFlags(
        QWebEngineUrlScheme.SecureScheme
        | QWebEngineUrlScheme.LocalScheme            # 与 file:// 同等本地权限
        | QWebEngineUrlScheme.LocalAccessAllowed     # 允许本地资源访问它
        | QWebEngineUrlScheme.CorsEnabled
        | QWebEngineUrlScheme.ContentSecurityPolicyIgnored
    )
    QWebEngineUrlScheme.registerScheme(s)


class BookSchemeHandler(QWebEngineUrlSchemeHandler):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._book = None  # 由 MainWindow 注入

    def attach_book(self, book) -> None:
        self._book = book

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
        url: QUrl = job.requestUrl()
        host = url.host() or ""
        path = (url.path() or "").lstrip("/")
        # 兜底：万一 host 为空（某些 Qt 版本），从 toString 解析
        if not host or not path:
            full = url.toString()
            if full.startswith("book://"):
                rest = full[len("book://"):]
                if "/" in rest:
                    h, p = rest.split("/", 1)
                    host = host or h
                    path = path or p
        data: Optional[bytes] = None
        mime: bytes = b"application/octet-stream"
        book = self._book
        if book is None:
            job.fail(QWebEngineUrlRequestJob.UrlNotFound)
            return
        if host == "res":
            for r in book.resources:
                if r.filename == path:
                    data = r.data
                    sniffed = _sniff_image_mime(data)
                    if sniffed:
                        mime = sniffed
                    else:
                        mime = (r.media_type or "application/octet-stream").encode("ascii")
                    break
        elif host == "font":
            for f in getattr(book, "custom_fonts", []) or []:
                if f.get("filename") == path:
                    data = f.get("data")
                    mime = (f.get("media_type") or "font/ttf").encode("ascii")
                    break
        if data is None:
            job.fail(QWebEngineUrlRequestJob.UrlNotFound)
            return
        buf = QBuffer(parent=job)
        buf.setData(QByteArray(data))
        buf.open(QIODevice.ReadOnly)
        job.reply(mime, buf)
