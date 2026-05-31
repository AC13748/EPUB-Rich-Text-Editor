"""主窗口：左侧章节树 + 中央 WebEngine 编辑器 + 右侧多 Tab 面板。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QTabWidget, QFormLayout, QLineEdit, QTextEdit, QComboBox,
    QDoubleSpinBox, QPushButton, QFileDialog, QMessageBox, QInputDialog,
    QLabel, QToolBar, QStatusBar, QMenu, QAbstractItemView, QFrame,
)

from .book_model import Book, Chapter, new_book_with_template
from .bridge import EditorBridge, split_data_url
from .epub_io import import_epub, export_epub
from .autosave import dump_book, load_book, has_autosave, clear_autosave


WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class MainWindow(QMainWindow):
    bookDirty = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PySave EPUB Editor")
        self.resize(1440, 900)

        self.book: Book = new_book_with_template()
        self.current_chapter_id: Optional[str] = None
        self._dirty: bool = False
        self._loading: bool = False

        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # 自动保存定时器（5 秒）
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(5000)
        self._autosave_timer.timeout.connect(self._do_autosave)
        self._autosave_timer.start()

        # 启动后初始化 WebEngine 完成时再加载书籍
        QTimer.singleShot(80, self._maybe_recover_or_init)

    # ===================== 工具栏 =====================
    def _build_toolbar(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setIconSize(tb.iconSize() * 0.9)
        self.addToolBar(tb)

        act_new = QAction("新建", self)
        act_new.setShortcut(QKeySequence.New)
        act_new.triggered.connect(self.action_new)
        tb.addAction(act_new)

        act_open = QAction("打开 EPUB", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self.action_open)
        tb.addAction(act_open)

        act_save = QAction("保存工程", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self.action_save)
        tb.addAction(act_save)

        act_export = QAction("导出 EPUB", self)
        act_export.setShortcut("Ctrl+Shift+E")
        act_export.triggered.connect(self.action_export)
        tb.addAction(act_export)

        tb.addSeparator()

        # 插入图片
        act_img = QAction("插入图片", self)
        act_img.setShortcut("Ctrl+Shift+I")
        act_img.triggered.connect(self.action_insert_image)
        tb.addAction(act_img)
        self.act_insert_image = act_img

        # 导入字体
        act_font = QAction("导入字体", self)
        act_font.triggered.connect(self.action_import_font)
        tb.addAction(act_font)

        tb.addSeparator()

        # 全局搜索
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("全局搜索…（Enter 跳到下一个）")
        self.search_box.setFixedWidth(280)
        self.search_box.returnPressed.connect(self._do_search)
        tb.addWidget(self.search_box)

        tb.addSeparator()

        # 编辑主题
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["护眼 (sepia)", "纯白 (light)", "深色 (dark)"])
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        tb.addWidget(QLabel("编辑主题: "))
        tb.addWidget(self.theme_combo)

        # 聚焦/打字机模式
        self.focus_btn = QAction("聚焦模式", self, checkable=True)
        self.focus_btn.toggled.connect(lambda on: self.bridge.setFocusMode.emit(bool(on)))
        tb.addAction(self.focus_btn)

        self.typewriter_btn = QAction("打字机", self, checkable=True)
        self.typewriter_btn.toggled.connect(lambda on: self.bridge.setTypewriterMode.emit(bool(on)))
        tb.addAction(self.typewriter_btn)

    # ===================== 三栏中央区 =====================
    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)

        # ----- 左：章节树 -----
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("章节")
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.itemSelectionChanged.connect(self._on_chapter_selected)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._chapter_menu)
        # 拖放完成后重建模型
        self.tree.model().rowsMoved.connect(self._chapters_reordered)

        # 快捷键：Delete 删除、Tab 降级、Shift+Tab 升级
        from PySide6.QtGui import QShortcut
        sc_del = QShortcut(QKeySequence(Qt.Key_Delete), self.tree)
        sc_del.setContext(Qt.WidgetShortcut)
        sc_del.activated.connect(self._delete_selected_chapters)
        sc_indent = QShortcut(QKeySequence(Qt.Key_Tab), self.tree)
        sc_indent.setContext(Qt.WidgetShortcut)
        sc_indent.activated.connect(self._indent_selected_chapters)
        sc_outdent = QShortcut(QKeySequence("Shift+Tab"), self.tree)
        sc_outdent.setContext(Qt.WidgetShortcut)
        sc_outdent.activated.connect(self._outdent_selected_chapters)

        left = QWidget()
        ll = QVBoxLayout(left); ll.setContentsMargins(6, 6, 6, 6); ll.setSpacing(6)
        ll.addWidget(QLabel("章节"))
        ll.addWidget(self.tree, 1)
        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ 新建章节")
        btn_add.clicked.connect(self._add_chapter)
        btn_split = QPushButton("快速分章…")
        btn_split.clicked.connect(self._open_split_dialog)
        btn_row.addWidget(btn_add); btn_row.addWidget(btn_split)
        ll.addLayout(btn_row)
        splitter.addWidget(left)

        # ----- 中：WebEngine 富文本编辑器 -----
        self.web = QWebEngineView()
        # 让从 file:// 加载的 editor.html 能访问 book:// 资源
        from PySide6.QtWebEngineCore import QWebEngineSettings
        ws = self.web.settings()
        ws.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        ws.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        ws.setAttribute(QWebEngineSettings.AllowRunningInsecureContent, True)
        self.bridge = EditorBridge()
        self.channel = QWebChannel(self.web.page())
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)

        # 安装 book:// 资源协议处理器（在 QApplication 启动后才能装）
        from .book_scheme import BookSchemeHandler, SCHEME_NAME
        self._book_scheme_handler = BookSchemeHandler(self)
        self._book_scheme_handler.attach_book(self.book)
        self.web.page().profile().installUrlSchemeHandler(SCHEME_NAME, self._book_scheme_handler)

        self.bridge.htmlChanged.connect(self._on_html_changed)
        self.bridge.outlineChanged.connect(self._on_outline_changed)
        self.bridge.imageDropped.connect(self._on_image_dropped)
        self.bridge.splitRequested.connect(self._on_split_requested)
        self.bridge.imageRemoved.connect(self._on_image_removed)

        editor_url = QUrl.fromLocalFile(str(WEB_DIR / "editor.html"))
        self.web.load(editor_url)
        self._editor_ready = False
        self.web.loadFinished.connect(self._on_editor_load_finished)

        center = QFrame(); cl = QVBoxLayout(center); cl.setContentsMargins(0, 0, 0, 0); cl.addWidget(self.web)
        splitter.addWidget(center)

        # ----- 右：Tab 面板 -----
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self._build_outline_tab(), "大纲")
        self.right_tabs.addTab(self._build_meta_tab(), "元数据")
        self.right_tabs.addTab(self._build_export_tab(), "导出")
        splitter.addWidget(self.right_tabs)

        splitter.setSizes([240, 880, 320])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        self.setCentralWidget(splitter)

    def _build_outline_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        self.outline_view = QTreeWidget()
        self.outline_view.setHeaderLabel("当前章节大纲")
        l.addWidget(self.outline_view)
        return w

    def _build_meta_tab(self) -> QWidget:
        w = QWidget(); form = QFormLayout(w)
        self.meta_title = QLineEdit(); self.meta_title.editingFinished.connect(self._sync_meta)
        self.meta_author = QLineEdit(); self.meta_author.editingFinished.connect(self._sync_meta)
        self.meta_lang = QLineEdit(); self.meta_lang.editingFinished.connect(self._sync_meta)
        self.meta_publisher = QLineEdit(); self.meta_publisher.editingFinished.connect(self._sync_meta)
        self.meta_isbn = QLineEdit(); self.meta_isbn.editingFinished.connect(self._sync_meta)
        self.meta_desc = QTextEdit(); self.meta_desc.setFixedHeight(96); self.meta_desc.textChanged.connect(self._sync_meta)
        self.cover_label = QLabel("尚未设置封面")
        self.cover_label.setFixedHeight(160)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet("border:1px dashed #aaa; color:#888")
        self.cover_label.setAcceptDrops(True)
        self.cover_label.installEventFilter(self)
        cover_btn = QPushButton("选择封面图片…")
        cover_btn.clicked.connect(self._choose_cover)

        form.addRow("书名", self.meta_title)
        form.addRow("作者", self.meta_author)
        form.addRow("语言", self.meta_lang)
        form.addRow("出版方", self.meta_publisher)
        form.addRow("标识/ISBN", self.meta_isbn)
        form.addRow("简介", self.meta_desc)
        form.addRow("封面", self.cover_label)
        form.addRow("", cover_btn)
        return w

    def _build_export_tab(self) -> QWidget:
        from PySide6.QtWidgets import QSpinBox
        w = QWidget(); form = QFormLayout(w)
        self.export_theme = QComboBox()
        self.export_theme.addItems(["经典书籍 (classic)", "现代简约 (modern)", "学术 (academic)"])
        self.export_theme.currentIndexChanged.connect(self._sync_export)
        self.export_font = QLineEdit("Source Han Serif, Noto Serif CJK SC, serif")
        self.export_font.editingFinished.connect(self._sync_export)
        self.export_lh = QDoubleSpinBox(); self.export_lh.setRange(1.0, 3.0); self.export_lh.setSingleStep(0.05); self.export_lh.setValue(1.8)
        self.export_lh.valueChanged.connect(self._sync_export)

        # 旧字段保留作 fallback（导出 CSS 用），但 UI 改为四向 mm
        self.export_margin = QLineEdit("2.2em")  # legacy

        def _mm(default):
            sp = QSpinBox(); sp.setRange(0, 80); sp.setSuffix(" mm"); sp.setValue(default)
            sp.valueChanged.connect(self._sync_export)
            return sp
        self.export_mt = _mm(18)
        self.export_mb = _mm(18)
        self.export_ml = _mm(22)
        self.export_mr = _mm(22)
        margin_box = QWidget(); mh = QHBoxLayout(margin_box); mh.setContentsMargins(0,0,0,0)
        mh.addWidget(QLabel("上")); mh.addWidget(self.export_mt)
        mh.addWidget(QLabel("下")); mh.addWidget(self.export_mb)
        mh.addWidget(QLabel("左")); mh.addWidget(self.export_ml)
        mh.addWidget(QLabel("右")); mh.addWidget(self.export_mr)

        btn_export = QPushButton("导出 EPUB…")
        btn_export.clicked.connect(self.action_export)

        form.addRow("导出主题", self.export_theme)
        form.addRow("字体族", self.export_font)
        form.addRow("行高", self.export_lh)
        form.addRow("页边距", margin_box)
        form.addRow("", btn_export)
        return w

    # ===================== 状态栏 =====================
    def _build_statusbar(self) -> None:
        sb = QStatusBar(); self.setStatusBar(sb)
        self.status_msg = QLabel("就绪")
        sb.addWidget(self.status_msg)
        self.status_dirty = QLabel("● 已保存"); self.status_dirty.setStyleSheet("color:#0a0")
        sb.addPermanentWidget(self.status_dirty)

    # ===================== 启动流程 =====================
    def _maybe_recover_or_init(self) -> None:
        ap = has_autosave(None)
        if ap is not None:
            ret = QMessageBox.question(
                self, "崩溃恢复",
                "检测到上次未正常关闭的工作。是否从自动保存中恢复？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret == QMessageBox.Yes:
                try:
                    self.book = load_book(ap)
                    self._reload_all()
                    self.status_msg.setText("已从自动保存恢复")
                    return
                except Exception as e:
                    QMessageBox.warning(self, "恢复失败", str(e))
        self._reload_all()

    def _reload_all(self) -> None:
        # 切书后让 scheme handler 指向新 Book 的资源
        if hasattr(self, "_book_scheme_handler"):
            self._book_scheme_handler.attach_book(self.book)
        self._refresh_chapter_tree()
        self._sync_meta_to_form()
        self._sync_export_to_form()
        if self.book.chapters:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._theme_changed(self.theme_combo.currentIndex())
        # 重发自定义字体 + 排版到 WebEngine（避免切书后样式丢失）
        QTimer.singleShot(60, self._push_book_assets_to_editor)

    def _push_book_assets_to_editor(self) -> None:
        for f in getattr(self.book, "custom_fonts", []) or []:
            self.bridge.addFontFace.emit(f["name"], f"book://font/{f['filename']}")
        self.bridge.setTypography.emit(
            float(self.book.line_height),
            int(getattr(self.book, "margin_top_mm", 18)),
            int(getattr(self.book, "margin_bottom_mm", 18)),
            int(getattr(self.book, "margin_left_mm", 22)),
            int(getattr(self.book, "margin_right_mm", 22)),
        )

    def _on_editor_load_finished(self, ok: bool) -> None:
        self._editor_ready = bool(ok)
        if not ok:
            return
        # 编辑器加载完才推 assets，避免早于 QWebChannel 就绪
        QTimer.singleShot(50, self._push_book_assets_to_editor)
        # 当前章节也补推一次
        if self.current_chapter_id:
            ch = self.book.find_chapter(self.current_chapter_id)
            if ch is not None:
                self.bridge.loadChapter.emit(ch.chapter_id, ch.title, self._to_editor_html(ch.body_html))

    # ===================== 章节树 =====================
    def _refresh_chapter_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        # 按 chapters 顺序 + parent_id 构建嵌套
        item_map: dict[str, QTreeWidgetItem] = {}
        for ch in self.book.chapters:
            it = QTreeWidgetItem([ch.title])
            it.setData(0, Qt.UserRole, ch.chapter_id)
            it.setFlags(it.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            item_map[ch.chapter_id] = it
        for ch in self.book.chapters:
            it = item_map[ch.chapter_id]
            parent_it = item_map.get(ch.parent_id) if ch.parent_id else None
            if parent_it is None:
                self.tree.addTopLevelItem(it)
            else:
                parent_it.addChild(it)
        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _on_chapter_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        cid = items[0].data(0, Qt.UserRole)
        if cid == self.current_chapter_id:
            return
        ch = self.book.find_chapter(cid)
        if ch is None:
            return
        self.current_chapter_id = cid
        self._loading = True
        self.bridge.loadChapter.emit(cid, ch.title, self._to_editor_html(ch.body_html))
        self._loading = False
        self.status_msg.setText(f"当前：{ch.title}")

    # ===== 编辑期 src 转换：images/xxx <-> book://res/xxx =====
    @staticmethod
    def _to_editor_html(html: str) -> str:
        """送给前端 WebEngine 时：把 images/<file> 改写为 book://res/<file>，让 scheme handler 提供数据。"""
        if not html:
            return html
        import re as _re
        return _re.sub(
            r'(<img\b[^>]*\bsrc=")(?:\.\./)?images/([^"]+)"',
            r'\1book://res/\2"',
            html,
        )

    @staticmethod
    def _from_editor_html(html: str) -> str:
        """收回前端 HTML 时：把 book://res/ 还原为 images/，落到 Book.body_html。"""
        if not html:
            return html
        import re as _re
        return _re.sub(
            r'(<img\b[^>]*\bsrc=")book://res/([^"]+)"',
            r'\1images/\2"',
            html,
        )

    def _chapter_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("新建章节", self._add_chapter)
        if item is not None:
            menu.addAction("重命名", lambda: self._start_inline_rename(item.data(0, Qt.UserRole)))
            menu.addSeparator()
            menu.addAction("降级 (Tab)", self._indent_selected_chapters)
            menu.addAction("升级 (Shift+Tab)", self._outdent_selected_chapters)
            menu.addSeparator()
            menu.addAction("删除章节 (Delete)", self._delete_selected_chapters)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _add_chapter(self) -> None:
        """新建章节：插入到当前选中章节之后（无选中则放末尾），并自动重命名。"""
        ch = self._create_chapter_after_current()
        self._refresh_chapter_tree()
        self._select_chapter(ch.chapter_id)
        # 自动进入重命名状态
        QTimer.singleShot(0, lambda: self._start_inline_rename(ch.chapter_id))
        self._mark_dirty()

    def _create_chapter_after_current(self, title: str = "新建章节") -> Chapter:
        cur_id = self.current_chapter_id
        cur = self.book.find_chapter(cur_id) if cur_id else None
        idx = self.book.index_of(cur_id) if cur_id else -1
        if idx < 0:
            idx = len(self.book.chapters) - 1
        new_ch = Chapter(title=title, body_html="<p><br></p>",
                          parent_id=cur.parent_id if cur else None,
                          level=cur.level if cur else 0)
        self.book.chapters.insert(idx + 1, new_ch)
        return new_ch

    def _start_inline_rename(self, chapter_id: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self._find_tree_item(self.tree.topLevelItem(i), chapter_id)
            if item is not None:
                self.tree.setCurrentItem(item)
                self.tree.editItem(item, 0)
                return

    def _find_tree_item(self, root: QTreeWidgetItem, chapter_id: str) -> Optional[QTreeWidgetItem]:
        if root.data(0, Qt.UserRole) == chapter_id:
            return root
        for i in range(root.childCount()):
            found = self._find_tree_item(root.child(i), chapter_id)
            if found is not None:
                return found
        return None

    def _rename_chapter(self, item: QTreeWidgetItem) -> None:
        cid = item.data(0, Qt.UserRole)
        ch = self.book.find_chapter(cid)
        if ch is None:
            return
        title, ok = QInputDialog.getText(self, "重命名", "新章节名：", text=ch.title)
        if not ok or not title.strip():
            return
        ch.title = title.strip()
        item.setText(0, ch.title)
        self._mark_dirty()

    def _delete_chapter(self, item: QTreeWidgetItem) -> None:
        # 旧入口保留兼容；实际逻辑全部走 _delete_selected_chapters。
        self.tree.setCurrentItem(item)
        self._delete_selected_chapters()

    def _delete_selected_chapters(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        cids = [it.data(0, Qt.UserRole) for it in items]
        chapters = [self.book.find_chapter(c) for c in cids]
        chapters = [c for c in chapters if c is not None]
        if not chapters:
            return
        if len(self.book.chapters) - len(chapters) <= 0:
            QMessageBox.information(self, "无法删除", "至少需要保留 1 章。")
            return
        # 是否包含非空内容
        has_content = any(not ch.is_effectively_empty() for ch in chapters)
        if has_content:
            ret = QMessageBox.warning(
                self, "删除章节",
                "删除章节将同时删除其内容，是否继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
        for ch in chapters:
            self._merge_into_prev_and_remove(ch)
        if self.current_chapter_id in {c.chapter_id for c in chapters}:
            self.current_chapter_id = None
        self._refresh_chapter_tree()
        if self.book.chapters:
            first = self.tree.topLevelItem(0)
            if first is not None:
                self.tree.setCurrentItem(first)
        self._mark_dirty()

    def _merge_into_prev_and_remove(self, ch: Chapter) -> None:
        """删除章节时把其 body_html 并入前一个章节（同层级偏好），无前一个则并入下一个。"""
        idx = self.book.index_of(ch.chapter_id)
        if idx < 0:
            return
        target: Optional[Chapter] = None
        for j in range(idx - 1, -1, -1):
            target = self.book.chapters[j]
            break
        if target is None and idx + 1 < len(self.book.chapters):
            target = self.book.chapters[idx + 1]
        if target is not None and not ch.is_effectively_empty():
            merged = (target.body_html.rstrip() + "\n" + ch.body_html.lstrip()).strip()
            target.body_html = merged or "<p><br></p>"
        self.book.remove_chapter(ch.chapter_id)

    def _select_chapter(self, cid: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            it = self._find_tree_item(self.tree.topLevelItem(i), cid)
            if it is not None:
                self.tree.setCurrentItem(it)
                return

    def _chapters_reordered(self, *args) -> None:
        """拖拽完成后：根据 QTreeWidget 当前结构重写 book.chapters 顺序和 parent_id/level。"""
        flat: list[tuple[str, Optional[str], int]] = []

        def walk(item: QTreeWidgetItem, parent_id: Optional[str], level: int) -> None:
            cid = item.data(0, Qt.UserRole)
            flat.append((cid, parent_id, level))
            for i in range(item.childCount()):
                walk(item.child(i), cid, level + 1)

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i), None, 0)

        # 重排 chapters，并同步 parent_id / level
        ordered_ids = [t[0] for t in flat]
        meta = {cid: (pid, lvl) for cid, pid, lvl in flat}
        # 防止丢失（若 QTreeWidget 漏了某项）
        for ch in self.book.chapters:
            if ch.chapter_id not in meta:
                meta[ch.chapter_id] = (ch.parent_id, ch.level)
                ordered_ids.append(ch.chapter_id)
        self.book.reorder(ordered_ids)
        for ch in self.book.chapters:
            pid, lvl = meta[ch.chapter_id]
            # 防循环：父节点必须存在且不能是自己的后代
            if pid is not None:
                desc = set(self.book.descendants_of(ch.chapter_id))
                if pid in desc or pid == ch.chapter_id:
                    pid = None; lvl = 0
            ch.parent_id = pid
            ch.level = lvl
        self._mark_dirty()

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        cid = item.data(0, Qt.UserRole)
        ch = self.book.find_chapter(cid)
        if ch is None:
            return
        new_title = item.text(0).strip()
        if new_title and new_title != ch.title:
            ch.title = new_title
            self._mark_dirty()

    # ===================== 层级升降 =====================
    def _selected_chapter_ids_in_order(self) -> list[str]:
        ids = {it.data(0, Qt.UserRole) for it in self.tree.selectedItems()}
        return [c.chapter_id for c in self.book.chapters if c.chapter_id in ids]

    def _indent_selected_chapters(self) -> None:
        """降级（Tab）：让所选成为前一兄弟（在 book.chapters 里、同 parent）的子节点。"""
        cids = self._selected_chapter_ids_in_order()
        if not cids:
            return
        changed = False
        for cid in cids:
            ch = self.book.find_chapter(cid)
            if ch is None:
                continue
            idx = self.book.index_of(cid)
            # 找前面第一个 parent_id 与自己当前相同的同级节点
            new_parent: Optional[Chapter] = None
            for j in range(idx - 1, -1, -1):
                cand = self.book.chapters[j]
                if cand.parent_id == ch.parent_id and cand.chapter_id not in cids:
                    new_parent = cand
                    break
            if new_parent is None:
                continue
            ch.parent_id = new_parent.chapter_id
            ch.level = new_parent.level + 1
            # 同步整个子树
            self._cascade_level(ch.chapter_id, ch.level)
            changed = True
        if changed:
            self._mark_dirty()
            self._refresh_chapter_tree()
            self._restore_selection(cids)

    def _outdent_selected_chapters(self) -> None:
        """升级（Shift+Tab）：选中节点 -> 父级的同级兄弟；
        同时把后方"层级低于自身的兄弟"吸收为子节点。"""
        cids = self._selected_chapter_ids_in_order()
        if not cids:
            return
        changed = False
        for cid in cids:
            ch = self.book.find_chapter(cid)
            if ch is None or ch.parent_id is None:
                continue
            parent = self.book.find_chapter(ch.parent_id)
            if parent is None:
                continue
            old_level = ch.level
            ch.parent_id = parent.parent_id
            ch.level = parent.level
            self._cascade_level(ch.chapter_id, ch.level)

            # 把后方层级 > old_level（即原本是当前节点之后的更深层节点的祖孙）拉成自己的子树。
            # 简化语义：把紧跟在自身后面、原 level > new_level（= parent.level）的节点设为自己的子节点。
            idx = self.book.index_of(ch.chapter_id)
            for k in range(idx + 1, len(self.book.chapters)):
                nxt = self.book.chapters[k]
                if nxt.level <= ch.level:
                    break
                # 仅修改"前父链路指向 parent 或其子树"的节点
                if nxt.parent_id == parent.chapter_id or self._is_descendant_of(nxt.chapter_id, parent.chapter_id):
                    if nxt.parent_id == parent.chapter_id:
                        nxt.parent_id = ch.chapter_id
                        nxt.level = ch.level + 1
                        self._cascade_level(nxt.chapter_id, nxt.level)
            changed = True
        if changed:
            self._mark_dirty()
            self._refresh_chapter_tree()
            self._restore_selection(cids)

    def _cascade_level(self, root_id: str, root_level: int) -> None:
        """更新某节点子孙的 level（保持相对深度）。"""
        # 先记录原 level
        root = self.book.find_chapter(root_id)
        if root is None:
            return
        for cid in self.book.descendants_of(root_id):
            ch = self.book.find_chapter(cid)
            if ch is None:
                continue
            # 寻找其父在 chapters 里
            p = self.book.find_chapter(ch.parent_id) if ch.parent_id else None
            ch.level = (p.level + 1) if p else 0

    def _is_descendant_of(self, cid: str, ancestor_id: str) -> bool:
        cur = self.book.find_chapter(cid)
        while cur is not None and cur.parent_id is not None:
            if cur.parent_id == ancestor_id:
                return True
            cur = self.book.find_chapter(cur.parent_id)
        return False

    def _restore_selection(self, cids: list[str]) -> None:
        self.tree.clearSelection()
        for cid in cids:
            for i in range(self.tree.topLevelItemCount()):
                it = self._find_tree_item(self.tree.topLevelItem(i), cid)
                if it is not None:
                    it.setSelected(True)
                    break

    # ===================== 快速分章（正则） =====================
    def _open_split_dialog(self) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QPlainTextEdit, QRadioButton, QButtonGroup
        from .split_engine import DEFAULT_PATTERNS, preview_split, apply_split

        dlg = QDialog(self)
        dlg.setWindowTitle("快速分章（正则）")
        dlg.resize(720, 560)

        root = QVBoxLayout(dlg)
        root.addWidget(QLabel("内置正则（勾选启用，OR 关系）："))
        list_w = QListWidget()
        for p in DEFAULT_PATTERNS:
            it = QListWidgetItem(p)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            list_w.addItem(it)
        root.addWidget(list_w)

        root.addWidget(QLabel("自定义正则（每行一条）："))
        custom_edit = QPlainTextEdit()
        custom_edit.setFixedHeight(80)
        root.addWidget(custom_edit)

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("范围："))
        rb_current = QRadioButton("仅当前章节")
        rb_all = QRadioButton("整本书")
        rb_all.setChecked(True)
        grp = QButtonGroup(dlg); grp.addButton(rb_current); grp.addButton(rb_all)
        scope_row.addWidget(rb_current); scope_row.addWidget(rb_all); scope_row.addStretch(1)
        root.addLayout(scope_row)

        preview_label = QLabel('点击下方"预览"按钮查看分章结果。')
        preview_label.setWordWrap(True)
        root.addWidget(preview_label)

        btn_preview = QPushButton("预览")
        root.addWidget(btn_preview)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("执行分章")
        btns.button(QDialogButtonBox.Ok).setEnabled(False)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root.addWidget(btns)

        state = {"plan": []}

        def collect_patterns() -> list[str]:
            ps: list[str] = []
            for i in range(list_w.count()):
                it = list_w.item(i)
                if it.checkState() == Qt.Checked:
                    ps.append(it.text())
            for line in custom_edit.toPlainText().splitlines():
                line = line.strip()
                if line:
                    ps.append(line)
            return ps

        def collect_scope_ids() -> list[str]:
            if rb_current.isChecked() and self.current_chapter_id:
                return [self.current_chapter_id]
            return [c.chapter_id for c in self.book.chapters]

        def do_preview():
            patterns = collect_patterns()
            scope = collect_scope_ids()
            plan = preview_split(self.book, patterns, scope)
            state["plan"] = plan
            total_new = sum(len(ck) - 1 for _, ck in plan)
            if total_new == 0:
                preview_label.setText("（未匹配到任何分章规则，请调整正则或扩大范围。）")
                btns.button(QDialogButtonBox.Ok).setEnabled(False)
                return
            sample_titles: list[str] = []
            for _, chunks in plan:
                for ck in chunks:
                    sample_titles.append(ck.title)
                    if len(sample_titles) >= 5:
                        break
                if len(sample_titles) >= 5:
                    break
            preview_label.setText(
                f"将在 {len(plan)} 个章节中拆出 {total_new} 个新章节。"
                f"\n前 5 个标题：" + "  /  ".join(sample_titles[:5])
            )
            btns.button(QDialogButtonBox.Ok).setEnabled(True)

        btn_preview.clicked.connect(do_preview)

        if dlg.exec() == QDialog.Accepted:
            patterns = collect_patterns()
            scope = collect_scope_ids()
            added = apply_split(self.book, patterns, scope)
            if added:
                self._refresh_chapter_tree()
                self._mark_dirty()
                self.status_msg.setText(f"快速分章完成，新增 {added} 章。")
            else:
                self.status_msg.setText("快速分章未匹配到任何分章。")

    # ===================== 元数据 / 导出设置同步 =====================
    def _sync_meta(self) -> None:
        if self._loading:
            return
        self.book.title = self.meta_title.text().strip() or "未命名书籍"
        self.book.author = self.meta_author.text().strip() or "佚名"
        self.book.language = self.meta_lang.text().strip() or "zh-CN"
        self.book.publisher = self.meta_publisher.text().strip()
        self.book.identifier = self.meta_isbn.text().strip() or self.book.identifier
        self.book.description = self.meta_desc.toPlainText().strip()
        self._mark_dirty()

    def _sync_meta_to_form(self) -> None:
        self._loading = True
        self.meta_title.setText(self.book.title)
        self.meta_author.setText(self.book.author)
        self.meta_lang.setText(self.book.language)
        self.meta_publisher.setText(self.book.publisher)
        self.meta_isbn.setText(self.book.identifier)
        self.meta_desc.setPlainText(self.book.description)
        self._update_cover_preview()
        self._loading = False

    def _sync_export(self) -> None:
        if self._loading:
            return
        idx = self.export_theme.currentIndex()
        self.book.export_theme = ["classic", "modern", "academic"][idx]
        self.book.font_family = self.export_font.text().strip()
        self.book.line_height = self.export_lh.value()
        self.book.margin_top_mm = self.export_mt.value()
        self.book.margin_bottom_mm = self.export_mb.value()
        self.book.margin_left_mm = self.export_ml.value()
        self.book.margin_right_mm = self.export_mr.value()
        # 把当前排版立刻反映到 WebEngine 编辑器
        if hasattr(self, "bridge"):
            self.bridge.setTypography.emit(
                float(self.book.line_height),
                int(self.book.margin_top_mm),
                int(self.book.margin_bottom_mm),
                int(self.book.margin_left_mm),
                int(self.book.margin_right_mm),
            )
        self._mark_dirty()

    def _sync_export_to_form(self) -> None:
        self._loading = True
        idx = {"classic": 0, "modern": 1, "academic": 2}.get(self.book.export_theme, 0)
        self.export_theme.setCurrentIndex(idx)
        self.export_font.setText(self.book.font_family)
        self.export_lh.setValue(self.book.line_height)
        self.export_mt.setValue(int(getattr(self.book, "margin_top_mm", 18)))
        self.export_mb.setValue(int(getattr(self.book, "margin_bottom_mm", 18)))
        self.export_ml.setValue(int(getattr(self.book, "margin_left_mm", 22)))
        self.export_mr.setValue(int(getattr(self.book, "margin_right_mm", 22)))
        self._loading = False

    def _choose_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择封面图片", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        data = Path(path).read_bytes()
        suffix = Path(path).suffix
        res = self.book.add_image(data, suffix)
        self.book.cover = res
        self._update_cover_preview()
        self._mark_dirty()

    def _update_cover_preview(self) -> None:
        if self.book.cover is None:
            self.cover_label.setText("尚未设置封面"); self.cover_label.setPixmap(QPixmap())
            return
        pm = QPixmap()
        pm.loadFromData(self.book.cover.data)
        if pm.isNull():
            self.cover_label.setText("（封面无法预览）"); return
        scaled = pm.scaledToHeight(150, Qt.SmoothTransformation)
        self.cover_label.setPixmap(scaled)

    # ===================== 编辑主题 =====================
    def _theme_changed(self, idx: int) -> None:
        themes = ["sepia", "light", "dark"]
        if 0 <= idx < len(themes):
            self.bridge.setEditorTheme.emit(themes[idx])

    # ===================== 来自 JS 的回报 =====================
    def _on_html_changed(self, chapter_id: str, html: str) -> None:
        ch = self.book.find_chapter(chapter_id)
        if ch is None:
            return
        if html is None:
            return
        normalized = self._from_editor_html(html) or ""
        if not normalized.strip():
            normalized = "<p><br/></p>"
        if ch.body_html != normalized:
            ch.body_html = normalized
            self._mark_dirty()

    def _on_outline_changed(self, outline_json: str) -> None:
        try:
            items = json.loads(outline_json or "[]")
        except Exception:
            items = []
        self.outline_view.clear()
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for it in items:
            level = int(it.get("level", 1))
            text = it.get("text", "") or "(无标题)"
            node = QTreeWidgetItem([text])
            while stack and stack[-1][0] >= level:
                stack.pop()
            if not stack:
                self.outline_view.addTopLevelItem(node)
            else:
                stack[-1][1].addChild(node)
            stack.append((level, node))
        self.outline_view.expandAll()

    def _on_image_dropped(self, data_url: str, suggested_filename: str) -> None:
        # 把 data URL 落地为 Book 资源（去重靠 add_image 内部）；编辑器拿到 book:// URL 即可显示。
        parsed = split_data_url(data_url)
        if parsed is None:
            return
        data, ext = parsed
        res = self.book.add_image(data, ext)
        self.bridge.insertImageAt.emit(f"book://res/{res.filename}", suggested_filename or "")
        self._mark_dirty()

    def action_insert_image(self) -> None:
        if self.current_chapter_id is None:
            QMessageBox.information(self, "插入图片", "请先选择一个章节。")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "插入图片", "",
            "Images (*.png *.jpg *.jpeg *.gif *.svg *.webp)",
        )
        if not path:
            return
        p = Path(path)
        try:
            data = p.read_bytes()
        except Exception as e:
            QMessageBox.critical(self, "插入失败", str(e)); return
        ext = p.suffix.lstrip(".").lower() or "png"
        res = self.book.add_image(data, ext)
        self.bridge.insertImageAt.emit(f"book://res/{res.filename}", p.stem)
        self._mark_dirty()

    def action_import_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入字体", "", "Fonts (*.ttf *.otf *.woff *.woff2)"
        )
        if not path:
            return
        p = Path(path)
        try:
            data = p.read_bytes()
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e)); return
        # 让用户起一个显示名（默认用文件名）
        display, ok = QInputDialog.getText(self, "字体名称", "在编辑器中显示的字体名称：", text=p.stem)
        if not ok or not display.strip():
            display = p.stem
        rec = self.book.add_custom_font(data, p.name, display.strip())
        # 通过 book:// 提供给 WebEngine
        font_url = f"book://font/{rec['filename']}"
        self.bridge.addFontFace.emit(rec["name"], font_url)
        self._mark_dirty()
        self.status_msg.setText(f"已导入字体：{rec['name']}")

    def _on_image_removed(self, src: str) -> None:
        """编辑器删除图片块后，若该资源不再被任何章节引用，则从 Book.resources 中清理。"""
        # 入参 src 可能是 'book://res/x.png' 或 'images/x.png'
        if not src:
            return
        if src.startswith("book://res/"):
            filename = src[len("book://res/"):]
        elif src.startswith("images/"):
            filename = src[len("images/"):]
        else:
            filename = src.rsplit("/", 1)[-1]
        # 引用计数（出现在任意章节 body_html 中）
        used = any((f"images/{filename}" in (ch.body_html or "")) for ch in self.book.chapters)
        if not used:
            self.book.resources = [r for r in self.book.resources if r.filename != filename]
        self._mark_dirty()

    def _on_split_requested(self, chapter_id: str, before_html: str, after_html: str) -> None:
        """编辑器内"由此分章"：原章节保留 before；新章节插到其后承载 after。"""
        ch = self.book.find_chapter(chapter_id)
        if ch is None:
            return
        before_html = self._from_editor_html(before_html or "")
        after_html  = self._from_editor_html(after_html or "")
        ch.body_html = before_html or "<p><br></p>"
        idx = self.book.index_of(chapter_id)
        new_ch = Chapter(
            title="新建章节",
            body_html=after_html or "<p><br></p>",
            parent_id=ch.parent_id,
            level=ch.level,
        )
        self.book.chapters.insert(idx + 1, new_ch)
        self._mark_dirty()
        self._refresh_chapter_tree()
        # 切换到新章节并立即进入重命名
        self._select_chapter(new_ch.chapter_id)
        QTimer.singleShot(0, lambda: self._start_inline_rename(new_ch.chapter_id))

    # ===================== 文件操作 =====================
    def action_new(self) -> None:
        if not self._confirm_discard_changes():
            return
        self.book = new_book_with_template()
        self.current_chapter_id = None
        self._reload_all()
        self._set_dirty(False)
        self.status_msg.setText("已新建书籍")

    def action_open(self) -> None:
        if not self._confirm_discard_changes():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开 EPUB", "", "EPUB (*.epub)")
        if not path:
            return
        try:
            book = import_epub(Path(path))
            book.work_path = Path(path)
            self.book = book
            self.current_chapter_id = None
            self._reload_all()
            self._set_dirty(False)
            self.status_msg.setText(f"已打开：{Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))

    def action_save(self) -> None:
        # "保存工程" = 保存到自动保存目录（始终可恢复）；
        # 同时若已有 work_path 则导出回 EPUB。
        try:
            dump_book(self.book)
            if self.book.work_path is not None and self.book.work_path.suffix.lower() == ".epub":
                export_epub(self.book, self.book.work_path)
                self.status_msg.setText(f"已保存：{self.book.work_path.name}")
                clear_autosave(self.book)
            else:
                self.status_msg.setText("已保存到自动保存目录（请使用『导出 EPUB』生成成品）")
            self._set_dirty(False)
            self.bridge.savedOk.emit()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def action_export(self) -> None:
        suggested = (self.book.title or "book") + ".epub"
        path, _ = QFileDialog.getSaveFileName(self, "导出 EPUB", suggested, "EPUB (*.epub)")
        if not path:
            return
        try:
            export_epub(self.book, Path(path))
            self.book.work_path = Path(path)
            self.status_msg.setText(f"已导出：{Path(path).name}")
            self._set_dirty(False)
            clear_autosave(self.book)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ===================== 全局搜索 =====================
    def _do_search(self) -> None:
        kw = self.search_box.text().strip()
        if not kw:
            return
        # 在所有章节中查找首个包含 kw 的章节并切换
        for ch in self.book.chapters:
            # 极简版：纯文本匹配 body_html
            if kw.lower() in ch.body_html.lower():
                self._select_chapter(ch.chapter_id)
                # 让 webview 内置查找定位
                self.web.findText(kw)
                return
        QMessageBox.information(self, "搜索", f"未找到“{kw}”")

    # ===================== Dirty / 自动保存 =====================
    def _mark_dirty(self) -> None:
        self._set_dirty(True)

    def _set_dirty(self, on: bool) -> None:
        self._dirty = on
        if on:
            self.status_dirty.setText("● 未保存"); self.status_dirty.setStyleSheet("color:#c0392b")
        else:
            self.status_dirty.setText("● 已保存"); self.status_dirty.setStyleSheet("color:#0a0")

    def _do_autosave(self) -> None:
        if not self._dirty:
            return
        try:
            dump_book(self.book)
            # 不清除 dirty（因为只是 autosave，不是用户主动保存）
        except Exception as e:
            print("[autosave]", e, file=sys.stderr)

    def _confirm_discard_changes(self) -> bool:
        if not self._dirty:
            return True
        ret = QMessageBox.question(
            self, "尚未保存",
            "当前书籍有未保存的更改，确定丢弃并继续？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if ret == QMessageBox.Cancel:
            return False
        if ret == QMessageBox.Save:
            self.action_save()
            return not self._dirty
        return True

    # ===================== 关闭事件 =====================
    def closeEvent(self, event) -> None:
        if not self._confirm_discard_changes():
            event.ignore(); return
        # 关闭前先把当前状态再写一次，万一下次启动用户想恢复。
        try:
            dump_book(self.book)
        except Exception:
            pass
        event.accept()

    # ===================== 封面拖拽 =====================
    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self.cover_label:
            if event.type() == QEvent.DragEnter:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction(); return True
            elif event.type() == QEvent.Drop:
                for url in event.mimeData().urls():
                    p = Path(url.toLocalFile())
                    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        data = p.read_bytes()
                        res = self.book.add_image(data, p.suffix)
                        self.book.cover = res
                        self._update_cover_preview()
                        self._mark_dirty()
                        break
                return True
        return super().eventFilter(obj, event)
