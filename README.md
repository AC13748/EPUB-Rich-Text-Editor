# PySave EPUB Editor

桌面端 EPUB 编辑器。用 PySide6 做壳，编辑画布跑在 `QWebEngineView` 里，操作手感向 Word / Google Docs 看齐：所见即所得，块级模型，浮动工具栏，斜杠命令，自动保存，多主题导出。

## 环境要求

- **Python ≥ 3.9**（推荐 3.10+）
- Windows / macOS / Linux

## 安装

```bash
pip install -r requirements.txt
```

依赖：

| 包 | 用途 |
| --- | --- |
| `PySide6 ≥ 6.5` | 主框架 + WebEngine |
| `ebooklib ≥ 0.18` | EPUB 读写 |
| `lxml ≥ 5.0` | XHTML 解析（替代 BS4 路径，速度/容错都更稳） |
| `beautifulsoup4 ≥ 4.12` | 个别容错降级路径 |
| `Pillow ≥ 10.0` | 图片元数据/缩放 |

## 运行

```bash
python main.py
```

## 功能总览

### 编辑

- **WYSIWYG 富文本画布**：单栏编辑，所见即所得，不暴露 HTML 源码
- **块级模型**：`Enter` 新块、`Shift+Enter` 软换行、行首 `/` 调出块菜单
- **浮动工具栏**（选区出现）：B / I / U / 删除线 / 行内代码 / 高亮 / 链接 / 清除格式
- **顶部块工具栏**（始终在）：左/中/右/两端对齐、字号、字体、行距、格式刷
- **选区信息浮窗**：划选文字时显示当前**字体 / 字号 / 粗斜 / 行距 / 对齐**
- **格式刷**：单击复制一次格式，双击锁定持续模式，`Esc` 退出
- **Markdown 快捷输入**：行首 `# / ## / ### / > / \`\`\` / - / 1.` 立即转块
- **斜杠命令**：段落、标题 1–3、引用、代码块、列表、图片、表格、分隔线
- **图片**：拖拽 / 粘贴 / 工具栏插入；图片浮动栏可调宽度、对齐、删除
- **智能粘贴**：从网页/Word 粘贴时自动清洗为 EPUB 兼容标签；`Ctrl+Shift+V` 强制纯文本
- **页面缩放**：`Ctrl + 滚轮`、`Ctrl++` / `Ctrl+-` / `Ctrl+0`

### 项目管理

- **章节树**：左侧拖拽排序 / 右键新建·重命名·删除·拆分
- **快速分章**：按正则、章节标记或固定字符数自动切分长文档
- **元数据 & 大纲**：右侧面板编辑书名 / 作者 / 语言 / 封面 / 出版者，大纲跟随标题实时更新
- **实时自动保存**：每 5 秒落盘到 `~/.pysave_epub/.autosave/`，崩溃后启动自动恢复

### 排版与导出

- **导出主题**：经典书籍（classic）/ 现代简约（modern）/ 学术（academic）
- **页边距**：上下左右独立 mm 输入
- **行距**：1.0 / 1.15 / 1.5 / 1.75 / 2.0 / 2.5 / 3.0，或自定义
- **自定义字体**：导入 ttf/otf/woff/woff2，自动注入 `@font-face` 并打包到 EPUB 的 `fonts/`
- **EPUB 3 输出**：标准 OPF + Nav 包，图片归集到 `EPUB/images/`、字体到 `EPUB/fonts/`，重写所有 `src`

## 快捷键

