# rewritten wit ai for update, didnt test it yet, this file is only for status and unstable i mean!
# 80 % PURE VIBECODE after ai understand and read my Universal hub python code...


# =============================================================================
# DESKTOP_CLIENT/hub.py
# Universal MCP Desktop Client
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 2.1
# Repo: https://github.com/VolkanSah/Universal-MCP-Hub-sandboxed
# =============================================================================
# USAGE:
#   pip install PySide6 httpx Pillow PyPDF2 pandas openpyxl
#   python mcp_desktop.py
#
# CONNECT:
#   1. Enter HF Token + Hub URL in Settings tab → Save
#   2. Go to Connect tab → Connect
#   3. Use Chat tab — select Tool, Provider, Model, upload files
# =============================================================================
# 7 Bugs from Claude AI fixed by Human 14.03.2026 but amazing work from ClaudeAi

import sys
import json
import asyncio
import httpx
import io
import base64
import zipfile
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QTextEdit, QLabel, QTabWidget,
    QStatusBar, QComboBox, QSpinBox, QFileDialog
)
from PySide6.QtCore import QThread, Signal, QObject

# Optional file handling imports — graceful degradation if missing
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import PyPDF2
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# =============================================================================
# Local config — saved to ~/.mcp_desktop.json
# =============================================================================

CONFIG_PATH = Path(__file__).parent / "mcp_desktop.json"
# old CONFIG_PATH = Path.home() / ".mcp_desktop.json"

