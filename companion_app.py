import os
import io
import sys
import json
import math
import time
import asyncio
import shutil
import tempfile
import threading
import webbrowser
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write as write_wav
from faster_whisper import WhisperModel

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from openai import OpenAI
import edge_tts
import pygame

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QLinearGradient
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QDialog,
    QFormLayout,
    QComboBox,
    QFrame,
)

# ---------------------------------------------------------
# 1. User-Configurable Settings (Customizable by Any Client)
# ---------------------------------------------------------
CONFIG_FILE = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "agent_name": "M.I.T.H.U.",
    "user_name": "Tanmoy",
    "business_context": "Software development, AI automation, and digital education",
    "language": "English + Bengali (Bilingual)",
    "voice": "en-IN-NeerjaNeural",
    "whisper_size": "base",
    "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", ""),
    "model_id": "nvidia/nemotron-3.5-lightning:free",
    "n8n_webhook_url": "http://localhost:5678/webhook/agent-ops",
}

VOICE_MAP = {
    "English (India - Neerja)": "en-IN-NeerjaNeural",
    "Bengali (India - Tanishaa)": "bn-IN-TanishaaNeural",
    "Bengali (Bangladesh - Nabanita)": "bn-BD-NabanitaNeural",
    "Hindi (India - Swara)": "hi-IN-SwaraNeural",
    "English (US - Aria)": "en-US-AriaNeural",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------------------------------------------------------
# 2. Thread-Safe UI Signal Bridge
# ---------------------------------------------------------
class AgentSignals(QObject):
    state_changed = Signal(str, str)       # (idle/listening/thinking/speaking, status_label)
    subtitle_updated = Signal(str, str)    # (speaker, message)
    bg_task_status = Signal(str)           # background build banner
    speech_finished = Signal()             # triggers auto-listen when hands-free is on


# ---------------------------------------------------------
# 3. Free Neural Voice Output Engine (edge-tts + pygame)
# ---------------------------------------------------------
class VoiceEngine:
    def __init__(self, signals: AgentSignals):
        self.signals = signals
        pygame.mixer.init()
        self._lock = threading.Lock()

    def speak(self, text: str, voice: str):
        """Synthesizes speech asynchronously and plays through Windows speakers."""
        clean_text = text.replace("*", "").replace("#", "").replace("`", "").strip()
        if not clean_text:
            self.signals.speech_finished.emit()
            return

        def _worker():
            with self._lock:
                tmp_mp3 = Path(tempfile.gettempdir()) / f"companion_tts_{int(time.time()*1000)}.mp3"
                try:
                    self.signals.state_changed.emit("speaking", "Speaking...")
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    communicate = edge_tts.Communicate(clean_text[:900], voice)
                    loop.run_until_complete(communicate.save(str(tmp_mp3)))
                    loop.close()

                    pygame.mixer.music.load(str(tmp_mp3))
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.1)
                    pygame.mixer.music.unload()
                except Exception as e:
                    print(f"[TTS Error] {e}")
                finally:
                    if tmp_mp3.exists():
                        try:
                            tmp_mp3.unlink()
                        except Exception:
                            pass
                    self.signals.state_changed.emit("idle", "Standby")
                    self.signals.speech_finished.emit()

        threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------
# 4. Specialist Team Tools + Background Website Builder
# ---------------------------------------------------------
OUTPUT_WORKSPACE = Path.home() / "Desktop" / "AI_Companion_Workspace"
OUTPUT_WORKSPACE.mkdir(parents=True, exist_ok=True)


