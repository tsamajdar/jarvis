import os
import json
import shutil
import webbrowser
import subprocess
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from openai import OpenAI, RateLimitError, APIError
import streamlit as st


# ---------------------------------------------------------
# 1. Helper: Resolve Windows Folder Shortcuts Safely
# ---------------------------------------------------------
def resolve_path(folder_path: str) -> Path:
    shortcuts = {
        "desktop": Path.home() / "Desktop",
        "downloads": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "pictures": Path.home() / "Pictures",
        "home": Path.home(),
    }
    cleaned = folder_path.strip().strip('"').strip("'")
    if cleaned.lower() in shortcuts:
        return shortcuts[cleaned.lower()]
    return Path(os.path.expanduser(cleaned)).resolve()


# ---------------------------------------------------------
# 2. Jarvis Tool Implementations (Browser + Desktop + Files)
# ---------------------------------------------------------
def web_search(query: str) -> str:
    """Search the live web using DuckDuckGo."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No search results found."
        formatted = [
            f"- {r.get('title')}: {r.get('body')} (URL: {r.get('href')})"
            for r in results
        ]
        return "\n".join(formatted)
    except Exception as e:
        return f"Search error: {e}"


def read_webpage(url: str) -> str:
    """Fetch and extract readable text from a URL."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:4000] if text else "Page contained no readable text."
    except Exception as e:
        return f"Failed to read webpage: {e}"


def open_in_browser(url: str) -> str:
    """Open a URL in the default desktop web browser."""
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)
        return f"Opened {url} in your default browser."
    except Exception as e:
        return f"Browser launch error: {e}"


def list_folder(folder_path: str = "Desktop") -> str:
    """List files and subfolders inside a Windows directory."""
    try:
        target = resolve_path(folder_path)
        if not target.exists() or not target.is_dir():
            return f"Folder not found: {target}"
        items = []
        for entry in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.is_dir():
                items.append(f"[DIR]  {entry.name}")
            else:
                size_kb = round(entry.stat().st_size / 1024, 1)
                items.append(f"[FILE] {entry.name} ({size_kb} KB)")
        return f"Contents of {target}:\n" + ("\n".join(items[:60]) or "Folder is empty.")
    except Exception as e:
        return f"Error listing folder: {e}"


def search_files(folder_path: str, keyword: str) -> str:
    """Search recursively for files matching a keyword or extension."""
    try:
        target = resolve_path(folder_path)
        if not target.exists():
            return f"Folder not found: {target}"
        matches = [
            str(p)
            for p in target.rglob("*")
            if p.is_file() and keyword.lower() in p.name.lower()
        ]
        if not matches:
            return f"No files matching '{keyword}' found in {target}."
        return "Found matches:\n" + "\n".join(matches[:25])
    except Exception as e:
        return f"Search error: {e}"


def organize_folder(folder_path: str) -> str:
    """Sort loose files in a folder into categorized subfolders."""
    categories = {
        "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"},
        "Documents": {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".csv", ".pptx", ".md"},
        "Videos": {".mp4", ".mkv", ".mov", ".avi"},
        "Audio": {".mp3", ".wav", ".aac", ".flac"},
        "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
        "Code_And_Data": {".py", ".js", ".html", ".css", ".json", ".sql", ".php", ".java"},
        "Installers": {".exe", ".msi"},
    }
    try:
        target = resolve_path(folder_path)
        if not target.exists() or not target.is_dir():
            return f"Folder not found: {target}"

        moved_summary = {}
        for item in target.iterdir():
            if item.is_dir() or item.name.startswith("."):
                continue
            ext = item.suffix.lower()
            dest_folder_name = "Others"
            for cat, exts in categories.items():
                if ext in exts:
                    dest_folder_name = cat
                    break

            dest_dir = target / dest_folder_name
            dest_dir.mkdir(exist_ok=True)
            dest_file = dest_dir / item.name

            # Avoid overwriting files with identical names
            counter = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{item.stem}_{counter}{item.suffix}"
                counter += 1

            shutil.move(str(item), str(dest_file))
            moved_summary[dest_folder_name] = moved_summary.get(dest_folder_name, 0) + 1

        if not moved_summary:
            return f"No loose files needed organizing in {target}."
        summary_lines = [f"- {cat}: {count} file(s)" for cat, count in moved_summary.items()]
        return f"Organized {target}:\n" + "\n".join(summary_lines)
    except Exception as e:
        return f"Error organizing folder: {e}"