def load_config() -> dict:
    """Load local config. Returns defaults if not found."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {
        "hf_token":         "",
        "hub_url":          "",
        "default_provider": "",
        "default_model":    "",
        "default_tool":     "llm_complete",
        "font_size":        14,
        "chats":            {},   # chat_id → {"title": str, "messages": [...]}
    }

def save_config(cfg: dict) -> None:
    """Save config to mcp_desktop.json."""
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        print(f"[save_config] WARNING: Could not save config: {e}")


# =============================================================================
# File Handler — extracted from Streamlit app
# Supports: images, text/code, PDF, CSV/XLSX, ZIP
# =============================================================================

def encode_image(image) -> str:
    """Encode PIL image to base64 JPEG string."""
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def process_file(file_path: str) -> dict:
    """
    Process uploaded file into text or image content.
    Returns dict: {"type": "text"|"image"|"error", "content": ...}
    Supports: jpg/png, txt/code, pdf, csv/xlsx, zip
    """
    path      = Path(file_path)
    file_type = path.suffix.lower().lstrip(".")

    # Images — requires Pillow
    if file_type in ["jpg", "jpeg", "png"]:
        if not HAS_PIL:
            return {"type": "error", "content": "Pillow not installed — pip install Pillow"}
        img = Image.open(file_path).convert("RGB")
        return {"type": "image", "content": img, "b64": encode_image(img)}

    # Text / code files
    code_extensions = ["html", "css", "php", "js", "py", "java", "c", "cpp",
                       "ts", "go", "rb", "rs", "sh", "sql", "json", "xml", "md"]
    if file_type in ["txt"] + code_extensions:
        try:
            return {"type": "text", "content": path.read_text(encoding="utf-8")}
        except Exception as e:
            return {"type": "error", "content": str(e)}

    # CSV / Excel — requires pandas
    if file_type in ["csv", "xlsx"]:
        if not HAS_PANDAS:
            return {"type": "error", "content": "pandas not installed — pip install pandas openpyxl"}
        try:
            df = pd.read_csv(file_path) if file_type == "csv" else pd.read_excel(file_path)
            return {"type": "text", "content": df.to_string()}
        except Exception as e:
            return {"type": "error", "content": str(e)}

    # PDF — requires PyPDF2
    if file_type == "pdf":
        if not HAS_PDF:
            return {"type": "error", "content": "PyPDF2 not installed — pip install PyPDF2"}
        try:
            reader  = PyPDF2.PdfReader(file_path)
            content = "".join(
                page.extract_text() for page in reader.pages if page.extract_text()
            )
            return {"type": "text", "content": content}
        except Exception as e:
            return {"type": "error", "content": str(e)}

    # ZIP — recursive text extraction
    if file_type == "zip":
        try:
            text_extensions = (
                ".txt", ".csv", ".py", ".html", ".js", ".css", ".php",
                ".json", ".xml", ".c", ".cpp", ".java", ".cs", ".rb",
                ".go", ".ts", ".swift", ".kt", ".rs", ".sh", ".sql", ".md"
            )
            content = "ZIP Contents:\n"
            with zipfile.ZipFile(file_path) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    try:
                        with z.open(info.filename) as f:
                            if info.filename.lower().endswith(text_extensions):
                                content += f"\n📄 {info.filename}:\n{f.read().decode('utf-8')}\n"
                            else:
                                try:
                                    content += f"\n📄 {info.filename}:\n{f.read().decode('utf-8')}\n"
                                except UnicodeDecodeError:
                                    content += f"\n⚠️ Binary ignored: {info.filename}\n"
                    except Exception as e:
                        content += f"\n❌ Error in {info.filename}: {e}\n"
            return {"type": "text", "content": content}
        except Exception as e:
            return {"type": "error", "content": str(e)}

    return {"type": "error", "content": f"Unsupported format: .{file_type}"}


# =============================================================================
# Async Worker — all Hub HTTP communication
# =============================================================================
class AsyncWorker(QObject):
    result  = Signal(str)
    error   = Signal(str)
    log     = Signal(str)
    tools   = Signal(dict)
    status  = Signal(str)

    def __init__(self, hub_url: str, hf_token: str):
        super().__init__()
        self.hub_url = hub_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {hf_token}"}

    def _run(self, coro):
        """Run coroutine in fresh event loop — thread safe."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def health_check(self):
        """GET / — returns uptime + status."""
        async def _do():
            try:
                async with httpx.AsyncClient() as client:
                    r    = await client.get(f"{self.hub_url}/", headers=self.headers, timeout=10)
                    data = r.json()
                    self.status.emit(f"● connected — uptime: {data.get('uptime_seconds', '?')}s")
                    self.log.emit(f"[health] {json.dumps(data)}")
            except Exception as e:
                self.status.emit("✗ disconnected")
                self.error.emit(f"Health check failed: {e}")
        self._run(_do())

    def fetch_tools(self):
        """POST /api → list_active_tools — returns tools + providers + models."""
        async def _do():
            try:
                async with httpx.AsyncClient() as client:
                    r    = await client.post(
                        f"{self.hub_url}/api",
                        headers={**self.headers, "Content-Type": "application/json"},
                        json={"tool": "list_active_tools", "params": {}},
                        timeout=15,
                    )
                    data = r.json()
                    self.tools.emit(data)
                    self.log.emit(f"[tools] {json.dumps(data)}")
            except Exception as e:
                self.error.emit(f"Fetch tools failed: {e}")
        self._run(_do())

    def call_tool(self, tool_name: str, prompt: str, provider: str = None, model: str = None):
        """
        Generic tool call — POST /api with tool_name + params.
        Handles all tools: llm_complete, summarize, translate, db_query, etc.
        db_query uses 'sql' param instead of 'prompt'.
        """
        async def _do():
            try:
                # db_query needs 'sql' not 'prompt'
                if tool_name == "db_query":
                    params = {"sql": prompt}
                else:
                    params = {
                        "prompt":       prompt,
                        "provider_name": provider or "",
                        "model":        model or "",
                        "max_tokens":   1024,
                    }

                async with httpx.AsyncClient() as client:
                    r = await client.post(
                        f"{self.hub_url}/api",
                        headers={**self.headers, "Content-Type": "application/json"},
                        json={"tool": tool_name, "params": params},
                        timeout=60,
                    )
                    data     = r.json()
                    response = data.get("result", data.get("error", str(data)))
                    if isinstance(response, (dict, list)):
                        response = json.dumps(response, indent=2)
                    self.result.emit(str(response))
                    self.log.emit(f"[{tool_name}] prompt: {prompt[:60]}...")
            except Exception as e:
                self.error.emit(f"Tool call failed: {e}")
        self._run(_do())


# =============================================================================
# Worker Thread
# =============================================================================
class WorkerThread(QThread):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        self.fn()


