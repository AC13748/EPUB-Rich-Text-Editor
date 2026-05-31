"""导出主题 CSS。

三套主题：经典书籍 / 现代简约 / 学术。
"""
from __future__ import annotations


_BASE = """
@charset "utf-8";
html, body { margin: 0; padding: 0; }
body {
    font-family: %(font_family)s;
    line-height: %(line_height)s;
    margin: %(page_margin)s;
    -webkit-hanging-punctuation: allow-end;
    hanging-punctuation: allow-end;
    text-spacing: trim-start trim-end;
    word-break: normal;
    overflow-wrap: anywhere;
}
p { margin: 0 0 1em 0; text-indent: 2em; }
h1, h2, h3, h4 { font-weight: 700; line-height: 1.4; margin: 1.4em 0 0.8em; text-indent: 0; }
h1 { font-size: 1.8em; }
h2 { font-size: 1.45em; }
h3 { font-size: 1.2em; }
blockquote {
    margin: 1em 0;
    padding: 0.4em 1em;
    border-left: 3px solid #888;
    color: #444;
    background: #f7f7f7;
    text-indent: 0;
}
pre {
    background: #1e1e1e;
    color: #f8f8f2;
    padding: 0.8em 1em;
    border-radius: 4px;
    overflow-x: auto;
    font-family: "JetBrains Mono", Consolas, monospace;
    line-height: 1.5;
    text-indent: 0;
}
code { font-family: "JetBrains Mono", Consolas, monospace; background: #f0f0f0; padding: 0 0.3em; border-radius: 3px; }
ul, ol { margin: 0 0 1em 1.6em; }
li { margin: 0.2em 0; }
img { max-width: 100%%; height: auto; }
figure { margin: 1.2em 0; text-align: center; text-indent: 0; }
figcaption { color: #666; font-size: 0.9em; margin-top: 0.4em; }
hr { border: none; border-top: 1px solid #ccc; margin: 2em 20%%; }
table { border-collapse: collapse; margin: 1em auto; }
th, td { border: 1px solid #888; padding: 0.4em 0.8em; }
a { color: #0a66c2; text-decoration: underline; }
mark { background: #fff3a3; }
"""

_CLASSIC = """
body { color: #1a1a1a; background: #fdfaf3; }
h1, h2, h3 { font-family: "Source Han Serif", "Noto Serif CJK SC", serif; }
h1 { text-align: center; border-bottom: 1px solid #c9b88a; padding-bottom: 0.3em; }
"""

_MODERN = """
body { color: #222; background: #ffffff; font-family: "Source Han Sans", "Noto Sans CJK SC", sans-serif; }
p { text-indent: 0; margin: 0 0 1.1em 0; }
h1, h2, h3 { font-family: inherit; }
h1::before { content: ""; display: block; width: 2em; height: 3px; background: #2563eb; margin-bottom: 0.4em; }
"""

_ACADEMIC = """
body { color: #111; background: #fff; font-family: "Source Han Serif", "Times New Roman", serif; }
h1 { text-align: center; }
p { text-indent: 2em; }
blockquote { font-size: 0.95em; }
"""

_THEMES = {
    "classic": _CLASSIC,
    "modern": _MODERN,
    "academic": _ACADEMIC,
}


def build_export_css(book) -> str:
    base = _BASE % {
        "font_family": book.font_family,
        "line_height": book.line_height,
        "page_margin": book.page_margin,
    }
    # 自定义字体 @font-face
    fonts_css = ""
    for f in getattr(book, "custom_fonts", []) or []:
        fmt_map = {"font/ttf": "truetype", "font/otf": "opentype",
                   "font/woff": "woff", "font/woff2": "woff2"}
        fmt = fmt_map.get(f.get("media_type", ""), "truetype")
        family = f.get("name") or f.get("filename", "custom")
        filename = f.get("filename", "")
        fonts_css += (
            f'@font-face {{ font-family: "{family}"; '
            f'src: url("../fonts/{filename}") format("{fmt}"); '
            f'font-display: swap; }}\n'
        )
    # 精细页边距：@page + body padding fallback
    mt, mb, ml, mr = (
        getattr(book, "margin_top_mm", 18),
        getattr(book, "margin_bottom_mm", 18),
        getattr(book, "margin_left_mm", 22),
        getattr(book, "margin_right_mm", 22),
    )
    geom = (
        f"@page {{ margin: {mt}mm {mr}mm {mb}mm {ml}mm; }}\n"
        f"body {{ padding: {mt}mm {mr}mm {mb}mm {ml}mm; box-sizing: border-box; }}\n"
    )
    return fonts_css + base + _THEMES.get(book.export_theme, _CLASSIC) + geom