| 操作 | 快捷键 |
| --- | --- |
| 新建 / 打开 / 保存 | `Ctrl+N` / `Ctrl+O` / `Ctrl+S` |
| 导出 EPUB | `Ctrl+Shift+E` |
| 加粗 / 斜体 / 下划线 / 链接 | `Ctrl+B` / `Ctrl+I` / `Ctrl+U` / `Ctrl+K` |
| 左 / 中 / 右 / 两端对齐 | `Ctrl+L` / `Ctrl+E` / `Ctrl+R` / `Ctrl+J` |
| 软换行 | `Shift+Enter` |
| 块菜单 | 行首输入 `/` |
| 首行缩进 ± | `Tab` / `Shift+Tab` |
| 缩放 | `Ctrl+滚轮` / `Ctrl+=` / `Ctrl+-` / `Ctrl+0` |
| 纯文本粘贴 | `Ctrl+Shift+V` |
| 退出格式刷 | `Esc` |

## 目录结构

```
epub/
├── main.py                  程序入口（注册 book:// scheme，启动 QApplication）
├── requirements.txt
├── app/
│   ├── main_window.py       主窗口三栏布局 + 顶部菜单 + 工具栏接线
│   ├── bridge.py            QWebChannel JS↔Python 桥（信号定义）
│   ├── book_model.py        Book / Chapter / Resource / 自定义字体模型
│   ├── book_scheme.py       book:// URL Scheme 与资源处理器
│   ├── epub_io.py           EPUB 读 / 写（基于 ebooklib + lxml）
│   ├── xhtml_utils.py       XHTML 解析与序列化辅助
│   ├── split_engine.py      快速分章引擎
│   ├── autosave.py          自动保存与恢复（含字体/排版字段）
│   └── theme.py             导出主题 CSS 构建
└── web/
    ├── editor.html          富文本编辑器壳
    ├── editor.css           编辑区 / 工具栏 / 浮窗样式
    └── editor.js            块级编辑器内核（对齐 / 字体 / 格式刷 / 行距 / 缩放）
```

## 架构要点

- **编辑器内核** 运行在 `QWebEngineView`（Chromium）里，基于 `contenteditable` + 自研块级模型实现富文本，不在 Python 侧重新发明 ProseMirror。
- **唯一事实源** 是 Python 侧的 `Book / Chapter / Resource`。JS 通过 `QWebChannel` 把 HTML 变更回报，autosave 只针对 Python 模型快照。
- **`book://` URL Scheme**：自定义协议把内存里的图片 / 字体直接喂给 WebEngine，无需落盘。
  - `book://res/<filename>` → `Book.resources` 中的图片
  - `book://font/<filename>` → `Book.custom_fonts` 中的字体文件
  - 注册为 `LocalScheme`，让 `file://` 加载的 `editor.html` 可以跨 scheme 引用
  - Handler 用魔数嗅探真实 MIME，避免 `media_type` 错配导致的破损图占位
- **样式覆盖策略**：原 EPUB 段落里常带 `style="font-family:..."` 这类高优先级 inline 样式。切换字体/字号时先递归 strip 选区内所有同名属性，再 wrap span 写入新值，确保用户操作必然生效。
- **导出主题** 与 **编辑主题** 解耦：
  - 编辑主题（护眼/纯白/深色）只影响 WebView 显示
  - 导出主题（经典/现代/学术）只影响最终 EPUB 的 `book.css`，含 `@page margin` 与 body 排版
- **autosave** 序列化 Book 全字段（含 `margin_*_mm`、`custom_fonts` 的 base64 字节），崩溃恢复后字体与排版完整还原。

## 调试小贴士

- 编辑器 DevTools：在 `main.py` 启动前设置环境变量 `QTWEBENGINE_REMOTE_DEBUGGING=9222`，浏览器访问 `http://localhost:9222` 即可。
- 图片显示问题先看 Network：
  - 状态非 200 → scheme 通道被拦，检查 `book_scheme.register_scheme()` 是否在 `QApplication` 之前被调用
  - 200 但 Type 是 `application/octet-stream` → `_sniff_image_mime` 没命中（非常规格式），按需扩展
- autosave 路径：默认 `~/.pysave_epub/.autosave/state.json`；如果打开过文件，则在该文件父目录下的 `.autosave/`
