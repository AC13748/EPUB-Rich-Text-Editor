/* ====================================================================
 * PySave Block Editor —— 块级 contenteditable 编辑器
 * 目标：Notion 风格的「块级 + 富文本」操作手感
 * ==================================================================== */

(function () {
"use strict";

// ===== 全局状态 =====
const state = {
    chapterId: null,
    bridge: null,
    suppressReport: false,
    reportTimer: null,
    slashAnchor: null,    // 触发斜杠菜单的块
    slashFilter: "",      // 斜杠菜单过滤词
    slashIndex: 0,
    dragSource: null,
    savedSelection: null,
};

const editor = document.getElementById("editor");
const page   = document.getElementById("page");
const toolbar = document.getElementById("floating-toolbar");
const handle  = document.getElementById("block-handle");
const slashMenu = document.getElementById("slash-menu");
const linkPop = document.getElementById("link-popover");
const linkInput = document.getElementById("link-input");

// ===================== QWebChannel 接入 =====================
new QWebChannel(qt.webChannelTransport, function (channel) {
    state.bridge = channel.objects.bridge;

    state.bridge.loadChapter.connect(function (cid, title, html) {
        state.chapterId = cid;
        state.suppressReport = true;
        editor.innerHTML = html && html.trim() ? html : "<p><br></p>";
        normalizeAll();
        state.suppressReport = false;
        editor.focus();
        placeCaretAtStart(editor);
        emitOutline();
    });
    state.bridge.setEditorTheme.connect(function (theme) {
        document.body.dataset.theme = theme || "light";
    });
    state.bridge.setFocusMode.connect(function (on) {
        document.body.classList.toggle("focus-mode", !!on);
        updateFocusedBlock();
    });
    state.bridge.setTypewriterMode.connect(function (on) {
        document.body.classList.toggle("typewriter", !!on);
    });
    state.bridge.insertImageAt.connect(function (src, alt) {
        insertImageBlock(src, alt || "");
    });
    state.bridge.addFontFace.connect(function (family, srcUrl) {
        injectFontFace(family, srcUrl);
        addFontOption(family);
    });
    state.bridge.setTypography.connect(function (lh, mt, mb, ml, mr) {
        applyTypography(lh, mt, mb, ml, mr);
    });
});

// ===================== 选区保存与恢复 =====================
function saveSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const r = sel.getRangeAt(0);
    if (!editor.contains(r.startContainer) && !editor.contains(r.endContainer)) return null;
    state.savedSelection = r.cloneRange();
    return state.savedSelection;
}

function restoreSelection() {
    const r = state.savedSelection;
    if (!r) return false;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
    return true;
}

// ===================== 文本对齐 =====================
const ALIGN_CMDS = { left: "left", center: "center", right: "right", justify: "justify" };

function applyAlignToCurrent(align) {
    if (!ALIGN_CMDS[align]) return;
    if (document.activeElement !== editor) restoreSelection();
    const blocks = blocksInSelection();
    blocks.forEach(b => {
        b.style.textAlign = align;
        b.dataset.align = align;
    });
    updateAlignButtons();
    scheduleReport();
}

function blocksInSelection() {
    const sel = window.getSelection();
    let r = (sel && sel.rangeCount) ? sel.getRangeAt(0) : state.savedSelection;
    if (!r) {
        const b = currentBlock(); return b ? [b] : [];
    }
    const start = blockOf(r.startContainer);
    const end = blockOf(r.endContainer);
    if (!start) return [];
    if (start === end) return [start];
    const out = [];
    let cur = start;
    while (cur) {
        out.push(cur);
        if (cur === end) break;
        cur = cur.nextElementSibling;
    }
    return out;
}

function updateAlignButtons() {
    const blk = currentBlock(); if (!blk) return;
    const cur = blk.style.textAlign || blk.dataset.align || "";
    document.querySelectorAll("#block-toolbar [data-align]").forEach(b => {
        b.classList.toggle("active", b.dataset.align === cur);
    });
}

document.addEventListener("DOMContentLoaded", wireBlockToolbar);
if (document.readyState !== "loading") wireBlockToolbar();

function wireBlockToolbar() {
    const tb = document.getElementById("block-toolbar"); if (!tb) return;
    // 工具栏内任意控件按下前先记下当前选区，事件处理时再恢复
    tb.addEventListener("mousedown", e => {
        saveSelection();
        if (e.target.tagName !== "INPUT" && e.target.tagName !== "SELECT") e.preventDefault();
    }, true);

    tb.querySelectorAll("[data-align]").forEach(btn => {
        btn.addEventListener("click", () => { restoreSelection(); applyAlignToCurrent(btn.dataset.align); });
    });
    const sizeSel = document.getElementById("font-size-select");
    sizeSel.addEventListener("mousedown", saveSelection, true);
    sizeSel.addEventListener("change", () => {
        restoreSelection();
        if (sizeSel.value) applyFontSize(parseFloat(sizeSel.value));
    });
    const sizeInput = document.getElementById("font-size-input");
    sizeInput.addEventListener("focus", saveSelection);
    sizeInput.addEventListener("change", () => {
        const v = parseFloat(sizeInput.value);
        restoreSelection();
        if (!isNaN(v) && v >= 6 && v <= 96) applyFontSize(Math.round(v * 10) / 10);
    });
    const famSel = document.getElementById("font-family-select");
    famSel.addEventListener("mousedown", saveSelection, true);
    rebuildSystemFontOptions();
    famSel.addEventListener("change", () => { restoreSelection(); applyFontFamily(famSel.value); });

    const lhSel = document.getElementById("line-height-select");
    if (lhSel) {
        lhSel.addEventListener("mousedown", saveSelection, true);
        lhSel.addEventListener("change", () => { restoreSelection(); applyLineHeight(parseFloat(lhSel.value)); });
    }
    const painter = document.getElementById("format-painter");
    if (painter) {
        painter.addEventListener("mousedown", saveSelection, true);
        painter.addEventListener("click", () => { restoreSelection(); toggleFormatPainter(); });
        painter.addEventListener("dblclick", () => { restoreSelection(); toggleFormatPainter(true); });
    }
}

// 快捷键：Ctrl+L / E / R / J
document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "l") { e.preventDefault(); applyAlignToCurrent("left"); }
    else if (k === "e") { e.preventDefault(); applyAlignToCurrent("center"); }
    else if (k === "r") { e.preventDefault(); applyAlignToCurrent("right"); }
    else if (k === "j") { e.preventDefault(); applyAlignToCurrent("justify"); }
});

editor.addEventListener("keyup", () => { updateAlignButtons(); updateInfoPopover(); reflectToolbarFromSelection(); });
editor.addEventListener("mouseup", () => { updateAlignButtons(); updateInfoPopover(); reflectToolbarFromSelection(); });