def web_research(query: str) -> str:
    """Research Team: Live web search via DuckDuckGo."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No live web results found."
        return "\n".join(
            f"- {r.get('title')}: {r.get('body')} ({r.get('href')})" for r in results
        )
    except Exception as e:
        return f"Research error: {e}"


def open_browser_or_youtube(target: str) -> str:
    """Browser Control: Opens a URL or searches/plays a song or video on YouTube."""
    try:
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
            return f"Opened {target} in browser."
        query = requests.utils.quote(target)
        url = f"https://www.youtube.com/results?search_query={query}"
        webbrowser.open(url)
        return f"Opened YouTube search for '{target}' in your browser."
    except Exception as e:
        return f"Browser error: {e}"


def organize_windows_folder(folder_name: str = "Downloads") -> str:
    """Operations Team: Organizes loose files into categorized subfolders."""
    shortcuts = {
        "desktop": Path.home() / "Desktop",
        "downloads": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
    }
    target = shortcuts.get(folder_name.lower().strip(), Path(folder_name).expanduser())
    if not target.exists() or not target.is_dir():
        return f"Folder not found: {target}"

    categories = {
        "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp"},
        "Documents": {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".csv", ".pptx"},
        "Videos": {".mp4", ".mkv", ".mov"},
        "Archives": {".zip", ".rar", ".7z"},
        "Code": {".py", ".js", ".html", ".css", ".json", ".php"},
    }
    moved = {}
    for item in target.iterdir():
        if item.is_dir() or item.name.startswith("."):
            continue
        ext = item.suffix.lower()
        cat_name = next((c for c, exts in categories.items() if ext in exts), "Others")
        dest_dir = target / cat_name
        dest_dir.mkdir(exist_ok=True)
        dest_file = dest_dir / item.name
        if not dest_file.exists():
            shutil.move(str(item), str(dest_file))
            moved[cat_name] = moved.get(cat_name, 0) + 1

    if not moved:
        return f"No loose files needed sorting in {target}."
    return f"Organized {target}: " + ", ".join(f"{k} ({v})" for k, v in moved.items())


def trigger_n8n_operations(task_summary: str, payload_json: str = "{}") -> str:
    """Operations Team: Sends an event to a local n8n webhook."""
    cfg = load_config()
    webhook = cfg.get("n8n_webhook_url", "").strip()
    if not webhook:
        return "No n8n webhook URL configured in Settings."
    try:
        resp = requests.post(
            webhook,
            json={"task": task_summary, "data": payload_json},
            timeout=6,
        )
        return f"Sent task to n8n workflow (HTTP {resp.status_code})."
    except Exception as e:
        return f"Could not reach local n8n ({webhook}): {e}"


def spawn_background_website_builder(
    project_title: str,
    requirements: str,
    signals: AgentSignals,
) -> str:
    """Dev Team: Builds or upgrades a complete Tailwind CSS webpage in the background."""

    def _build_worker():
        cfg = load_config()
        signals.bg_task_status.emit(f"Dev Team building '{project_title}' in background...")
        try:
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=cfg["openrouter_api_key"],
            )
            safe_slug = "".join(c if c.isalnum() else "_" for c in project_title.lower()).strip("_")
            out_file = OUTPUT_WORKSPACE / f"{safe_slug or 'landing_page'}.html"
            existing_code = out_file.read_text(encoding="utf-8") if out_file.exists() else ""

            prompt = (
                f"Build a complete, modern, single-file responsive HTML5 + Tailwind CSS (via CDN) webpage "
                f"for '{project_title}'. Requirements: {requirements}.\n"
                "Return ONLY raw valid HTML starting with <!DOCTYPE html>. No markdown fences."
            )
            if existing_code:
                prompt += f"\n\nUpgrade this existing HTML file with the requested changes:\n{existing_code[:6000]}"

            resp = client.chat.completions.create(
                model=cfg["model_id"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            html_code = resp.choices[0].message.content or ""
            html_code = html_code.replace("```html", "").replace("```", "").strip()
            out_file.write_text(html_code, encoding="utf-8")

            signals.bg_task_status.emit(f"Ready! Opening {out_file.name} in browser...")
            webbrowser.open(out_file.as_uri())
            time.sleep(4)
            signals.bg_task_status.emit("")
        except Exception as e:
            signals.bg_task_status.emit(f"Dev build error: {e}")

    threading.Thread(target=_build_worker, daemon=True).start()
    return (
        f"Started background Dev Team worker to build '{project_title}'. "
        f"It will automatically open in your browser when finished."
    )


# ---------------------------------------------------------
# 5. Multi-Agent Orchestrator
# ---------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_research",
            "description": "[Research Team] Search live internet facts, competitor data, news, or market prices.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_or_update_website",
            "description": "[Dev Team] Asynchronously build or update an HTML/Tailwind landing page or web app in the background and open it in Chrome.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_title": {"type": "string", "description": "Short project name"},
                    "requirements": {"type": "string", "description": "Design, sections, pricing, and features requested"},
                },
                "required": ["project_title", "requirements"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_browser_or_youtube",
            "description": "[Browser Control] Open a website URL or search/play a song or video on YouTube.",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string", "description": "URL or YouTube song/video search phrase"}},
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "organize_windows_folder",
            "description": "[Operations Team] Sort loose files in Desktop, Downloads, or Documents into categorized folders.",
            "parameters": {
                "type": "object",
                "properties": {"folder_name": {"type": "string"}},
                "required": ["folder_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_n8n_operations",
            "description": "[Operations & Ads Team] Send a structured automation task to local n8n workflows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_summary": {"type": "string"},
                    "payload_json": {"type": "string"},
                },
                "required": ["task_summary"],
            },
        },
    },
]


class MultiAgentBrain:
    def __init__(self, signals: AgentSignals, voice_engine: VoiceEngine):
        self.signals = signals
        self.voice_engine = voice_engine
        self.history = []

    def process_user_message(self, user_text: str):
        def _worker():
            cfg = load_config()
            if not cfg.get("openrouter_api_key"):
                reply = "Please click the gear icon to add your OpenRouter API key first."
                self.signals.subtitle_updated.emit(cfg["agent_name"], reply)
                self.voice_engine.speak(reply, cfg["voice"])
                return

            self.signals.state_changed.emit("thinking", "Orchestrator")
            self.history.append({"role": "user", "content": user_text})

            system_prompt = (
                f"You are {cfg['agent_name']}, a warm, intelligent voice-first personal desktop AI assistant for {cfg['user_name']}. "
                f"Context: {cfg['business_context']}. "
                f"Language preference: {cfg['language']}. "
                "You coordinate 6 teams: Research, Content, Ads, Dev, Support, and Operations. "
                "Because your replies are spoken aloud, keep answers concise, natural, and conversational (1-3 sentences)."
            )

            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=cfg["openrouter_api_key"],
            )
            messages = [{"role": "system", "content": system_prompt}] + self.history[-10:]

            try:
                for _ in range(4):
                    resp = client.chat.completions.create(
                        model=cfg["model_id"],
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="auto",
                        temperature=0.3,
                    )
                    msg = resp.choices[0].message

                    if not msg.tool_calls:
                        final_reply = msg.content or "Done."
                        self.history.append({"role": "assistant", "content": final_reply})
                        self.signals.subtitle_updated.emit(cfg["agent_name"], final_reply)
                        self.voice_engine.speak(final_reply, cfg["voice"])
                        return

                    messages.append(
                        {
                            "role": "assistant",
                            "content": msg.content or "",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in msg.tool_calls
                            ],
                        }
                    )

                    for tc in msg.tool_calls:
                        fn = tc.function.name
                        args = json.loads(tc.function.arguments or "{}")

                        if fn == "web_research":
                            self.signals.state_changed.emit("thinking", "Research Team")
                            out = web_research(**args)
                        elif fn == "build_or_update_website":
                            self.signals.state_changed.emit("thinking", "Dev Team")
                            out = spawn_background_website_builder(
                                args.get("project_title", "Web Project"),
                                args.get("requirements", ""),
                                self.signals,
                            )
                        elif fn == "open_browser_or_youtube":
                            self.signals.state_changed.emit("thinking", "Browser Control")
                            out = open_browser_or_youtube(**args)
                        elif fn == "organize_windows_folder":
                            self.signals.state_changed.emit("thinking", "Operations Team")
                            out = organize_windows_folder(**args)
                        elif fn == "trigger_n8n_operations":
                            self.signals.state_changed.emit("thinking", "Ops / n8n")
                            out = trigger_n8n_operations(**args)
                        else:
                            out = "Tool not recognized."

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "name": fn,
                                "content": str(out),
                            }
                        )

            except Exception as e:
                err_msg = f"I ran into an API issue: {e}"
                self.signals.subtitle_updated.emit(cfg["agent_name"], err_msg)
                self.signals.state_changed.emit("idle", "Standby")
                self.signals.speech_finished.emit()

        threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------
# 6. Animated Taskbar Avatar Widget
# ---------------------------------------------------------
class AnimatedAvatarCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(92, 92)
        self.state = "idle"
        self.phase = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(40)

    def set_state(self, state: str):
        self.state = state
        self.update()

    def _tick(self):
        self.phase += 0.15
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        cx, cy = self.width() / 2, self.height() / 2
        pulse = math.sin(self.phase) * 4

        palette = {
            "idle": (QColor("#38bdf8"), QColor("#0284c7")),
            "listening": (QColor("#4ade80"), QColor("#16a34a")),
            "thinking": (QColor("#facc15"), QColor("#ca8a04")),
            "speaking": (QColor("#c084fc"), QColor("#7e22ce")),
        }
        c_outer, c_inner = palette.get(self.state, palette["idle"])

        aura_r = 35 + (pulse if self.state in ("speaking", "listening") else pulse * 0.4)
        c_outer.setAlpha(75)
        painter.setBrush(QBrush(c_outer))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(int(cx - aura_r), int(cy - aura_r), int(aura_r * 2), int(aura_r * 2))

        grad = QLinearGradient(0, 10, 0, 82)
        grad.setColorAt(0, c_inner)
        grad.setColorAt(1, QColor("#0f172a"))
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(QColor("#e2e8f0"), 2))
        painter.drawEllipse(int(cx - 25), int(cy - 25), 50, 50)

        painter.setBrush(QBrush(QColor("#f8fafc")))
        painter.setPen(Qt.NoPen)
        if self.state == "idle":
            painter.drawRoundedRect(int(cx - 12), int(cy - 5), 8, 3, 1, 1)
            painter.drawRoundedRect(int(cx + 4), int(cy - 5), 8, 3, 1, 1)
        else:
            painter.drawEllipse(int(cx - 12), int(cy - 8), 7, 7)
            painter.drawEllipse(int(cx + 5), int(cy - 8), 7, 7)

        if self.state == "speaking":
            mouth_h = max(3, int(abs(math.sin(self.phase * 2)) * 10))
            painter.drawRoundedRect(int(cx - 8), int(cy + 6), 16, mouth_h, 3, 3)
        elif self.state == "listening":
            painter.drawEllipse(int(cx - 4), int(cy + 6), 8, 8)
        else:
            painter.drawRoundedRect(int(cx - 6), int(cy + 8), 12, 2, 1, 1)


# ---------------------------------------------------------
# 7. Onboarding / Settings Dialog
# ---------------------------------------------------------
class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Personalize Your Voice Companion")
        self.setFixedWidth(430)
        self.cfg = load_config()

        layout = QFormLayout(self)
        self.agent_name_in = QLineEdit(self.cfg["agent_name"])
        self.user_name_in = QLineEdit(self.cfg["user_name"])
        self.context_in = QLineEdit(self.cfg["business_context"])
        self.api_key_in = QLineEdit(self.cfg["openrouter_api_key"])
        self.api_key_in.setEchoMode(QLineEdit.Password)

        self.model_in = QComboBox()
        self.model_in.setEditable(True)
        self.model_in.addItems([
            "nvidia/nemotron-3.5-lightning:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3.5-super-vl:free",
            "openrouter/free",
        ])
        self.model_in.setCurrentText(self.cfg["model_id"])

        self.voice_in = QComboBox()
        for label, code in VOICE_MAP.items():
            self.voice_in.addItem(label, code)
            if code == self.cfg["voice"]:
                self.voice_in.setCurrentText(label)

        self.whisper_in = QComboBox()
        self.whisper_in.addItems(["tiny", "base", "small"])
        self.whisper_in.setCurrentText(self.cfg.get("whisper_size", "base"))

        self.n8n_in = QLineEdit(self.cfg["n8n_webhook_url"])

        layout.addRow("Companion Name:", self.agent_name_in)
        layout.addRow("Your Name:", self.user_name_in)
        layout.addRow("Role / Business:", self.context_in)
        layout.addRow("OpenRouter API Key:", self.api_key_in)
        layout.addRow("Nemotron Model:", self.model_in)
        layout.addRow("Voice & Language:", self.voice_in)
        layout.addRow("Offline Whisper Size:", self.whisper_in)
        layout.addRow("n8n Webhook (Optional):", self.n8n_in)

        save_btn = QPushButton("Save & Apply")
        save_btn.clicked.connect(self._save)
        layout.addRow(save_btn)

    def _save(self):
        self.cfg["agent_name"] = self.agent_name_in.text().strip() or "M.I.T.H.U."
        self.cfg["user_name"] = self.user_name_in.text().strip() or "Friend"
        self.cfg["business_context"] = self.context_in.text().strip()
        self.cfg["openrouter_api_key"] = self.api_key_in.text().strip()
        self.cfg["model_id"] = self.model_in.currentText().strip()
        self.cfg["voice"] = self.voice_in.currentData()
        self.cfg["whisper_size"] = self.whisper_in.currentText()
        self.cfg["n8n_webhook_url"] = self.n8n_in.text().strip()
        save_config(self.cfg)
        self.accept()


# ---------------------------------------------------------
# 8. Main Floating Desktop HUD with Hands-Free Voice Loop
# ---------------------------------------------------------
class DesktopCompanionHUD(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.signals = AgentSignals()
        self.voice_engine = VoiceEngine(self.signals)
        self.brain = MultiAgentBrain(self.signals, self.voice_engine)
        self.hands_free_active = False
        self.whisper_model = None
        self._drag_pos = None

        self._init_ui()
        self._connect_signals()
        self._dock_bottom_right()
        self._preload_whisper()

    def _preload_whisper(self):
        """Loads local offline Whisper STT in background so voice responses are fast."""
        def _loader():
            try:
                self.signals.bg_task_status.emit("Loading offline Whisper voice model...")
                size = self.cfg.get("whisper_size", "base")
                self.whisper_model = WhisperModel(size, device="cpu", compute_type="int8")
                self.signals.bg_task_status.emit("")
            except Exception as e:
                self.signals.bg_task_status.emit(f"Whisper load notice: {e}")

        threading.Thread(target=_loader, daemon=True).start()

    def _init_ui(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(430)

        container = QFrame(self)
        container.setObjectName("hudFrame")
        container.setStyleSheet("""
            #hudFrame {
                background-color: rgba(15, 23, 42, 240);
                border: 1px solid rgba(56, 189, 248, 150);
                border-radius: 18px;
            }
            QLabel { color: #f1f5f9; font-family: 'Segoe UI'; }
            QLineEdit {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 7px 10px;
                font-size: 13px;
            }
            QPushButton {
                background-color: #0284c7;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 7px 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0369a1; }
            QTextEdit {
                background-color: rgba(30, 41, 59, 185);
                color: #e2e8f0;
                border: none;
                border-radius: 10px;
                padding: 8px;
                font-size: 12.5px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)

        # Header Row
        top_row = QHBoxLayout()
        self.avatar = AnimatedAvatarCanvas()
        top_row.addWidget(self.avatar)

        title_col = QVBoxLayout()
        self.title_lbl = QLabel(self.cfg["agent_name"])
        self.title_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.team_lbl = QLabel("● Standby | Click '🎤 Live' to Talk")
        self.team_lbl.setStyleSheet("color: #38bdf8; font-size: 11.5px;")
        self.bg_lbl = QLabel("")
        self.bg_lbl.setStyleSheet("color: #facc15; font-size: 11px;")
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.team_lbl)
        title_col.addWidget(self.bg_lbl)
        top_row.addLayout(title_col, 1)

        gear_btn = QPushButton("⚙")
        gear_btn.setFixedSize(30, 30)
        gear_btn.setStyleSheet("background-color: #334155;")
        gear_btn.clicked.connect(self._open_settings)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet("background-color: #7f1d1d;")
        close_btn.clicked.connect(QApplication.quit)

        top_row.addWidget(gear_btn)
        top_row.addWidget(close_btn)
        layout.addLayout(top_row)

        # Live Transcript Box
        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setFixedHeight(130)
        self.transcript.setHtml(
            f"<b>{self.cfg['agent_name']}:</b> Hello {self.cfg['user_name']}! "
            "Click <b>🎤 Live</b> for hands-free voice conversation, or type below."
        )
        layout.addWidget(self.transcript)

        # Controls Row: Hands-Free Voice Button + Text Input
        input_row = QHBoxLayout()
        self.mic_btn = QPushButton("🎤 Live")
        self.mic_btn.setFixedWidth(74)
        self.mic_btn.setStyleSheet("background-color: #15803d;")
        self.mic_btn.setToolTip("Toggle continuous hands-free voice conversation")
        self.mic_btn.clicked.connect(self._toggle_hands_free)

        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Or type a command here...")
        self.input_box.returnPressed.connect(self._send_text)

        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send_text)

        input_row.addWidget(self.mic_btn)
        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

    def _connect_signals(self):
        self.signals.state_changed.connect(self._on_state_changed)
        self.signals.subtitle_updated.connect(self._on_subtitle)
        self.signals.bg_task_status.connect(self.bg_lbl.setText)
        self.signals.speech_finished.connect(self._on_speech_done_auto_listen)

    def _dock_bottom_right(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        x = screen.right() - self.width() - 18
        y = screen.bottom() - self.height() - 18
        self.move(x, y)

    def _on_state_changed(self, state: str, team: str):
        self.avatar.set_state(state)
        self.team_lbl.setText(f"● {state.capitalize()} | {team}")

    def _on_subtitle(self, speaker: str, text: str):
        self.transcript.append(f"<br><b>{speaker}:</b> {text}")
        self.transcript.verticalScrollBar().setValue(
            self.transcript.verticalScrollBar().maximum()
        )

    def _send_text(self):
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self._on_subtitle(self.cfg["user_name"], text)
        self.brain.process_user_message(text)

    # -----------------------------------------------------
    # Hands-Free Continuous Voice Loop (sounddevice + Whisper)
    # -----------------------------------------------------
    def _toggle_hands_free(self):
        self.hands_free_active = not self.hands_free_active
        if self.hands_free_active:
            self.mic_btn.setText("🔴 Stop")
            self.mic_btn.setStyleSheet("background-color: #dc2626;")
            self._listen_turn()
        else:
            self.mic_btn.setText("🎤 Live")
            self.mic_btn.setStyleSheet("background-color: #15803d;")
            self.signals.state_changed.emit("idle", "Standby")

    def _on_speech_done_auto_listen(self):
        """Re-opens microphone automatically 0.5s after voice reply ends."""
        if self.hands_free_active:
            QTimer.singleShot(500, self._listen_turn)

    def _listen_turn(self):
        def _mic_worker():
            if not self.hands_free_active:
                return
            try:
                fs = 16000
                chunk_sec = 0.5
                max_seconds = 12
                silence_limit = 2.0  # Stop recording after 2s of silence once speech starts
                energy_threshold = 350

                self.signals.state_changed.emit("listening", "Listening (Speak Now)...")
                frames = []
                speech_started = False
                silent_chunks = 0

                with sd.InputStream(samplerate=fs, channels=1, dtype="int16") as stream:
                    for _ in range(int(max_seconds / chunk_sec)):
                        if not self.hands_free_active:
                            return
                        data, _ = stream.read(int(fs * chunk_sec))
                        frames.append(data.copy())
                        rms = np.sqrt(np.mean(data.astype(np.float32) ** 2))

                        if rms > energy_threshold:
                            speech_started = True
                            silent_chunks = 0
                        elif speech_started:
                            silent_chunks += 1
                            if silent_chunks * chunk_sec >= silence_limit:
                                break

                if not speech_started or not self.hands_free_active:
                    # Nothing spoken — loop quietly while hands-free stays active
                    if self.hands_free_active:
                        QTimer.singleShot(200, self._listen_turn)
                    return

                self.signals.state_changed.emit("thinking", "Transcribing Speech...")
                audio_np = np.concatenate(frames, axis=0)
                wav_buf = io.BytesIO()
                write_wav(wav_buf, fs, audio_np)
                wav_buf.seek(0)

                if self.whisper_model is None:
                    self.whisper_model = WhisperModel(
                        self.cfg.get("whisper_size", "base"),
                        device="cpu",
                        compute_type="int8",
                    )

                segments, _ = self.whisper_model.transcribe(wav_buf, beam_size=1)
                spoken_text = " ".join(seg.text for seg in segments).strip()

                if not spoken_text:
                    if self.hands_free_active:
                        QTimer.singleShot(200, self._listen_turn)
                    return

                self.signals.subtitle_updated.emit(self.cfg["user_name"], spoken_text)
                self.brain.process_user_message(spoken_text)

            except Exception as e:
                self.hands_free_active = False
                self.mic_btn.setText("🎤 Live")
                self.mic_btn.setStyleSheet("background-color: #15803d;")
                self.signals.state_changed.emit("idle", "Standby")
                self.signals.subtitle_updated.emit("System", f"Microphone notice: {e}")

        threading.Thread(target=_mic_worker, daemon=True).start()

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self.cfg = load_config()
            self.title_lbl.setText(self.cfg["agent_name"])

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() == Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


if __name__ == "__main__":
    app = QApplication(sys.argv)
    hud = DesktopCompanionHUD()
    hud.show()
    sys.exit(app.exec())