# =============================================================================
# Main Window
# =============================================================================
class MCPDesktop(QMainWindow):

    # GitHub dark theme
    STYLE = """
        QMainWindow, QWidget {{
            background-color: #0d1117;
            color: #e6edf3;
            font-family: 'Consolas', monospace;
            font-size: {font_size}px;
        }}
        QTabWidget::pane {{
            border: 1px solid #21262d;
            background: #0d1117;
        }}
        QTabBar::tab {{
            background: #161b22;
            color: #8b949e;
            padding: 8px 16px;
            border: 1px solid #21262d;
            border-bottom: none;
        }}
        QTabBar::tab:selected {{
            background: #0d1117;
            color: #58a6ff;
            border-bottom: 2px solid #58a6ff;
        }}
        QTabBar::tab:hover {{ color: #e6edf3; }}
        QLineEdit {{
            background: #161b22;
            border: 1px solid #21262d;
            border-radius: 4px;
            padding: 6px 10px;
            color: #e6edf3;
        }}
        QLineEdit:focus {{ border-color: #58a6ff; }}
        QTextEdit {{
            background: #161b22;
            border: 1px solid #21262d;
            border-radius: 4px;
            padding: 8px;
            color: #e6edf3;
            font-family: 'Consolas', monospace;
        }}
        QPushButton {{
            background: #21262d;
            color: #e6edf3;
            border: 1px solid #30363d;
            border-radius: 4px;
            padding: 6px 14px;
            font-weight: bold;
        }}
        QPushButton:hover {{
            background: #30363d;
            border-color: #58a6ff;
            color: #58a6ff;
        }}
        QPushButton:pressed {{ background: #161b22; }}
        QPushButton#btn_connect {{
            background: #238636; border-color: #2ea043; color: #fff;
        }}
        QPushButton#btn_connect:hover {{ background: #2ea043; }}
        QPushButton#btn_send {{
            background: #1f6feb; border-color: #388bfd; color: #fff; min-width: 80px;
        }}
        QPushButton#btn_send:hover {{ background: #388bfd; }}
        QPushButton#btn_save {{
            background: #6e40c9; border-color: #8957e5; color: #fff;
        }}
        QPushButton#btn_save:hover {{ background: #8957e5; }}
        QPushButton#btn_new_chat {{
            background: #0d1117; border-color: #238636; color: #3fb950; padding: 4px 10px;
        }}
        QPushButton#btn_new_chat:hover {{ background: #238636; color: #fff; }}
        QComboBox {{
            background: #161b22;
            border: 1px solid #21262d;
            border-radius: 4px;
            padding: 5px 8px;
            color: #e6edf3;
        }}
        QComboBox QAbstractItemView {{
            background: #161b22;
            border: 1px solid #30363d;
            color: #e6edf3;
            selection-background-color: #21262d;
        }}
        QSpinBox {{
            background: #161b22;
            border: 1px solid #21262d;
            border-radius: 4px;
            padding: 4px 8px;
            color: #e6edf3;
        }}
        QStatusBar {{
            background: #161b22;
            color: #8b949e;
            border-top: 1px solid #21262d;
            font-size: 12px;
        }}
    """

    def __init__(self):
        super().__init__()
        self.cfg              = load_config()
        self._threads         = []     # list — prevents GC while running
        self._file_cache      = None   # currently loaded file
        self._current_chat_id = None
        self._tools_loading   = False  # prevent duplicate tool fetches
        self._last_models     = []     # Bug 3 fix: cached models for tool filter
        self._last_llm_providers = []  # cached providers

        self.setWindowTitle("Universal MCP Desktop")
        self.setMinimumSize(1000, 720)
        self._apply_style()
        self._build_ui()
        self._set_status("✗ not connected")
        self._load_last_chat()

    def _apply_style(self):
        self.setStyleSheet(self.STYLE.format(font_size=self.cfg.get("font_size", 14)))

    # =========================================================================
    # UI Build
    # =========================================================================
    def _build_ui(self):
        central = QWidget()
        layout  = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_chat(),     "💬 Chat")
        self.tabs.addTab(self._tab_tools(),    "🛠 Tools")
        self.tabs.addTab(self._tab_connect(),  "🔌 Connect")
        self.tabs.addTab(self._tab_settings(), "⚙ Settings")
        self.tabs.addTab(self._tab_logs(),     "📋 Logs")
        layout.addWidget(self.tabs)

        self.setCentralWidget(central)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def _build_header(self) -> QWidget:
        """Header bar: title | Tool | Provider | Model | status"""
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background: #161b22; border-bottom: 1px solid #21262d;")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(8)

        title = QLabel("⬡ Universal MCP Desktop")
        title.setStyleSheet("color: #58a6ff; font-size: 15px; font-weight: bold;")
        row.addWidget(title)
        row.addSpacing(16)

        # Tool selector — in header, always visible
        row.addWidget(self._small_label("Tool:"))
        self.tool_select = QComboBox()
        self.tool_select.addItem("llm_complete")
        self.tool_select.setMinimumWidth(130)
        row.addWidget(self.tool_select)

        # Provider selector
        row.addWidget(self._small_label("Provider:"))
        self.provider_select = QComboBox()
        self.provider_select.addItem("default")
        self.provider_select.setMinimumWidth(120)
        row.addWidget(self.provider_select)

        # Model selector
        row.addWidget(self._small_label("Model:"))
        self.model_select = QComboBox()
        self.model_select.addItem("default")
        self.model_select.setMinimumWidth(200)
        row.addWidget(self.model_select)

        row.addStretch()

        self.status_label = QLabel("✗ not connected")
        self.status_label.setStyleSheet("color: #f85149; font-size: 12px;")
        row.addWidget(self.status_label)

        return header

    # =========================================================================
    # Tab: Chat — multi-chat + file upload
    # =========================================================================
    def _tab_chat(self) -> QWidget:
        tab    = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # Chat selector row
        chat_row = QHBoxLayout()
        chat_row.addWidget(QLabel("Chat:"))
        self.chat_select = QComboBox()
        self.chat_select.setMinimumWidth(280)
        self.chat_select.currentIndexChanged.connect(self._on_chat_selected)
        chat_row.addWidget(self.chat_select)

        new_btn = QPushButton("+ New")
        new_btn.setObjectName("btn_new_chat")
        new_btn.clicked.connect(self._new_chat)
        chat_row.addWidget(new_btn)

        del_btn = QPushButton("🗑")
        del_btn.setToolTip("Delete current chat")
        del_btn.clicked.connect(self._delete_chat)
        chat_row.addWidget(del_btn)

        chat_row.addStretch()
        layout.addLayout(chat_row)

        # Chat output
        self.chat_output = QTextEdit()
        self.chat_output.setReadOnly(True)
        self.chat_output.setPlaceholderText(
            "Connect in Connect tab first — Tool/Provider/Model selectable in header bar above"
        )
        layout.addWidget(self.chat_output)

        # File label — shown when file attached, click to remove
        self.file_label = QLabel("")
        self.file_label.setStyleSheet(
            "color: #3fb950; font-size: 11px; padding: 2px 6px;"
            "background: #0d1117; border: 1px solid #238636; border-radius: 3px;"
        )
        self.file_label.hide()
        self.file_label.mousePressEvent = lambda e: self._clear_file()
        layout.addWidget(self.file_label)

        # Input row
        input_row = QHBoxLayout()

        file_btn = QPushButton("📎")
        file_btn.setToolTip("Attach file (image, PDF, text, ZIP, CSV...)")
        file_btn.clicked.connect(self._attach_file)
        input_row.addWidget(file_btn)

        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText(
            "Enter prompt — Tool/Provider/Model in header bar..."
        )
        self.chat_input.returnPressed.connect(self._send_chat)
        input_row.addWidget(self.chat_input)

        send_btn = QPushButton("Send ▶")
        send_btn.setObjectName("btn_send")
        send_btn.clicked.connect(self._send_chat)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

        return tab

    # =========================================================================
    # Tab: Tools
    # =========================================================================
    def _tab_tools(self) -> QWidget:
        tab    = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("↻ Refresh Tools")
        refresh_btn.clicked.connect(self._fetch_tools)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.tools_output = QTextEdit()
        self.tools_output.setReadOnly(True)
        self.tools_output.setPlaceholderText("Connect first, then refresh...")
        layout.addWidget(self.tools_output)

        return tab

    # =========================================================================
    # Tab: Connect — separate from Settings!
    # =========================================================================
    def _tab_connect(self) -> QWidget:
        tab    = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        layout.addWidget(self._section("Hub URL (edit in Settings tab)"))
        self.connect_url_label = QLabel(self.cfg.get("hub_url", "— not configured —"))
        self.connect_url_label.setStyleSheet("color: #58a6ff; padding: 6px 0;")
        layout.addWidget(self.connect_url_label)

        layout.addWidget(self._section("HF Token"))
        token_ok = bool(self.cfg.get("hf_token"))
        token_status = QLabel("✓ configured" if token_ok else "✗ not set — go to Settings")
        token_status.setStyleSheet("color: #3fb950;" if token_ok else "color: #f85149;")
        layout.addWidget(token_status)

        btn_row = QHBoxLayout()
        conn_btn = QPushButton("🔌 Connect")
        conn_btn.setObjectName("btn_connect")
        conn_btn.clicked.connect(self._connect)
        btn_row.addWidget(conn_btn)

        ping_btn = QPushButton("❤ Ping")
        ping_btn.clicked.connect(self._health_check)
        btn_row.addWidget(ping_btn)

        refresh_btn = QPushButton("↻ Refresh Tools")
        refresh_btn.clicked.connect(self._fetch_tools)
        btn_row.addWidget(refresh_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

        info = QTextEdit()
        info.setReadOnly(True)
        info.setMaximumHeight(80)
        info.setPlainText(
            "Connect = Health Check + auto-load Tools/Providers/Models into header dropdowns.\n"
            "Token is only sent to your own Hub — never stored elsewhere."
        )
        info.setStyleSheet("color: #8b949e; background: #0d1117; border: none; font-size: 12px;")
        layout.addWidget(info)

        return tab

    # =========================================================================
    # Tab: Settings — ONLY config, no connect!
    # =========================================================================
    def _tab_settings(self) -> QWidget:
        tab    = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        layout.addWidget(self._section("HuggingFace Token"))
        self.token_input = QLineEdit(self.cfg.get("hf_token", ""))
        self.token_input.setPlaceholderText("hf_...")
        self.token_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.token_input)

        layout.addWidget(self._section("Hub URL"))
        self.url_input = QLineEdit(self.cfg.get("hub_url", ""))
        self.url_input.setPlaceholderText("https://your-space-name.hf.space")
        layout.addWidget(self.url_input)

        layout.addWidget(self._section("Default Provider (optional)"))
        self.default_provider_input = QLineEdit(self.cfg.get("default_provider", ""))
        self.default_provider_input.setPlaceholderText("e.g. gemini — leave empty for Hub default")
        layout.addWidget(self.default_provider_input)

        layout.addWidget(self._section("Default Model (optional)"))
        self.default_model_input = QLineEdit(self.cfg.get("default_model", ""))
        self.default_model_input.setPlaceholderText("e.g. gemini-2.5-flash — leave empty for Hub default")
        layout.addWidget(self.default_model_input)

        layout.addWidget(self._section("Font Size"))
        font_row = QHBoxLayout()
        self.font_size_input = QSpinBox()
        self.font_size_input.setRange(10, 24)
        self.font_size_input.setValue(self.cfg.get("font_size", 14))
        font_row.addWidget(self.font_size_input)
        font_row.addStretch()
        layout.addLayout(font_row)

        layout.addStretch()

        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 Save Settings")
        save_btn.setObjectName("btn_save")
        save_btn.clicked.connect(self._save_settings)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        info = QTextEdit()
        info.setReadOnly(True)
        info.setMaximumHeight(60)
        info.setPlainText("Saved to ~/.mcp_desktop.json — go to Connect tab to connect.")
        info.setStyleSheet("color: #8b949e; background: #0d1117; border: none; font-size: 12px;")
        layout.addWidget(info)

        return tab

    # =========================================================================
    # Tab: Logs
    # =========================================================================
    def _tab_logs(self) -> QWidget:
        tab    = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("🗑 Clear")
        clear_btn.clicked.connect(lambda: self.log_output.clear())
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("All requests and responses appear here...")
        layout.addWidget(self.log_output)

        return tab

    # =========================================================================
    # Helper Widgets
    # =========================================================================
    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #8b949e; font-size: 11px; margin-top: 8px;")
        return label

    def _small_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #8b949e; font-size: 11px;")
        return label

    # =========================================================================
    # Status + Log
    # =========================================================================
    def _set_status(self, text: str):
        self.status_label.setText(text)
        self.status_bar.showMessage(text)
        connected = "connected" in text and "not" not in text
        color = "#3fb950" if connected else "#f85149"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{ts}] {msg}")

    def _make_worker(self) -> AsyncWorker:
        return AsyncWorker(
            hub_url=self.cfg.get("hub_url", ""),
            hf_token=self.cfg.get("hf_token", ""),
        )

    def _run_in_thread(self, fn):
        """Run in background QThread — list keeps all references alive until done."""
        t = WorkerThread(fn)
        def _cleanup():
            try:
                self._threads.remove(t)
            except ValueError:
                pass
            t.deleteLater()
        t.finished.connect(_cleanup)
        t.start()
        self._threads.append(t)

    # =========================================================================
    # Chat Management — multi-chat with timestamp IDs
    # =========================================================================
    def _new_chat(self):
        """Create new chat session — ID = timestamp."""
        chat_id = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cfg.setdefault("chats", {})[chat_id] = {"title": f"Chat {chat_id}", "messages": []}
        save_config(self.cfg)
        self._refresh_chat_dropdown()
        idx = self.chat_select.findText(chat_id)
        if idx >= 0:
            self.chat_select.setCurrentIndex(idx)
        self._current_chat_id = chat_id
        self.chat_output.clear()

    def _delete_chat(self):
        """Delete currently selected chat."""
        chat_id = self.chat_select.currentText()
        if chat_id and chat_id in self.cfg.get("chats", {}):
            del self.cfg["chats"][chat_id]
            save_config(self.cfg)
            self._refresh_chat_dropdown()
            self.chat_output.clear()

    def _refresh_chat_dropdown(self):
        """Rebuild dropdown — sorted newest first."""
        self.chat_select.blockSignals(True)
        self.chat_select.clear()
        for chat_id in sorted(self.cfg.get("chats", {}).keys(), reverse=True):
            self.chat_select.addItem(chat_id)
        self.chat_select.blockSignals(False)
        if self.chat_select.count() > 0:
            self._on_chat_selected(0)

    def _on_chat_selected(self, idx: int):
        """Load selected chat into output."""
        chat_id = self.chat_select.currentText()
        if not chat_id:
            return
        self._current_chat_id = chat_id
        self.chat_output.clear()
        for msg in self.cfg.get("chats", {}).get(chat_id, {}).get("messages", []):
            self.chat_output.append(msg)

    def _load_last_chat(self):
        """Load most recent chat on startup — create one if none exist."""
        self._refresh_chat_dropdown()
        if self.chat_select.count() > 0:
            self.chat_select.setCurrentIndex(0)
        else:
            self._new_chat()

    def _save_chat_message(self, msg: str):
        """Persist message to current chat history."""
        if not self._current_chat_id:
            return
        self.cfg.setdefault("chats", {}).setdefault(
            self._current_chat_id, {"title": "", "messages": []}
        )["messages"].append(msg)
        save_config(self.cfg)

    # =========================================================================
    # File Attachment
    # =========================================================================
    def _attach_file(self):
        """Open file dialog — process and cache file for next send."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach File", "",
            "Supported (*.jpg *.jpeg *.png *.txt *.py *.php *.js *.html *.css "
            "*.pdf *.csv *.xlsx *.zip *.json *.xml *.md *.sql *.sh *.c *.cpp *.java);;"
            "All files (*.*)"
        )
        if not path:
            return

        result = process_file(path)
        if result["type"] == "error":
            self._log(f"File error: {result['content']}")
            return

        self._file_cache        = result
        self._file_cache["path"] = path
        filename                = Path(path).name
        self.file_label.setText(f"📎 {filename}  (click to remove)")
        self.file_label.show()
        self._log(f"[file] loaded: {filename} ({result['type']})")

    def _clear_file(self):
        """Remove attached file."""
        self._file_cache = None
        self.file_label.hide()

    # =========================================================================
    # Actions
    # =========================================================================
    def _save_settings(self):
        """Save settings only — no connect action."""
        self.cfg["hf_token"]         = self.token_input.text().strip()
        self.cfg["hub_url"]          = self.url_input.text().strip()
        self.cfg["default_provider"] = self.default_provider_input.text().strip()
        self.cfg["default_model"]    = self.default_model_input.text().strip()
        self.cfg["font_size"]        = self.font_size_input.value()
        save_config(self.cfg)
        self._apply_style()
        self.connect_url_label.setText(self.cfg.get("hub_url", "— not configured —"))
        self._log("Settings saved — go to Connect tab to connect.")

    def _connect(self):
        """Health check + auto-fetch tools on success."""
        if not self.cfg.get("hf_token") or not self.cfg.get("hub_url"):
            self._set_status("✗ configure in Settings first!")
            return
        self._set_status("… connecting")
        self._log(f"Connecting to {self.cfg['hub_url']}...")
        w = self._make_worker()
        w.status.connect(self._set_status)
        w.status.connect(lambda s: self._fetch_tools() if "● connected" in s else None)
        w.error.connect(lambda e: self._log(f"ERROR: {e}"))
        w.log.connect(self._log)
        self._run_in_thread(w.health_check)

    def _health_check(self):
        """Ping Hub — update status."""
        w = self._make_worker()
        w.status.connect(self._set_status)
        w.status.connect(lambda s: self._fetch_tools() if "● connected" in s else None)
        w.error.connect(lambda e: self._log(f"ERROR: {e}"))
        w.log.connect(self._log)
        self._run_in_thread(w.health_check)

    def _fetch_tools(self):
        """Fetch tools + providers + models — skip if already loading."""
        if self._tools_loading:
            return
        self._tools_loading = True
        w = self._make_worker()
        w.tools.connect(self._on_tools)
        w.error.connect(lambda e: setattr(self, '_tools_loading', False) or self._log(f"ERROR: {e}"))
        w.log.connect(self._log)
        self._run_in_thread(w.fetch_tools)

    def _on_tools(self, data: dict):
        """Populate tool/provider/model dropdowns from Hub response."""
        self._tools_loading = False
        result = data.get("result", data)
        self.tools_output.setPlainText(
            json.dumps({"active_tools": result}, indent=2)
            if isinstance(result, list)
            else json.dumps(result, indent=2)
        )

        tools = result.get("active_tools", []) if isinstance(result, dict) else []
        try:
            self.tool_select.currentTextChanged.disconnect(self._populate_models_for_tool)
        except RuntimeError:
            pass
        self.tool_select.clear()
        for t in tools:
            self.tool_select.addItem(t)
        idx = self.tool_select.findText(self.cfg.get("default_tool", "llm_complete"))
        if idx >= 0:
            self.tool_select.setCurrentIndex(idx)

        self.provider_select.clear()
        self.provider_select.addItem("default")
        for p in result.get("active_llm_providers", []) if isinstance(result, dict) else []:
            self.provider_select.addItem(p)

        self._last_models = result.get("available_models", []) if isinstance(result, dict) else []
        self._last_llm_providers = result.get("active_llm_providers", []) if isinstance(result, dict) else []

        self._populate_models_for_tool(self.tool_select.currentText())
        self.tool_select.currentTextChanged.connect(self._populate_models_for_tool)

        self._log(f"Tools loaded: {tools}")

    _LLM_TOOLS = {"llm_complete", "code_review", "summarize", "translate"}

    def _populate_models_for_tool(self, tool_name: str):
        """Filter model dropdown based on selected tool — only LLM tools need models."""
        self.model_select.clear()
        self.model_select.addItem("default")
        if tool_name in self._LLM_TOOLS:
            for m in getattr(self, "_last_models", []):
                self.model_select.addItem(m)

    def _send_chat(self):
        """Send prompt to Hub — uses Tool/Provider/Model from header dropdowns."""
        prompt = self.chat_input.text().strip()
        if not prompt:
            return

        tool_name = self.tool_select.currentText() or "llm_complete"
        provider  = self.provider_select.currentText()
        if provider == "default":
            provider = self.cfg.get("default_provider") or None
        model = self.model_select.currentText()
        if model == "default":
            model = self.cfg.get("default_model") or None

        full_prompt = prompt
        if self._file_cache:
            if self._file_cache["type"] == "text":
                full_prompt = f"{prompt}\n\n[File Content]\n{self._file_cache['content']}"
            self._clear_file()

        user_msg = f"\n▶ You [{tool_name}]: {prompt}"
        self.chat_output.append(user_msg)
        self._save_chat_message(user_msg)
        self.chat_input.clear()
        self._log(f"→ tool: {tool_name}, provider: {provider or 'default'}, model: {model or 'default'}")

        w = self._make_worker()
        def on_result(r):
            msg = f"⬡ Hub: {r}\n"
            self.chat_output.append(msg)
            self._save_chat_message(msg)
        w.result.connect(on_result)
        w.error.connect(lambda e: self.chat_output.append(f"✗ Error: {e}\n"))
        w.log.connect(self._log)
        self._run_in_thread(lambda: w.call_tool(tool_name, full_prompt, provider, model))


# =============================================================================
# Entry Point
# =============================================================================
if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app    = QApplication(sys.argv)
    window = MCPDesktop()
    window.show()
    sys.exit(app.exec())