// ===================== 字号 =====================
function applyFontSize(pt) {
    if (!pt) return;
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const r = sel.getRangeAt(0);
    if (r.collapsed) {
        const blk = currentBlock(); if (!blk) return;
        blk.querySelectorAll("[style*='font-size']").forEach(el => {
            el.style.removeProperty("font-size");
            if (!el.getAttribute("style")) el.removeAttribute("style");
        });
        blk.style.fontSize = pt + "pt";
    } else {
        stripStyleInRange(r, ["font-size"]);
        styleSelection({ "font-size": pt + "pt" });
    }
    scheduleReport();
    updateInfoPopover();
}

function applyLineHeight(lh) {
    if (!lh || lh <= 0) return;
    const blocks = blocksInSelection();
    if (blocks.length === 0) {
        const b = currentBlock(); if (b) b.style.lineHeight = String(lh);
    } else {
        blocks.forEach(b => b.style.lineHeight = String(lh));
    }
    scheduleReport();
}

// 让选区文字应用 inline 样式：拆分跨节点情况下也能稳定工作
function styleSelection(styles) {
    const sel = window.getSelection(); if (!sel.rangeCount) return;
    const r = sel.getRangeAt(0); if (r.collapsed) return;
    // 先标记选区
    document.execCommand("styleWithCSS", false, true);
    // 用 fontName/fontSize execCommand 不够细粒度，我们手动 wrap
    const frag = r.extractContents();
    const span = document.createElement("span");
    Object.entries(styles).forEach(([k, v]) => span.style.setProperty(k, v));
    span.appendChild(frag);
    r.insertNode(span);
    // 重新选中包裹的内容
    const nr = document.createRange();
    nr.selectNodeContents(span);
    sel.removeAllRanges(); sel.addRange(nr);
}

// 兼容旧名
function wrapSelectionInSpan(styles) { return styleSelection(styles); }


// ===================== 字体 =====================
const SYSTEM_FONTS = [
    { name: "默认", family: "" },
    { name: "宋体 / Serif", family: "Source Han Serif, Noto Serif CJK SC, serif" },
    { name: "黑体 / Sans", family: "Source Han Sans, Noto Sans CJK SC, sans-serif" },
    { name: "等宽 / Mono", family: "JetBrains Mono, Consolas, monospace" },
];

function rebuildSystemFontOptions() {
    const sel = document.getElementById("font-family-select"); if (!sel) return;
    const customs = Array.from(sel.querySelectorAll('option[data-custom="1"]'))
        .map(o => ({ value: o.value, text: o.textContent }));
    sel.innerHTML = "";
    SYSTEM_FONTS.forEach(f => {
        const op = document.createElement("option");
        op.value = f.family; op.textContent = f.name;
        sel.appendChild(op);
    });
    if (customs.length) {
        const og = document.createElement("optgroup"); og.label = "自定义字体";
        customs.forEach(c => {
            const op = document.createElement("option");
            op.value = c.value; op.textContent = c.text; op.dataset.custom = "1";
            og.appendChild(op);
        });
        sel.appendChild(og);
    }
}

function addFontOption(family) {
    const sel = document.getElementById("font-family-select"); if (!sel) return;
    if (Array.from(sel.options).some(o => o.value === family)) return;
    const op = document.createElement("option");
    op.value = family; op.textContent = family; op.dataset.custom = "1";
    let og = sel.querySelector("optgroup[label='自定义字体']");
    if (!og) { og = document.createElement("optgroup"); og.label = "自定义字体"; sel.appendChild(og); }
    og.appendChild(op);
}

function injectFontFace(family, srcUrl) {
    const id = "ff-" + family.replace(/[^a-z0-9_-]/gi, "_");
    if (document.getElementById(id)) return;
    const style = document.createElement("style");
    style.id = id;
    style.textContent = `@font-face{font-family:"${family}";src:url("${srcUrl}");font-display:swap;}`;
    document.head.appendChild(style);
}

function applyFontFamily(family) {
    const sel = window.getSelection(); if (!sel.rangeCount) return;
    const r = sel.getRangeAt(0);
    if (r.collapsed) {
        const blk = currentBlock(); if (!blk) return;
        // 清掉块内所有内联 font-family，再设到块上，确保胜出
        blk.querySelectorAll("[style*='font-family']").forEach(el => {
            el.style.removeProperty("font-family");
            if (!el.getAttribute("style")) el.removeAttribute("style");
        });
        blk.style.fontFamily = family || "";
    } else {
        // 选区：先清子节点 font-family 再 wrap
        stripStyleInRange(r, ["font-family"]);
        if (family) styleSelection({ "font-family": family });
    }
    scheduleReport();
    updateInfoPopover();
}

// 移除选区内所有元素上指定的 inline 样式
function stripStyleInRange(range, props) {
    const root = range.commonAncestorContainer.nodeType === 1
        ? range.commonAncestorContainer
        : range.commonAncestorContainer.parentNode;
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll("[style]").forEach(el => {
        if (!range.intersectsNode(el)) return;
        props.forEach(p => el.style.removeProperty(p));
        if (!el.getAttribute("style")) el.removeAttribute("style");
    });
}

// ===================== 选区信息浮窗 =====================
function getEffectiveStyle(node) {
    let el = (node && node.nodeType === 1) ? node : (node ? node.parentNode : null);
    if (!el) return null;
    return window.getComputedStyle(el);
}