def manage_note(action: str, file_path: str, content: str = "") -> str:
    """Create, append, or read a text/markdown file."""
    try:
        target = resolve_path(file_path)
        if action == "read":
            if not target.exists():
                return f"File does not exist: {target}"
            return target.read_text(encoding="utf-8")[:4000]
        elif action == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Saved note to {target}"
        elif action == "append":
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                f.write("\n" + content)
            return f"Appended content to {target}"
        return "Invalid action. Use 'read', 'write', or 'append'."
    except Exception as e:
        return f"File operation error: {e}"


def launch_windows_app(app_name: str) -> str:
    """Launch common Windows desktop applications."""
    allowed_apps = {
        "notepad": ["notepad.exe"],
        "calculator": ["calc.exe"],
        "explorer": ["explorer.exe"],
        "cmd": ["cmd.exe"],
        "powershell": ["powershell.exe"],
        "vscode": ["code"],
        "chrome": ["chrome"],
        "edge": ["msedge"],
    }
    key = app_name.lower().strip()
    if key not in allowed_apps:
        return f"App '{app_name}' is not in the allowed list: {', '.join(allowed_apps.keys())}"
    try:
        subprocess.Popen(allowed_apps[key], shell=True)
        return f"Launched {app_name}."
    except Exception as e:
        return f"Could not launch {app_name}: {e}"


AVAILABLE_FUNCTIONS = {
    "web_search": web_search,
    "read_webpage": read_webpage,
    "open_in_browser": open_in_browser,
    "list_folder": list_folder,
    "search_files": search_files,
    "organize_folder": organize_folder,
    "manage_note": manage_note,
    "launch_windows_app": launch_windows_app,
}