function fmtFamily(family) {
    if (!family) return "";
    // 取第一个 family，去引号
    return family.split(",")[0].trim().replace(/^["']|["']$/g, "");
}

function pxToPt(px) {
    const v = parseFloat(px);
    if (!v) return "";
    return (v * 0.75).toFixed(1).replace(/\.0$/, "") + "pt";
}

function updateInfoPopover() {
    const pop = document.getElementById("info-popover");
    if (!pop) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed || !editor.contains(sel.anchorNode)) {
        pop.classList.remove("visible");
        return;
    }
    const r = sel.getRangeAt(0);
    const rect = r.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) { pop.classList.remove("visible"); return; }
    const cs = getEffectiveStyle(r.startContainer);
    if (!cs) { pop.classList.remove("visible"); return; }
    const family = fmtFamily(cs.fontFamily);
    const size = pxToPt(cs.fontSize);
    const weight = (parseInt(cs.fontWeight, 10) >= 600) ? " 粗" : "";
    const italic = (cs.fontStyle === "italic") ? " 斜" : "";
    const blk = blockOf(r.startContainer);
    const lh = blk ? (blk.style.lineHeight || cs.lineHeight) : cs.lineHeight;
    const align = (blk && (blk.style.textAlign || blk.dataset.align)) || cs.textAlign || "默认";
    pop.innerHTML =
        `<span class="k">字体</span><span class="v">${escapeHtml(family || "默认")}</span>` +
        `<span class="k">字号</span><span class="v">${size || "—"}${weight}${italic}</span>` +
        `<span class="k">行距</span><span class="v">${escapeHtml(String(lh || "默认"))}</span>` +
        `<span class="k">对齐</span><span class="v">${escapeHtml(align)}</span>`;
    pop.style.left = (window.scrollX + rect.left + rect.width / 2) + "px";
    pop.style.top  = (window.scrollY + rect.bottom + 8) + "px";
    pop.classList.add("visible");
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

// 让顶部工具栏的 select 跟着选区变化
function reflectToolbarFromSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const r = sel.getRangeAt(0);
    const cs = getEffectiveStyle(r.startContainer);
    if (!cs) return;
    const sizeSel = document.getElementById("font-size-select");
    const sizeInput = document.getElementById("font-size-input");
    const famSel = document.getElementById("font-family-select");
    const lhSel = document.getElementById("line-height-select");
    const ptStr = pxToPt(cs.fontSize);
    if (sizeSel) {
        const ptNum = parseFloat(ptStr);
        const opt = Array.from(sizeSel.options).find(o => parseFloat(o.value) === ptNum);
        sizeSel.value = opt ? opt.value : "";
        if (sizeInput) sizeInput.value = opt ? "" : (ptNum || "");
    }
    if (famSel) {
        const cur = fmtFamily(cs.fontFamily);
        const match = Array.from(famSel.options).find(o => fmtFamily(o.value) === cur);
        if (match) famSel.value = match.value;
    }
    if (lhSel) {
        const blk = blockOf(r.startContainer);
        const v = blk ? blk.style.lineHeight : "";
        const opt = Array.from(lhSel.options).find(o => o.value === v);
        lhSel.value = opt ? opt.value : "";
    }
}

// ===================== 格式刷 =====================
const painterState = {
    active: false,
    sticky: false,    // 双击 = 持续模式
    style: null,      // {fontFamily, fontSize, fontWeight, fontStyle, color, ...}
    blockStyle: null, // {textAlign, lineHeight}
};

const PAINTER_INLINE_PROPS = [
    "font-family", "font-size", "font-weight", "font-style",
    "color", "background-color", "text-decoration",
];

function captureStyleAtSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const cs = getEffectiveStyle(sel.getRangeAt(0).startContainer);
    if (!cs) return null;
    const inline = {};
    PAINTER_INLINE_PROPS.forEach(p => {
        const v = cs.getPropertyValue(p);
        if (v) inline[p] = v;
    });
    const blk = blockOf(sel.getRangeAt(0).startContainer);
    const block = blk ? {
        "text-align": blk.style.textAlign || cs.textAlign || "",
        "line-height": blk.style.lineHeight || "",
    } : null;
    return { inline, block };
}

function toggleFormatPainter(sticky=false) {
    const btn = document.getElementById("format-painter");
    if (painterState.active && !sticky) {
        painterState.active = false; painterState.sticky = false;
        btn && btn.classList.remove("active", "sticky");
        document.body.style.cursor = "";
        return;
    }
    const captured = captureStyleAtSelection();
    if (!captured) return;
    painterState.style = captured.inline;
    painterState.blockStyle = captured.block;
    painterState.active = true;
    painterState.sticky = !!sticky;
    btn && btn.classList.add("active");
    if (sticky) btn && btn.classList.add("sticky");
    document.body.style.cursor = "cell";
}

function applyPainter() {
    if (!painterState.active || !painterState.style) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
    const r = sel.getRangeAt(0);
    // inline 样式
    const styles = {};
    Object.entries(painterState.style).forEach(([k, v]) => { if (v) styles[k] = v; });
    stripStyleInRange(r, Object.keys(styles));
    if (Object.keys(styles).length) styleSelection(styles);
    // 块级样式
    if (painterState.blockStyle) {
        blocksInSelection().forEach(b => {
            if (painterState.blockStyle["text-align"]) {
                b.style.textAlign = painterState.blockStyle["text-align"];
                b.dataset.align = painterState.blockStyle["text-align"];
            }
            if (painterState.blockStyle["line-height"]) {
                b.style.lineHeight = painterState.blockStyle["line-height"];
            }
        });
    }
    scheduleReport();
    if (!painterState.sticky) toggleFormatPainter();
}

editor.addEventListener("mouseup", () => {
    if (painterState.active) {
        // 略等一下让 selection 稳定
        setTimeout(applyPainter, 0);
    }
});

// Esc 退出格式刷
document.addEventListener("keydown", e => {
    if (e.key === "Escape" && painterState.active) toggleFormatPainter();
});

// ===================== Word 风格手感 =====================
// Tab / Shift+Tab：调整段落首行缩进（每次 2em）
editor.addEventListener("keydown", function (e) {
    if (e.key !== "Tab") return;
    const blocks = blocksInSelection();
    if (!blocks.length) return;
    e.preventDefault();
    const delta = e.shiftKey ? -2 : 2;
    blocks.forEach(b => {
        if (b.tagName === "LI") {
            // 列表保持原 contenteditable 行为：indent / outdent
            return;
        }
        const cur = parseFloat(b.style.textIndent) || 2; // 默认中文缩进 2em
        const next = Math.max(0, cur + delta);
        b.style.textIndent = next + "em";
    });
    scheduleReport();
});

// Ctrl + 鼠标滚轮：缩放（Word 风格）
let zoomLevel = 1.0;
function setZoom(z) {
    zoomLevel = Math.min(3, Math.max(0.5, z));
    const pageEl = document.getElementById("page");
    if (pageEl) {
        pageEl.style.transform = `scale(${zoomLevel})`;
        pageEl.style.transformOrigin = "top center";
    }
}
document.addEventListener("wheel", function (e) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setZoom(zoomLevel + (e.deltaY < 0 ? 0.1 : -0.1));
}, { passive: false });
document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.key === "0") { e.preventDefault(); setZoom(1); }
    else if (e.key === "=" || e.key === "+") { e.preventDefault(); setZoom(zoomLevel + 0.1); }
    else if (e.key === "-") { e.preventDefault(); setZoom(zoomLevel - 0.1); }
});

// 输入时强制 styleWithCSS，让 Bold/Italic 生成 <span style> 而非 <b>/<i>，便于和我们的 inline 模型一致
try { document.execCommand("styleWithCSS", false, true); } catch (_) {}

// 让 Backspace 在空段开头时合并到上一段（Word 行为）
editor.addEventListener("keydown", function (e) {
    if (e.key !== "Backspace") return;
    const sel = window.getSelection();
    if (!sel.rangeCount || !sel.isCollapsed) return;
    const r = sel.getRangeAt(0);
    const blk = blockOf(r.startContainer);
    if (!blk || r.startOffset !== 0) return;
    // 必须在段首
    if (!isAtBlockStart(r, blk)) return;
    const prev = blk.previousElementSibling;
    if (!prev || prev.tagName === "FIGURE" || prev.tagName === "HR") return;
    e.preventDefault();
    const caretAt = prev.lastChild;
    const range = document.createRange();
    if (caretAt) {
        range.setStartAfter(caretAt); range.collapse(true);
    } else {
        range.selectNodeContents(prev); range.collapse(false);
    }
    while (blk.firstChild) prev.appendChild(blk.firstChild);
    blk.remove();
    sel.removeAllRanges(); sel.addRange(range);
    scheduleReport();
});

function isAtBlockStart(range, blk) {
    const pre = document.createRange();
    pre.selectNodeContents(blk);
    pre.setEnd(range.startContainer, range.startOffset);
    return pre.toString().length === 0;
}

// ===================== 排版（行距 / 页边距） =====================
function applyTypography(lineHeight, mt, mb, ml, mr) {
    const root = document.documentElement;
    if (lineHeight && lineHeight > 0) root.style.setProperty("--editor-line-height", String(lineHeight));
    const pageEl = document.getElementById("page");
    if (pageEl) {
        pageEl.style.paddingTop = mt + "mm";
        pageEl.style.paddingBottom = mb + "mm";
        pageEl.style.paddingLeft = ml + "mm";
        pageEl.style.paddingRight = mr + "mm";
    }
}

// ===================== 块级模型工具 =====================
const BLOCK_TAGS = new Set(["P", "H1", "H2", "H3", "H4", "BLOCKQUOTE", "PRE", "UL", "OL", "FIGURE", "HR", "TABLE"]);

function blockOf(node) {
    while (node && node !== editor) {
        if (node.nodeType === 1 && BLOCK_TAGS.has(node.tagName)) return node;
        node = node.parentNode;
    }
    return null;
}

function currentBlock() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return null;
    return blockOf(sel.getRangeAt(0).startContainer);
}

function placeCaretAtEnd(el) {
    el.focus();
    const r = document.createRange();
    r.selectNodeContents(el);
    r.collapse(false);
    const s = window.getSelection();
    s.removeAllRanges(); s.addRange(r);
}

function placeCaretAtStart(el) {
    el.focus();
    const r = document.createRange();
    r.selectNodeContents(el);
    r.collapse(true);
    const s = window.getSelection();
    s.removeAllRanges(); s.addRange(r);
}

function normalizeAll() {
    if (!editor.firstElementChild) editor.innerHTML = "<p><br></p>";
    Array.from(editor.children).forEach(updateEmptyHint);
}

function updateEmptyHint(block) {
    if (block.tagName === "P") {
        const txt = block.textContent.replace(/​/g, "").trim();
        if (txt === "") block.dataset.empty = "true";
        else delete block.dataset.empty;
    } else {
        delete block.dataset.empty;
    }
}

// ===================== 输入与变更监听 =====================
editor.addEventListener("input", function (e) {
    // 行内 Markdown 触发
    handleMarkdownShortcuts();
    // 维护空段提示
    const blk = currentBlock();
    if (blk) updateEmptyHint(blk);
    scheduleReport();
    if (slashMenu.classList.contains("visible")) updateSlashFilter();
});

editor.addEventListener("keydown", function (e) {
    if (slashMenu.classList.contains("visible")) {
        if (e.key === "ArrowDown") { e.preventDefault(); moveSlash(1); return; }
        if (e.key === "ArrowUp")   { e.preventDefault(); moveSlash(-1); return; }
        if (e.key === "Enter") { e.preventDefault(); pickSlashCurrent(); return; }
        if (e.key === "Escape") { hideSlash(); return; }
    }

    // Ctrl+B/I/U/K
    if (e.ctrlKey || e.metaKey) {
        const k = e.key.toLowerCase();
        if (k === "b") { e.preventDefault(); cmdInline("bold"); return; }
        if (k === "i") { e.preventDefault(); cmdInline("italic"); return; }
        if (k === "u") { e.preventDefault(); cmdInline("underline"); return; }
        if (k === "k") { e.preventDefault(); openLinkPopover(); return; }
        if (e.shiftKey && k === "v") { /* 由 paste 处理 */ }
    }

    // Shift+Enter = 软换行
    if (e.key === "Enter" && e.shiftKey) {
        e.preventDefault();
        document.execCommand("insertLineBreak");
        return;
    }

    // 斜杠：在空块或行首输入 / 时打开菜单
    if (e.key === "/") {
        const blk = currentBlock();
        if (blk && isCaretAtBlockStart(blk)) {
            // 等输入完成再打开
            setTimeout(openSlashMenu, 0);
        }
    }

    // Enter 在 PRE 中不分块
    if (e.key === "Enter") {
        const blk = currentBlock();
        if (blk && blk.tagName === "PRE") {
            e.preventDefault();
            document.execCommand("insertText", false, "\n");
            return;
        }
        // Enter 在标题/引用末尾：分到普通段
        if (blk && (/^H[1-4]$/.test(blk.tagName) || blk.tagName === "BLOCKQUOTE") && isCaretAtBlockEnd(blk)) {
            e.preventDefault();
            const p = document.createElement("p");
            p.innerHTML = "<br>";
            blk.after(p);
            placeCaretAtStart(p);
            updateEmptyHint(p);
            scheduleReport();
            return;
        }
    }

    // Backspace 在空块开头：把当前块降级为段落
    if (e.key === "Backspace") {
        const blk = currentBlock();
        if (blk && isCaretAtBlockStart(blk) && blk.textContent.trim() === "") {
            if (blk.tagName !== "P") {
                e.preventDefault();
                convertBlock(blk, "p");
                return;
            }
            // 删除空段并跳到上一块末尾
            const prev = blk.previousElementSibling;
            if (prev) {
                e.preventDefault();
                blk.remove();
                placeCaretAtEnd(prev);
                scheduleReport();
                return;
            }
        }
    }
});

editor.addEventListener("keyup", function () {
    syncFloatingToolbar();
    updateFocusedBlock();
});

editor.addEventListener("mouseup", syncFloatingToolbar);
document.addEventListener("selectionchange", syncFloatingToolbar);

// 失焦时关闭浮动 UI
document.addEventListener("mousedown", function (e) {
    if (!toolbar.contains(e.target) && !linkPop.contains(e.target) && !slashMenu.contains(e.target)) {
        // 不立刻关浮动工具栏，selectionchange 会处理
        if (!slashMenu.contains(e.target)) hideSlash();
        if (!linkPop.contains(e.target) && e.target !== editor && !editor.contains(e.target)) hideLinkPopover();
    }
});