# ---------------------------------------------------------
# 3. Tool Schemas for OpenRouter Function Calling
# ---------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the internet for real-time news, facts, documentation, or URLs.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search keywords"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_webpage",
            "description": "Fetch and read the text content of a specific webpage URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Full https:// URL"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_in_browser",
            "description": "Open a URL in the user's desktop web browser.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to open"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_folder",
            "description": "List files and folders inside a directory (accepts 'Desktop', 'Downloads', 'Documents', or full path).",
            "parameters": {
                "type": "object",
                "properties": {"folder_path": {"type": "string", "description": "Folder name or full path"}},
                "required": ["folder_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Recursively search for files matching a keyword or extension in a folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_path": {"type": "string", "description": "Base folder to search"},
                    "keyword": {"type": "string", "description": "Filename keyword or extension like '.pdf'"},
                },
                "required": ["folder_path", "keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "organize_folder",
            "description": "Automatically sort loose files in a folder into categorized subfolders (Images, Documents, Videos, etc.).",
            "parameters": {
                "type": "object",
                "properties": {"folder_path": {"type": "string", "description": "Folder to organize"}},
                "required": ["folder_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_note",
            "description": "Create, append, or read a text or markdown file on the user's computer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write", "append"]},
                    "file_path": {"type": "string", "description": "Target file path, e.g. Desktop/notes.md"},
                    "content": {"type": "string", "description": "Text content for write/append"},
                },
                "required": ["action", "file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_windows_app",
            "description": "Launch a Windows desktop app (notepad, calculator, explorer, cmd, powershell, vscode, chrome, edge).",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string"}},
                "required": ["app_name"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are JARVIS, a crisp, capable personal AI desktop and browser assistant running on Windows. "
    "Use your tools whenever the user asks to search the web, open websites, inspect or organize folders, "
    "read/write local notes, or launch desktop apps. Report clearly what actions you performed."
)


# ---------------------------------------------------------
# 4. Agent Loop (OpenRouter + NVIDIA Nemotron)
# ---------------------------------------------------------
def run_jarvis_agent(client: OpenAI, model_id: str, conversation: list) -> tuple[str, list]:
    api_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation
    tool_logs = []

    try:
        for _ in range(6):  # Allow up to 6 chained tool calls per turn
            response = client.chat.completions.create(
                model=model_id,
                messages=api_messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
            msg = response.choices[0].message

            if not msg.tool_calls:
                return msg.content or "Done.", tool_logs

            # Record assistant's tool call request
            api_messages.append(
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

            # Execute each requested tool locally
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    fn_args = {}

                fn_to_call = AVAILABLE_FUNCTIONS.get(fn_name)
                result = fn_to_call(**fn_args) if fn_to_call else f"Unknown tool: {fn_name}"

                tool_logs.append({"tool": fn_name, "args": fn_args, "output": result})
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": str(result),
                    }
                )

        return "Reached maximum tool steps for this request.", tool_logs

    except RateLimitError:
        return (
            "⚠️ **Rate Limit Hit (HTTP 429):** The selected free model is temporarily busy or you hit "
            "OpenRouter's free-tier limit (20 req/min, 50 req/day). Switch to another Nemotron model "
            "or `openrouter/free` in the sidebar and try again.",
            tool_logs,
        )
    except APIError as e:
        return f"⚠️ **OpenRouter API Error:** {e}", tool_logs
    except Exception as e:
        return f"⚠️ **Unexpected Error:** {e}", tool_logs


# ---------------------------------------------------------
# 5. Streamlit UI
# ---------------------------------------------------------
st.set_page_config(page_title="J.A.R.V.I.S. Desktop Agent", page_icon="🤖", layout="wide")
st.title("🤖 J.A.R.V.I.S. — Personal Desktop & Browser Agent (OpenRouter)")

with st.sidebar:
    st.header("OpenRouter Configuration")
    api_key = st.text_input(
        "OpenRouter API Key",
        value=os.environ.get("OPENROUTER_API_KEY", ""),
        type="password",
        help="Get your free key at openrouter.ai/keys",
    )

    model_options = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-super-vl:free",
        "nvidia/nemotron-3.5-lightning:free",
        "openrouter/free",
    ]
    selected_model = st.selectbox(
        "Select Free Model",
        model_options,
        index=0,
        help="Nemotron 3 Ultra is best for multi-step reasoning; Lightning is fastest; openrouter/free auto-routes if one provider is busy.",
    )

    custom_model = st.text_input(
        "Or Custom OpenRouter Model ID (Optional)",
        placeholder="e.g. nvidia/nemotron-3.5-super-vl",
    )
    active_model = custom_model.strip() if custom_model.strip() else selected_model

    st.divider()
    st.subheader("Quick Commands")
    presets = [
        "List all files on my Desktop",
        "Organize my Downloads folder by file type",
        "Search the web for today's top AI news and save a summary to Desktop/ai_brief.md",
        "Open YouTube in my browser and launch Notepad",
    ]
    selected_preset = None
    for p in presets:
        if st.button(p, use_container_width=True):
            selected_preset = p

    if st.button("Clear Chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("logs"):
            with st.expander("🛠️ Executed Tools"):
                for log in m["logs"]:
                    st.markdown(f"**`{log['tool']}`** `{log['args']}`")
                    st.code(log["output"][:1000])

user_prompt = st.chat_input("Ask JARVIS to manage folders, search the web, or open apps...")
active_prompt = selected_preset or user_prompt

if active_prompt:
    if not api_key:
        st.error("Please enter your OpenRouter API Key in the sidebar first.")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": active_prompt})
    with st.chat_message("user"):
        st.markdown(active_prompt)

    with st.chat_message("assistant"):
        with st.spinner(f"JARVIS is executing via `{active_model}`..."):
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
                default_headers={
                    "HTTP-Referer": "http://localhost:8501",
                    "X-Title": "JARVIS Desktop Agent",
                },
            )
            clean_history = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages
            ]
            reply, logs = run_jarvis_agent(client, active_model, clean_history)
            st.markdown(reply)
            if logs:
                with st.expander("🛠️ Executed Tools", expanded=True):
                    for log in logs:
                        st.markdown(f"**`{log['tool']}`** `{log['args']}`")
                        st.code(log["output"][:1000])

    st.session_state.messages.append({"role": "assistant", "content": reply, "logs": logs})