// ===================== 浮动工具栏 =====================
function syncFloatingToolbar() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0 || !editor.contains(sel.anchorNode)) {
        toolbar.classList.remove("visible");
        return;
    }
    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) {
        toolbar.classList.remove("visible");
        return;
    }
    toolbar.style.left = (window.scrollX + rect.left + rect.width / 2) + "px";
    toolbar.style.top  = (window.scrollY + rect.top) + "px";
    toolbar.classList.add("visible");
    // 更新按钮 active 状态
    toolbar.querySelectorAll("button").forEach(function (btn) {
        const cmd = btn.dataset.cmd;
        let on = false;
        if (cmd === "bold") on = document.queryCommandState("bold");
        else if (cmd === "italic") on = document.queryCommandState("italic");
        else if (cmd === "underline") on = document.queryCommandState("underline");
        else if (cmd === "strike") on = document.queryCommandState("strikeThrough");
        btn.classList.toggle("active", !!on);
    });
}

toolbar.addEventListener("mousedown", function (e) { e.preventDefault(); });
toolbar.addEventListener("click", function (e) {
    const btn = e.target.closest("button");
    if (!btn) return;
    const cmd = btn.dataset.cmd;
    if (cmd === "bold" || cmd === "italic" || cmd === "underline") cmdInline(cmd);
    else if (cmd === "strike") cmdInline("strikeThrough");
    else if (cmd === "code") wrapInlineTag("code");
    else if (cmd === "hl") wrapInlineTag("mark");
    else if (cmd === "link") openLinkPopover();
    else if (cmd === "clear") {
        document.execCommand("removeFormat");
        document.execCommand("unlink");
    }
    syncFloatingToolbar();
    scheduleReport();
});

function cmdInline(cmd) { document.execCommand(cmd, false, null); }

function wrapInlineTag(tag) {
    const sel = window.getSelection();
    if (!sel.rangeCount || sel.isCollapsed) return;
    const range = sel.getRangeAt(0);
    // 简单实现：包裹选区
    const el = document.createElement(tag);
    try {
        el.appendChild(range.extractContents());
        range.insertNode(el);
        sel.removeAllRanges();
        const r = document.createRange();
        r.selectNodeContents(el); r.collapse(false);
        sel.addRange(r);
    } catch (err) { console.warn(err); }
}

// ===================== 链接气泡 =====================
function openLinkPopover() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    state.savedSelection = sel.getRangeAt(0).cloneRange();
    const rect = state.savedSelection.getBoundingClientRect();
    linkPop.style.left = (window.scrollX + rect.left) + "px";
    linkPop.style.top  = (window.scrollY + rect.bottom + 6) + "px";
    linkPop.classList.add("visible");
    // 取已有链接
    const a = nearestAnchor(sel.anchorNode);
    linkInput.value = a ? a.href : "";
    setTimeout(function () { linkInput.focus(); linkInput.select(); }, 0);
}
function hideLinkPopover() { linkPop.classList.remove("visible"); }

function nearestAnchor(node) {
    while (node && node !== editor) {
        if (node.nodeType === 1 && node.tagName === "A") return node;
        node = node.parentNode;
    }
    return null;
}

document.getElementById("link-apply").addEventListener("click", function () {
    const url = linkInput.value.trim();
    if (!url) return;
    if (state.savedSelection) {
        const sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(state.savedSelection);
    }
    document.execCommand("createLink", false, url);
    hideLinkPopover();
    scheduleReport();
});
document.getElementById("link-remove").addEventListener("click", function () {
    if (state.savedSelection) {
        const sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(state.savedSelection);
    }
    document.execCommand("unlink");
    hideLinkPopover();
    scheduleReport();
});
linkInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); document.getElementById("link-apply").click(); }
    if (e.key === "Escape") hideLinkPopover();
});

// ===================== 块级菜单（左侧 ⋮⋮ 手柄） =====================
let hoverBlock = null;
editor.addEventListener("mousemove", function (e) {
    let target = e.target;
    while (target && target !== editor && !(target.parentElement === editor)) target = target.parentElement;
    if (target && target.parentElement === editor) {
        hoverBlock = target;
        const rect = target.getBoundingClientRect();
        handle.style.left = (window.scrollX + rect.left - 26) + "px";
        handle.style.top  = (window.scrollY + rect.top + 4) + "px";
        handle.classList.add("visible");
    }
});
page.addEventListener("mouseleave", function () { handle.classList.remove("visible"); });

handle.addEventListener("mousedown", function (e) {
    if (!hoverBlock) return;
    if (e.button === 0) {
        // 左键：拖拽
        state.dragSource = hoverBlock;
        handle.classList.add("dragging");
        e.preventDefault();
    }
});
handle.addEventListener("contextmenu", function (e) { e.preventDefault(); });
handle.addEventListener("click", function (e) {
    if (!hoverBlock) return;
    // 单击展开斜杠菜单当作"块类型"切换
    state.slashAnchor = hoverBlock;
    state.slashFilter = "";
    showSlashMenuAtBlock(hoverBlock, true);
});

document.addEventListener("mousemove", function (e) {
    if (!state.dragSource) return;
    // 找到被悬停的块
    let target = document.elementFromPoint(e.clientX, e.clientY);
    while (target && target.parentElement !== editor) target = target.parentElement;
    Array.from(editor.children).forEach(function (c) {
        c.classList.remove("drop-target-before");
        c.classList.remove("drop-target-after");
    });
    if (target && target !== state.dragSource) {
        const rect = target.getBoundingClientRect();
        const before = (e.clientY - rect.top) < rect.height / 2;
        target.classList.add(before ? "drop-target-before" : "drop-target-after");
    }
});
document.addEventListener("mouseup", function (e) {
    if (!state.dragSource) return;
    let target = document.elementFromPoint(e.clientX, e.clientY);
    while (target && target.parentElement !== editor) target = target.parentElement;
    if (target && target !== state.dragSource) {
        const rect = target.getBoundingClientRect();
        const before = (e.clientY - rect.top) < rect.height / 2;
        if (before) target.before(state.dragSource);
        else target.after(state.dragSource);
        scheduleReport();
        emitOutline();
    }
    Array.from(editor.children).forEach(function (c) {
        c.classList.remove("drop-target-before"); c.classList.remove("drop-target-after");
    });
    state.dragSource = null;
    handle.classList.remove("dragging");
});

// ===================== 斜杠菜单 =====================
const SLASH_ITEMS = [
    { key: "p",   label: "段落",     desc: "普通文本",      icon: "¶",  type: "p"  },
    { key: "h1",  label: "标题 1",   desc: "章/部 大标题",   icon: "H1", type: "h1" },
    { key: "h2",  label: "标题 2",   desc: "节标题",         icon: "H2", type: "h2" },
    { key: "h3",  label: "标题 3",   desc: "小节标题",       icon: "H3", type: "h3" },
    { key: "quote", label: "引用",   desc: "引述他人或要点", icon: "❝",  type: "blockquote" },
    { key: "code",  label: "代码块", desc: "整块代码",       icon: "{}", type: "pre" },
    { key: "ul",  label: "无序列表", desc: "项目符号列表",   icon: "•",  type: "ul" },
    { key: "ol",  label: "有序列表", desc: "数字编号列表",   icon: "1.", type: "ol" },
    { key: "img", label: "图片",     desc: "插入本地图片",   icon: "🖼", type: "image" },
    { key: "tbl", label: "表格",     desc: "3×3 表格",       icon: "▦",  type: "table" },
    { key: "hr",  label: "分隔线",   desc: "水平分隔",       icon: "—",  type: "hr" },
];

function openSlashMenu() {
    const blk = currentBlock();
    if (!blk) return;
    state.slashAnchor = blk;
    state.slashFilter = "";
    showSlashMenuAtBlock(blk, false);
}

function showSlashMenuAtBlock(blk, isFromHandle) {
    state.slashFromHandle = isFromHandle;
    renderSlashMenu();
    const rect = blk.getBoundingClientRect();
    slashMenu.style.left = (window.scrollX + rect.left) + "px";
    slashMenu.style.top  = (window.scrollY + rect.bottom + 4) + "px";
    slashMenu.classList.add("visible");
    state.slashIndex = 0;
}

function hideSlash() {
    slashMenu.classList.remove("visible");
    state.slashAnchor = null;
}

function updateSlashFilter() {
    if (!state.slashAnchor) return;
    const txt = state.slashAnchor.textContent;
    const m = txt.match(/\/([^\s\/]*)$/);
    state.slashFilter = m ? m[1].toLowerCase() : "";
    renderSlashMenu();
}

function renderSlashMenu() {
    const items = SLASH_ITEMS.filter(function (it) {
        if (!state.slashFilter) return true;
        return it.label.toLowerCase().includes(state.slashFilter)
            || it.key.includes(state.slashFilter);
    });
    if (items.length === 0) { hideSlash(); return; }
    if (state.slashIndex >= items.length) state.slashIndex = 0;
    slashMenu.innerHTML = items.map(function (it, i) {
        return '<div class="item' + (i === state.slashIndex ? " active" : "") + '" data-key="' + it.key + '">'
             + '<div class="icon">' + it.icon + '</div>'
             + '<div class="meta"><div class="label">' + it.label + '</div><div class="desc">' + it.desc + '</div></div>'
             + '</div>';
    }).join("");
    slashMenu.querySelectorAll(".item").forEach(function (el) {
        el.addEventListener("mousedown", function (e) { e.preventDefault(); });
        el.addEventListener("click", function () {
            const key = el.dataset.key;
            applySlashChoice(key);
        });
    });
}

function moveSlash(d) {
    const items = slashMenu.querySelectorAll(".item");
    if (!items.length) return;
    state.slashIndex = (state.slashIndex + d + items.length) % items.length;
    items.forEach(function (el, i) { el.classList.toggle("active", i === state.slashIndex); });
    items[state.slashIndex].scrollIntoView({ block: "nearest" });
}

function pickSlashCurrent() {
    const items = slashMenu.querySelectorAll(".item");
    if (!items.length) return;
    applySlashChoice(items[state.slashIndex].dataset.key);
}

function applySlashChoice(key) {
    const it = SLASH_ITEMS.find(function (x) { return x.key === key; });
    if (!it) return;
    const blk = state.slashAnchor || currentBlock();
    if (!blk) { hideSlash(); return; }
    // 移除触发用的 "/xxx" 文本
    if (!state.slashFromHandle) {
        stripSlashTrigger(blk);
    }
    if (it.type === "image") {
        hideSlash();
        triggerImageInsert();
        return;
    }
    if (it.type === "hr") {
        const hr = document.createElement("hr");
        blk.after(hr);
        const p = document.createElement("p"); p.innerHTML = "<br>";
        hr.after(p);
        if (blk.tagName === "P" && !blk.textContent.trim()) blk.remove();
        placeCaretAtStart(p);
    } else if (it.type === "table") {
        const tbl = buildTable(3, 3);
        blk.after(tbl);
        const p = document.createElement("p"); p.innerHTML = "<br>";
        tbl.after(p);
        if (blk.tagName === "P" && !blk.textContent.trim()) blk.remove();
        placeCaretAtStart(tbl.querySelector("th") || tbl.querySelector("td"));
    } else if (it.type === "ul" || it.type === "ol") {
        const list = document.createElement(it.type);
        const li = document.createElement("li");
        li.innerHTML = blk.innerHTML || "<br>";
        list.appendChild(li);
        blk.replaceWith(list);
        placeCaretAtEnd(li);
    } else {
        convertBlock(blk, it.type);
    }
    hideSlash();
    scheduleReport();
    emitOutline();
}

function stripSlashTrigger(blk) {
    // 把 "/abc" 文本去掉
    const re = /\/[^\s\/]*$/;
    walkText(blk, function (tn) {
        if (re.test(tn.nodeValue)) {
            tn.nodeValue = tn.nodeValue.replace(re, "");
            return true;
        }
        return false;
    });
}

function walkText(root, fn) {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    let n; const list = [];
    while ((n = it.nextNode())) list.push(n);
    list.reverse();
    for (const tn of list) if (fn(tn)) return;
}

function buildTable(rows, cols) {
    const t = document.createElement("table");
    const tbody = document.createElement("tbody");
    for (let r = 0; r < rows; r++) {
        const tr = document.createElement("tr");
        for (let c = 0; c < cols; c++) {
            const cell = document.createElement(r === 0 ? "th" : "td");
            cell.innerHTML = "<br>";
            tr.appendChild(cell);
        }
        tbody.appendChild(tr);
    }
    t.appendChild(tbody);
    return t;
}

function convertBlock(blk, tag) {
    const el = document.createElement(tag);
    if (tag === "pre") {
        // 将文本作为代码内容
        el.textContent = blk.textContent || "";
    } else {
        el.innerHTML = blk.innerHTML;
        if (!el.innerHTML.trim()) el.innerHTML = "<br>";
    }
    blk.replaceWith(el);
    placeCaretAtEnd(el);
    updateEmptyHint(el);
}

// ===================== Markdown 快捷输入 =====================
function handleMarkdownShortcuts() {
    const blk = currentBlock();
    if (!blk || blk.tagName !== "P") return;
    const text = blk.textContent;
    // 仅在光标处于行尾且文本以触发模式开头时转换
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const r = sel.getRangeAt(0);
    if (!r.collapsed) return;
    const m = text.match(/^(#{1,4}\s|>\s|```|-\s|\*\s|1\.\s)/);
    if (!m) return;
    const trig = m[1];
    // 等用户敲完触发的最后一个字符（一般是空格）
    if (trig === "```") {
        // 整段替换为 pre
        const rest = text.slice(3);
        const pre = document.createElement("pre");
        pre.textContent = rest || "";
        blk.replaceWith(pre);
        placeCaretAtEnd(pre);
        return;
    }
    const rest = text.slice(trig.length);
    let newTag = null;
    if (trig === "# ") newTag = "h1";
    else if (trig === "## ") newTag = "h2";
    else if (trig === "### ") newTag = "h3";
    else if (trig === "#### ") newTag = "h4";
    else if (trig === "> ") newTag = "blockquote";
    else if (trig === "- " || trig === "* ") newTag = "ul";
    else if (trig === "1. ") newTag = "ol";
    if (!newTag) return;
    if (newTag === "ul" || newTag === "ol") {
        const list = document.createElement(newTag);
        const li = document.createElement("li");
        li.textContent = rest || "";
        if (!rest) li.innerHTML = "<br>";
        list.appendChild(li);
        blk.replaceWith(list);
        placeCaretAtEnd(li);
    } else {
        const el = document.createElement(newTag);
        el.textContent = rest || "";
        if (!rest) el.innerHTML = "<br>";
        blk.replaceWith(el);
        placeCaretAtEnd(el);
    }
}

// ===================== 工具函数 =====================
function isCaretAtBlockStart(blk) {
    const sel = window.getSelection();
    if (!sel.rangeCount) return false;
    const r = sel.getRangeAt(0);
    if (!r.collapsed) return false;
    const test = document.createRange();
    test.selectNodeContents(blk); test.setEnd(r.startContainer, r.startOffset);
    return test.toString().length === 0;
}
function isCaretAtBlockEnd(blk) {
    const sel = window.getSelection();
    if (!sel.rangeCount) return false;
    const r = sel.getRangeAt(0);
    if (!r.collapsed) return false;
    const test = document.createRange();
    test.selectNodeContents(blk); test.setStart(r.endContainer, r.endOffset);
    return test.toString().length === 0;
}

function updateFocusedBlock() {
    if (!document.body.classList.contains("focus-mode")) {
        Array.from(editor.children).forEach(function (c) { c.classList.remove("is-focused"); });
        return;
    }
    const blk = currentBlock();
    Array.from(editor.children).forEach(function (c) { c.classList.toggle("is-focused", c === blk); });
}

// ===================== 智能粘贴 =====================
editor.addEventListener("paste", function (e) {
    e.preventDefault();
    const cd = e.clipboardData;
    if (!cd) return;
    // 优先处理图片
    for (const item of cd.items) {
        if (item.kind === "file" && item.type.startsWith("image/")) {
            const f = item.getAsFile();
            ingestImageFile(f);
            return;
        }
    }
    if (e.shiftKey) {
        const text = cd.getData("text/plain");
        document.execCommand("insertText", false, text);
        return;
    }
    let html = cd.getData("text/html");
    if (html) {
        html = sanitizeHtml(html);
        document.execCommand("insertHTML", false, html);
    } else {
        const text = cd.getData("text/plain");
        document.execCommand("insertText", false, text);
    }
    scheduleReport();
});

function sanitizeHtml(html) {
    const tpl = document.createElement("template");
    tpl.innerHTML = html;
    const allowed = new Set(["P","H1","H2","H3","H4","H5","H6","BLOCKQUOTE","PRE","UL","OL","LI",
                             "STRONG","EM","B","I","U","S","SUB","SUP","CODE","MARK","A","BR","HR",
                             "TABLE","THEAD","TBODY","TR","TH","TD","FIGURE","FIGCAPTION","IMG"]);
    function walk(node) {
        const children = Array.from(node.childNodes);
        for (const c of children) {
            if (c.nodeType === 3) continue;
            if (c.nodeType !== 1) { c.remove(); continue; }
            const tag = c.tagName;
            if (!allowed.has(tag)) {
                // 拍平：保留子节点
                while (c.firstChild) c.parentNode.insertBefore(c.firstChild, c);
                c.remove();
                continue;
            }
            // 清掉所有属性，仅保留必要
            for (const a of Array.from(c.attributes)) {
                if (tag === "A" && a.name === "href") continue;
                if (tag === "IMG" && (a.name === "src" || a.name === "alt")) continue;
                c.removeAttribute(a.name);
            }
            walk(c);
        }
    }
    walk(tpl.content);
    return tpl.innerHTML;
}

// 图片：在编辑器内拖入或粘贴
editor.addEventListener("dragover", function (e) { e.preventDefault(); });
editor.addEventListener("drop", function (e) {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    const f = e.dataTransfer.files[0];
    if (f.type.startsWith("image/")) {
        e.preventDefault();
        ingestImageFile(f);
    }
});

function ingestImageFile(f) {
    const reader = new FileReader();
    reader.onload = function () {
        if (state.bridge) state.bridge.reportImageDropped(reader.result, f.name || "image.png");
        else insertImageBlock(reader.result, f.name || "");
    };
    reader.readAsDataURL(f);
}

function triggerImageInsert() {
    // 触发系统文件选择
    const ip = document.createElement("input");
    ip.type = "file"; ip.accept = "image/*";
    ip.addEventListener("change", function () {
        if (ip.files && ip.files[0]) ingestImageFile(ip.files[0]);
    });
    ip.click();
}

function insertImageBlock(src, alt) {
    const blk = currentBlock();
    const fig = document.createElement("figure");
    fig.setAttribute("data-align", "center");
    fig.style.textAlign = "center";
    const safeAlt = (alt || "").replace(/"/g, "&quot;");
    fig.innerHTML = '<img src="' + src + '" alt="' + safeAlt + '" />' +
                    '<figcaption contenteditable="true">' + safeAlt + '</figcaption>';
    if (blk) blk.after(fig); else editor.appendChild(fig);
    const p = document.createElement("p"); p.innerHTML = "<br>";
    fig.after(p);
    placeCaretAtStart(p);
    scheduleReport();
}

// ===================== 图片块：选中、对齐、删除 =====================
function selectFigure(fig) {
    document.querySelectorAll("figure.selected").forEach(f => f.classList.remove("selected"));
    if (fig) fig.classList.add("selected");
    showImageBar(fig);
}

function showImageBar(fig) {
    const bar = document.getElementById("image-bar");
    if (!bar) return;
    if (!fig) { bar.classList.remove("visible"); return; }
    const r = fig.getBoundingClientRect();
    bar.style.left = (window.scrollX + r.left + r.width / 2) + "px";
    bar.style.top  = (window.scrollY + r.top - 36) + "px";
    bar.classList.add("visible");
    bar.querySelectorAll("[data-align]").forEach(b => {
        b.classList.toggle("active", b.dataset.align === (fig.dataset.align || "center"));
    });
}

editor.addEventListener("click", function (e) {
    const fig = e.target.closest("figure");
    if (fig && editor.contains(fig)) {
        // 点击 figcaption 时不"选中整块"，让用户直接编辑标题
        if (e.target.tagName !== "FIGCAPTION") selectFigure(fig);
    } else {
        selectFigure(null);
    }
});

document.addEventListener("scroll", function () {
    const fig = document.querySelector("figure.selected");
    if (fig) showImageBar(fig);
}, true);

const imageBar = document.getElementById("image-bar");
if (imageBar) {
    imageBar.addEventListener("mousedown", e => e.preventDefault()); // 防止失焦
    imageBar.addEventListener("click", function (e) {
        const btn = e.target.closest("button"); if (!btn) return;
        const fig = document.querySelector("figure.selected"); if (!fig) return;
        if (btn.dataset.align) {
            applyImageAlign(fig, btn.dataset.align);
        } else if (btn.dataset.action === "delete") {
            removeImageBlock(fig);
        }
    });
}

function applyImageAlign(fig, align) {
    fig.dataset.align = align;
    fig.style.textAlign = align;
    showImageBar(fig);
    scheduleReport();
}

function removeImageBlock(fig) {
    const img = fig.querySelector("img");
    const src = img ? img.getAttribute("src") : "";
    const next = fig.nextElementSibling || fig.previousElementSibling;
    fig.remove();
    if (src && state.bridge && state.bridge.reportImageRemoved) state.bridge.reportImageRemoved(src);
    if (next) placeCaretAtStart(next);
    selectFigure(null);
    scheduleReport();
}

// 选中图片后按 Delete/Backspace 删除整块
document.addEventListener("keydown", function (e) {
    if (e.key !== "Delete" && e.key !== "Backspace") return;
    const fig = document.querySelector("figure.selected");
    if (!fig) return;
    // 仅当焦点不在 figcaption 内时拦截（让用户能正常编辑标题）
    const ae = document.activeElement;
    if (ae && fig.contains(ae) && ae.tagName === "FIGCAPTION") return;
    e.preventDefault();
    removeImageBlock(fig);
}, true);

// ===================== 大纲提取 =====================
function emitOutline() {
    const items = [];
    Array.from(editor.querySelectorAll("h1, h2, h3, h4")).forEach(function (h) {
        items.push({ level: parseInt(h.tagName[1], 10), text: h.textContent.trim() });
    });
    if (state.bridge) state.bridge.reportOutline(JSON.stringify(items));
}

// ===================== 把变更回报给 Python =====================
function scheduleReport() {
    if (state.suppressReport) return;
    if (state.reportTimer) clearTimeout(state.reportTimer);
    state.reportTimer = setTimeout(function () {
        if (!state.bridge || !state.chapterId) return;
        // 清理拖拽辅助类后再回报
        Array.from(editor.children).forEach(function (c) {
            c.classList.remove("drop-target-before");
            c.classList.remove("drop-target-after");
            c.classList.remove("is-focused");
        });
        const html = editor.innerHTML;
        state.bridge.reportHtml(state.chapterId, html);
        emitOutline();
    }, 250);
}

// ===================== 右键菜单（"由此分章"） =====================
const ctxMenu = document.createElement("div");
ctxMenu.className = "ctx-menu";
ctxMenu.innerHTML = '<div class="item" data-action="split">✂ 由此分章</div>';
document.body.appendChild(ctxMenu);

editor.addEventListener("contextmenu", function (e) {
    if (!editor.contains(e.target)) return;
    e.preventDefault();
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const r = sel.getRangeAt(0);
    // 边界检查：开头/末尾禁用分章
    const isStart = isCaretAtEditorStart();
    const isEnd   = isCaretAtEditorEnd();
    const splitItem = ctxMenu.querySelector('[data-action="split"]');
    splitItem.classList.toggle("disabled", isStart || isEnd);
    ctxMenu.style.left = (window.scrollX + e.clientX) + "px";
    ctxMenu.style.top  = (window.scrollY + e.clientY) + "px";
    ctxMenu.classList.add("visible");
});

document.addEventListener("mousedown", function (e) {
    if (!ctxMenu.contains(e.target)) ctxMenu.classList.remove("visible");
});

ctxMenu.addEventListener("click", function (e) {
    const item = e.target.closest(".item");
    if (!item || item.classList.contains("disabled")) return;
    if (item.dataset.action === "split") {
        ctxMenu.classList.remove("visible");
        splitAtCaret();
    }
});

function isCaretAtEditorStart() {
    const blk = editor.firstElementChild;
    if (!blk) return true;
    const sel = window.getSelection();
    if (!sel.rangeCount) return false;
    const r = sel.getRangeAt(0);
    const cur = currentBlock();
    if (cur !== blk) return false;
    return isCaretAtBlockStart(blk);
}

function isCaretAtEditorEnd() {
    const blk = editor.lastElementChild;
    if (!blk) return true;
    const sel = window.getSelection();
    if (!sel.rangeCount) return false;
    const cur = currentBlock();
    if (cur !== blk) return false;
    return isCaretAtBlockEnd(blk);
}

function splitAtCaret() {
    if (!state.bridge || !state.chapterId) return;
    // 先取消任何待发的 reportHtml（避免把切分前的 innerHTML 回写原章节）
    if (state.reportTimer) { clearTimeout(state.reportTimer); state.reportTimer = null; }
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const r = sel.getRangeAt(0);
    const cur = currentBlock();
    if (!cur) return;

    // 在光标处把当前 block 切成两个：beforePart 留下，afterPart 进入新章节
    const beforeRange = document.createRange();
    beforeRange.setStart(cur, 0);
    beforeRange.setEnd(r.startContainer, r.startOffset);
    const afterRange = document.createRange();
    afterRange.setStart(r.endContainer, r.endOffset);
    afterRange.setEnd(cur, cur.childNodes.length);

    const beforeFrag = beforeRange.cloneContents();
    const afterFrag  = afterRange.cloneContents();
    const beforeBlock = cur.cloneNode(false);
    beforeBlock.appendChild(beforeFrag);
    const afterBlock  = cur.cloneNode(false);
    afterBlock.appendChild(afterFrag);

    // 拼接：当前块前的兄弟 + beforeBlock => before_html；afterBlock + 后续兄弟 => after_html
    const beforeHtml = [];
    for (const sib of Array.from(editor.children)) {
        if (sib === cur) break;
        beforeHtml.push(sib.outerHTML);
    }
    if (beforeBlock.innerHTML.trim() || beforeBlock.tagName !== "P") beforeHtml.push(beforeBlock.outerHTML);

    const afterHtml = [];
    if (afterBlock.innerHTML.trim() || afterBlock.tagName !== "P") afterHtml.push(afterBlock.outerHTML);
    let started = false;
    for (const sib of Array.from(editor.children)) {
        if (sib === cur) { started = true; continue; }
        if (!started) continue;
        afterHtml.push(sib.outerHTML);
    }

    const beforeHtmlStr = beforeHtml.join("\n") || "<p><br></p>";
    const afterHtmlStr  = afterHtml.join("\n")  || "<p><br></p>";
    state.bridge.reportSplit(state.chapterId, beforeHtmlStr, afterHtmlStr);
}

})();
