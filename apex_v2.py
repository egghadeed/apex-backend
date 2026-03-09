# apex_v2.py — Redesigned UI
# Design: Precision minimalism, 21st.dev-inspired dark tool aesthetic
# Accent: Cyan #00D4FF | Font: JetBrains Mono/Consolas | Near-black surfaces
"""
Apex Assistant v2 — Refined Dark Tool UI
=========================================
Hotkeys:
  Ctrl+Shift+S  → Screenshot
  Ctrl+Shift+H  → Highlight text
  Ctrl+Shift+A  → Open chat
  Ctrl+Shift+Q  → Quit

pip install anthropic pillow pynput pyperclip mss
"""

import anthropic
import base64
import threading
import queue
import time
import sys
import os
import io
from datetime import datetime

if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import scrolledtext
import pyperclip
from pynput import keyboard
from PIL import Image, ImageTk
import mss

# ── Config ────────────────────────────────────────────────────────────────────
SYSTEM = (
    "You are Apex, a helpful desktop AI assistant. "
    "You can see screenshots and read text the user highlights. "
    "Be concise but thorough when directed. "
    "In general, answer questions with minimal working."
)

import json
import urllib.request
import urllib.error

PROVIDER = "claude"   # kept for legacy references, backend decides actual model
MODEL    = "claude-sonnet-4-20250514"

msg_queue: queue.Queue = queue.Queue()

CHAT_HISTORY_DIR = os.path.join(os.path.expanduser("~"), ".apex_chats")
SCREENSHOTS_DIR  = os.path.join(os.path.expanduser("~"), ".apex_screenshots")
AUTH_FILE        = os.path.join(os.path.expanduser("~"), ".apex_auth")

# ── Backend URL — change to your deployed URL in production ──────────────────
BACKEND_URL   = os.getenv("APEX_BACKEND_URL", "https://apex-assistant-api.onrender.com")
if not BACKEND_URL.startswith("https://"):
    raise SystemExit(f"APEX_BACKEND_URL must use https://. Got: {BACKEND_URL}")
VERSION       = "1.0.0"   # bump this before each release
DASHBOARD_URL = "https://apex-assistant.vercel.app/dashboard"

# ── Update check ──────────────────────────────────────────────────────────────

def _parse_version(v: str) -> tuple:
    """Convert '1.2.3' to (1, 2, 3) for comparison."""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0, 0, 0)

_pending_update_version: str = ""   # set by background thread; read by main thread

def check_for_updates():
    """Runs in a background thread on startup. Sets _pending_update_version if newer."""
    global _pending_update_version
    try:
        req = urllib.request.Request(
            f"{BACKEND_URL}/version",
            headers={"User-Agent": f"ApexAssistant/{VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        latest = data.get("version", "")
        if latest and _parse_version(latest) > _parse_version(VERSION):
            _pending_update_version = latest
    except Exception:
        pass  # Never crash on update check failure

def _show_update_dialog_if_pending(window: tk.Tk):
    """Called from main thread via after(). Safe to create messageboxes here."""
    if _pending_update_version:
        import webbrowser
        from tkinter import messagebox
        result = messagebox.askyesno(
            "Update Available",
            f"Apex Assistant v{_pending_update_version} is available.\n"
            f"You have v{VERSION}.\n\n"
            "Open dashboard to download?",
            parent=window,
        )
        if result:
            webbrowser.open(DASHBOARD_URL)

# ── Model metadata (mirrors backend config) ───────────────────────────────────
MODEL_DISPLAY = {
    "gpt-4o-mini":               "GPT-4o mini",
    "gpt-4o":                    "GPT-4o",
    "gpt-4-turbo":               "GPT-4 Turbo",
    "o1-mini":                   "o1 mini",
    "o1":                        "o1",
    "o3-mini":                   "o3 mini",
    "claude-haiku-4-5-20251001": "Claude Haiku",
    "claude-sonnet-4-20250514":  "Claude Sonnet",
    "claude-opus-4-20250514":    "Claude Opus",
}
VISION_CAPABLE_CLIENT = {
    "gpt-4o-mini":               True,
    "gpt-4o":                    True,
    "gpt-4-turbo":               True,
    "o1-mini":                   False,
    "o1":                        True,
    "o3-mini":                   False,
    "claude-haiku-4-5-20251001": True,
    "claude-sonnet-4-20250514":  True,
    "claude-opus-4-20250514":    True,
}

# ── Auth state ────────────────────────────────────────────────────────────────
_access_token:  str = ""
_refresh_token: str = ""
_user_info:     dict = {}
_preferred_model: str = ""   # empty = use tier default

def save_auth(access_token: str, refresh_token: str, user: dict):
    global _access_token, _refresh_token, _user_info
    _access_token  = access_token
    _refresh_token = refresh_token
    _user_info     = user
    try:
        with open(AUTH_FILE, "w") as f:
            json.dump({"access_token": access_token,
                       "refresh_token": refresh_token,
                       "user": user,
                       "preferred_model": _preferred_model}, f)
        import stat
        os.chmod(AUTH_FILE, stat.S_IRUSR | stat.S_IWUSR)  # owner read/write only
    except Exception:
        pass

def load_auth() -> bool:
    """Load saved tokens. Returns True if tokens exist (not validated yet)."""
    global _access_token, _refresh_token, _user_info, _preferred_model
    try:
        if os.path.exists(AUTH_FILE):
            with open(AUTH_FILE) as f:
                data = json.load(f)
            _access_token    = data.get("access_token", "")
            _refresh_token   = data.get("refresh_token", "")
            _user_info       = data.get("user", {})
            _preferred_model = data.get("preferred_model", "")
            return bool(_access_token and _refresh_token)
    except Exception:
        pass
    return False

def clear_auth():
    global _access_token, _refresh_token, _user_info
    _access_token = _refresh_token = ""
    _user_info = {}
    try:
        os.remove(AUTH_FILE)
    except Exception:
        pass

def _api_request(method: str, path: str, body: dict = None,
                 token: str = None, timeout: int = 15) -> dict:
    """Simple JSON API request using stdlib (no requests dependency)."""
    url  = f"{BACKEND_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def refresh_access_token() -> bool:
    """Try to get a new access token using the refresh token."""
    global _access_token, _refresh_token
    try:
        res = _api_request("POST", "/auth/refresh",
                           {"refresh_token": _refresh_token})
        _access_token  = res["access_token"]
        _refresh_token = res["refresh_token"]
        save_auth(_access_token, _refresh_token, _user_info)
        return True
    except Exception:
        return False

# ── Design Tokens — Precision Minimalism ─────────────────────────────────────
BG_BASE       = "#0a0a0b"
BG_SURFACE    = "#111113"
BG_SURFACE2   = "#18181b"
BG_HOVER      = "#1f1f23"
BG_SIDEBAR    = "#08080a"
BORDER        = "#1e1e22"
BORDER_FOCUS  = "#00D4FF"

TEXT_PRIMARY   = "#f0f0f2"
TEXT_SECONDARY = "#70707a"
TEXT_MUTED     = "#3a3a42"

CYAN          = "#00D4FF"
CYAN_DIM      = "#0d2a33"
CYAN_ACTIVE   = "#0a1f26"
CYAN_HOVER    = "#22e5ff"

USER_BG       = "#0d1a1f"
ASST_BG       = "#111113"
RED           = "#ff4d4d"

# Font: JetBrains Mono → Consolas → Courier New fallback
def _resolve_font():
    import tkinter.font as tkfont
    root = tk.Tk()
    root.withdraw()
    available = tkfont.families()
    root.destroy()
    for f in ("JetBrains Mono", "Cascadia Code", "Consolas", "Courier New"):
        if f in available:
            return f
    return "Courier New"

try:
    _MONO = _resolve_font()
except Exception:
    _MONO = "Consolas"

FONT_SANS = _MONO   # everything monospaced — the 21st.dev tool aesthetic
FONT_MONO = _MONO

SIDEBAR_W  = 62
TOPBAR_H   = 38

# ── Config persistence (kept for chat history dir, screenshots dir) ───────────

def load_config():
    # Thin shim — model/provider now comes from the backend based on user tier
    return {"provider": "claude", "model": "claude-sonnet-4-20250514"}

def save_config(provider: str, model: str):
    pass  # no-op — backend owns model selection


def ensure_chat_history_dir():
    try: os.makedirs(CHAT_HISTORY_DIR, exist_ok=True)
    except Exception: pass

def ensure_screenshots_dir():
    try: os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    except Exception: pass

def save_screenshot_to_disk(img: Image.Image, prompt: str) -> str:
    """Save screenshot PNG to SCREENSHOTS_DIR. Returns filepath or ''."""
    ensure_screenshots_dir()
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_prompt = prompt[:40].replace(" ", "_").replace("/", "-").replace("\\", "-")
    filename = f"{ts}_{safe_prompt}.png"
    filepath = os.path.join(SCREENSHOTS_DIR, filename)
    try:
        img.save(filepath, format="PNG")
        return filepath
    except Exception as e:
        print(f"Error saving screenshot: {e}")
        return ""

def delete_all_screenshots():
    """Delete every PNG in SCREENSHOTS_DIR."""
    ensure_screenshots_dir()
    deleted = 0
    try:
        for f in os.listdir(SCREENSHOTS_DIR):
            if f.lower().endswith(".png"):
                try:
                    os.remove(os.path.join(SCREENSHOTS_DIR, f))
                    deleted += 1
                except Exception:
                    pass
    except Exception:
        pass
    return deleted

def save_chat_to_file(messages: list, title: str = None):
    import json
    ensure_chat_history_dir()
    if not title:
        title = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    import re
    safe_title = re.sub(r'[^\w\s\-.]', '_', title)[:80]
    filename = f"{safe_title}.json"
    filepath = os.path.join(CHAT_HISTORY_DIR, filename)
    try:
        clean_messages = []
        for msg in messages:
            if isinstance(msg["content"], str):
                clean_messages.append(msg)
            else:
                text_parts = [p.get("text", "") for p in msg["content"] if p.get("type") == "text"]
                clean_messages.append({
                    "role": msg["role"],
                    "content": " ".join(text_parts) if text_parts else "[Image]"
                })
        with open(filepath, "w") as f:
            json.dump({"title": title, "messages": clean_messages}, f, indent=2)
        return filepath
    except Exception as e:
        print(f"Error saving chat: {e}")
        return None

def load_chat_from_file(filepath: str):
    import json
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        return data.get("messages", [])
    except Exception as e:
        print(f"Error loading chat: {e}")
        return []

def get_chat_history_files():
    ensure_chat_history_dir()
    try:
        files = [f for f in os.listdir(CHAT_HISTORY_DIR) if f.endswith(".json")]
        files.sort(key=lambda f: os.path.getmtime(
            os.path.join(CHAT_HISTORY_DIR, f)), reverse=True)
        return files
    except Exception:
        return []

# ── Utility ───────────────────────────────────────────────────────────────────

def pil_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()

def ask_claude(messages: list, on_chunk=None) -> str:
    """Stream chat via Apex backend. Handles token refresh automatically."""
    global _access_token

    url      = f"{BACKEND_URL}/chat/stream"
    payload  = {"messages": messages}
    if _preferred_model:
        payload["model"] = _preferred_model
    body = json.dumps(payload).encode()

    def do_request(token: str) -> urllib.request.Request:
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token}")
        return req

    full = ""
    tried_refresh = False

    while True:
        req = do_request(_access_token)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                buffer = ""
                while True:
                    chunk = resp.read(64)
                    if not chunk:
                        break
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n\n" in buffer:
                        line, buffer = buffer.split("\n\n", 1)
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        try:
                            event = json.loads(payload)
                        except Exception:
                            continue
                        if event.get("type") == "chunk":
                            text = event.get("text", "")
                            full += text
                            if on_chunk:
                                on_chunk(text)
                        elif event.get("type") == "error":
                            raise RuntimeError(event.get("message", "Unknown error"))
            return full

        except urllib.error.HTTPError as e:
            if e.code == 401 and not tried_refresh:
                tried_refresh = True
                if refresh_access_token():
                    continue  # retry with new token
                else:
                    raise RuntimeError("Session expired — please log in again")
            elif e.code == 402:
                raise RuntimeError("Monthly limit reached — upgrade your plan")
            else:
                raise RuntimeError(f"Server error {e.code}: {e.reason}")


# ── Floating Overlay — HUD tooltip style ──────────────────────────────────────

class FloatingOverlay(tk.Toplevel):
    AUTO_CLOSE_MS = 8000

    def __init__(self, master):
        super().__init__(master)
        self._dismissed  = False
        self._pinned     = False   # click to pin — pauses timer, hides countdown
        self._remaining  = self.AUTO_CLOSE_MS
        self._drag_x     = 0
        self._drag_y     = 0

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.97)
        self.configure(bg=BG_BASE)

        sw = self.winfo_screenwidth()
        self.geometry(f"400x52+{sw - 420}+20")

        # Cyan top border line
        tk.Frame(self, bg=CYAN, height=1).pack(fill=tk.X)

        self._body = tk.Frame(self, bg=BG_BASE)
        self._body.pack(fill=tk.BOTH, expand=True)

        # Topbar — drag handle + controls
        topbar = tk.Frame(self._body, bg=BG_BASE, padx=10, pady=6)
        topbar.pack(fill=tk.X)

        dot_c = tk.Canvas(topbar, width=6, height=6,
                          bg=BG_BASE, highlightthickness=0)
        dot_c.pack(side=tk.LEFT, padx=(0, 6))
        dot_c.create_oval(0, 0, 6, 6, fill=CYAN, outline="")

        tk.Label(topbar, text="APEX", font=(FONT_MONO, 8, "bold"),
                 fg=CYAN, bg=BG_BASE).pack(side=tk.LEFT)

        # Timer label — hidden while pinned
        self._timer_lbl = tk.Label(topbar, text="[8s]",
                                   font=(FONT_MONO, 7),
                                   fg=TEXT_MUTED, bg=BG_BASE)
        self._timer_lbl.pack(side=tk.LEFT, padx=8)

        close = tk.Label(topbar, text="×", font=(FONT_MONO, 10),
                         fg=TEXT_MUTED, bg=BG_BASE, cursor="hand2")
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda e: self._dismiss())
        close.bind("<Enter>",    lambda e: close.configure(fg=RED))
        close.bind("<Leave>",    lambda e: close.configure(fg=TEXT_MUTED))

        # Pin indicator label
        self._pin_lbl = tk.Label(topbar, text="",
                                 font=(FONT_MONO, 7),
                                 fg=CYAN, bg=BG_BASE)
        self._pin_lbl.pack(side=tk.RIGHT, padx=(0, 8))

        txt_frame = tk.Frame(self._body, bg=BG_BASE, padx=10, pady=4)
        txt_frame.pack(fill=tk.BOTH, expand=True)

        self._text = tk.Text(
            txt_frame, wrap=tk.WORD,
            bg=BG_BASE, fg=TEXT_PRIMARY,
            font=(FONT_MONO, 9),
            relief=tk.FLAT, state=tk.DISABLED,
            height=1, padx=0, pady=0,
            selectbackground=CYAN_DIM,
            cursor="arrow",
            spacing1=1, spacing3=1,
        )
        self._text.pack(fill=tk.BOTH, expand=True)

        # Click anywhere on overlay (except × button) to toggle pin
        drag_targets = [self, self._body, topbar, dot_c, txt_frame, self._text,
                        self._timer_lbl, self._pin_lbl]
        for w in drag_targets:
            w.bind("<Button-1>",        self._on_click,      add="+")
            w.bind("<ButtonPress-1>",   self._drag_start,    add="+")
            w.bind("<B1-Motion>",       self._drag_motion,   add="+")

        self._tick()

    # ── Pin / unpin on click ──────────────────────────────────────────────────

    def _on_click(self, event):
        # Only toggle pin on a clean click (not drag)
        if getattr(self, "_dragging", False):
            return
        self._pinned = not self._pinned
        if self._pinned:
            self._timer_lbl.configure(text="")
            self._pin_lbl.configure(text="[pinned]")
            self.attributes("-alpha", 1.0)
        else:
            self._pin_lbl.configure(text="")
            self.attributes("-alpha", 0.97)
            # Reset timer on unpin so it doesn't instantly vanish
            self._remaining = self.AUTO_CLOSE_MS

    # ── Drag to move ──────────────────────────────────────────────────────────

    def _drag_start(self, event):
        self._drag_x  = event.x_root - self.winfo_x()
        self._drag_y  = event.y_root - self.winfo_y()
        self._dragging = False

    def _drag_motion(self, event):
        self._dragging = True
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        # Keep within screen bounds
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, min(x, sw - self.winfo_width()))
        y = max(0, min(y, sh - self.winfo_height()))
        self.geometry(f"+{x}+{y}")

    # ── Content ───────────────────────────────────────────────────────────────

    def append(self, chunk: str):
        if self._dismissed: return
        self._text.configure(state=tk.NORMAL)
        self._text.insert(tk.END, chunk)
        self._text.configure(state=tk.DISABLED)
        self._text.see(tk.END)
        self._resize()

    def clear_text(self):
        if self._dismissed: return
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.configure(state=tk.DISABLED)

    def _resize(self):
        content = self._text.get("1.0", tk.END)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        # Current position
        try:
            cur_x = self.winfo_x()
            cur_y = self.winfo_y()
        except Exception:
            cur_x = sw - 420
            cur_y = 20
        cpl   = 52
        lines = sum(max(1, (len(ln) // cpl) + 1) for ln in content.split('\n'))
        lines = max(lines, content.count('\n') + 1)
        txt_h = min(max(lines, 1), 14)
        self._text.configure(height=txt_h)
        total_h = min(txt_h * 18 + 56, sh // 2)
        self.geometry(f"400x{total_h}+{cur_x}+{cur_y}")

    # ── Timer tick ────────────────────────────────────────────────────────────

    def _tick(self):
        if self._dismissed: return
        if not self._pinned:
            self._remaining -= 250
            secs = max(0, self._remaining // 1000)
            self._timer_lbl.configure(text=f"[{secs}s]")
            if self._remaining <= 0:
                self._dismiss(); return
        self.after(250, self._tick)

    def _dismiss(self):
        self._dismissed = True
        try: self.destroy()
        except: pass


# ── Mini Chat Popup ───────────────────────────────────────────────────────────

class MiniChat(tk.Toplevel):
    def __init__(self, master, on_send, note=None):
        super().__init__(master)
        self.on_send  = on_send
        self._drag_x  = 0
        self._drag_y  = 0
        self._dragging = False
        self.overrideredirect(True)
        self.configure(bg=BG_BASE)
        self.attributes("-topmost", True)

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = 460, 130
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{sh - h - 80}")

        # Cyan top border
        tk.Frame(self, bg=CYAN, height=1).pack(fill=tk.X)

        body = tk.Frame(self, bg=BG_BASE, padx=16, pady=12)
        body.pack(fill=tk.BOTH, expand=True)

        hdr = tk.Frame(body, bg=BG_BASE)
        hdr.pack(fill=tk.X, pady=(0, 8))

        dot_c = tk.Canvas(hdr, width=6, height=6,
                          bg=BG_BASE, highlightthickness=0)
        dot_c.pack(side=tk.LEFT, padx=(0, 6))
        dot_c.create_oval(0, 0, 6, 6, fill=CYAN, outline="")

        title_lbl = tk.Label(hdr, text="QUICK ASK", font=(FONT_MONO, 8, "bold"),
                 fg=TEXT_SECONDARY, bg=BG_BASE)
        title_lbl.pack(side=tk.LEFT)
        close = tk.Label(hdr, text="×", font=(FONT_MONO, 11),
                         fg=TEXT_MUTED, bg=BG_BASE, cursor="hand2")
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda e: self.destroy())
        close.bind("<Enter>",    lambda e: close.configure(fg=RED))
        close.bind("<Leave>",    lambda e: close.configure(fg=TEXT_MUTED))

        # Drag via header row
        for w in (hdr, dot_c, title_lbl):
            w.bind("<ButtonPress-1>",  self._drag_start)
            w.bind("<B1-Motion>",      self._drag_motion)

        if note:
            tk.Label(body, text=note, font=(FONT_MONO, 7),
                     fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 4))

        # Input row
        input_frame = tk.Frame(body, bg=BG_SURFACE2,
                               highlightthickness=1,
                               highlightbackground=BORDER,
                               highlightcolor=CYAN)
        input_frame.pack(fill=tk.X)

        self.entry = tk.Text(
            input_frame, height=2, wrap=tk.WORD,
            bg=BG_SURFACE2, fg=TEXT_PRIMARY,
            font=(FONT_MONO, 10), relief=tk.FLAT,
            insertbackground=CYAN,
            selectbackground=CYAN_DIM,
            padx=10, pady=7,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Divider
        tk.Frame(input_frame, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)

        send = tk.Label(input_frame, text="↑",
                        font=(FONT_MONO, 12, "bold"),
                        fg=CYAN, bg=BG_SURFACE2, width=3, cursor="hand2")
        send.pack(side=tk.RIGHT, fill=tk.Y)
        send.bind("<Button-1>", lambda e: self._submit())
        send.bind("<Enter>", lambda e: send.configure(fg=CYAN_HOVER))
        send.bind("<Leave>", lambda e: send.configure(fg=CYAN))

        self.entry.bind("<Return>",       lambda e: (self._submit(), "break")[1])
        self.entry.bind("<Shift-Return>", lambda e: None)
        self.entry.bind("<Escape>",       lambda e: self.destroy())
        self.entry.bind("<FocusIn>",
            lambda e: input_frame.configure(highlightbackground=CYAN))
        self.entry.bind("<FocusOut>",
            lambda e: input_frame.configure(highlightbackground=BORDER))

        self.after(80, lambda: (self.lift(), self.entry.focus_force()))

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_motion(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(f"+{x}+{y}")

    def _submit(self):
        text = self.entry.get("1.0", tk.END).strip()
        if not text: return
        self.destroy()
        self.on_send(text)


# ── Screenshot selector ───────────────────────────────────────────────────────

class ScreenshotSelector(tk.Toplevel):
    def __init__(self, master, callback):
        super().__init__(master)
        self.callback = callback
        self.start_x = self.start_y = 0
        self.rect = None

        with mss.mss() as sct:
            m = sct.monitors[1]
            self._phys_left = m["left"]
            self._phys_top  = m["top"]

        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
        else:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()

        with mss.mss() as sct:
            m = sct.monitors[1]
        self._scale_x = m["width"]  / sw
        self._scale_y = m["height"] / sh

        self.overrideredirect(True)
        self.geometry(f"{sw}x{sh}+0+0")
        self.attributes("-alpha", 0.25)
        self.attributes("-topmost", True)
        self.configure(bg="#05050a", cursor="crosshair")
        self.lift()
        self.focus_force()

        self.canvas = tk.Canvas(self, bg="#05050a", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>",   self.on_press)
        self.canvas.bind("<B1-Motion>",       self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Escape>",        lambda e: self.destroy())

        pill = tk.Label(
            self,
            text="  drag to select  ·  esc to cancel  ",
            bg=BG_SURFACE2, fg=TEXT_SECONDARY,
            font=(FONT_MONO, 10),
            padx=8, pady=5,
            relief=tk.FLAT
        )
        pill.place(relx=0.5, rely=0.04, anchor="n")

    def on_press(self, e):
        self.start_x, self.start_y = e.x, e.y
        self.rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y,
            outline=CYAN, width=1, dash=(4, 3))

    def on_drag(self, e):
        if self.rect:
            self.canvas.coords(self.rect, self.start_x, self.start_y, e.x, e.y)

    def on_release(self, e):
        x1 = int(min(self.start_x, e.x) * self._scale_x) + self._phys_left
        y1 = int(min(self.start_y, e.y) * self._scale_y) + self._phys_top
        x2 = int(max(self.start_x, e.x) * self._scale_x) + self._phys_left
        y2 = int(max(self.start_y, e.y) * self._scale_y) + self._phys_top
        self.destroy(); self.update()
        time.sleep(0.15)
        with mss.mss() as sct:
            mon = {"left": x1, "top": y1, "width": x2-x1, "height": y2-y1} \
                  if x2-x1 > 30 and y2-y1 > 30 else sct.monitors[0]
            shot = sct.grab(mon)
            img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        self.callback(img)


# ── Prompt dialog ─────────────────────────────────────────────────────────────

class PromptDialog(tk.Toplevel):
    def __init__(self, master, img, on_submit):
        super().__init__(master)
        self._drag_x = 0
        self._drag_y = 0
        self.overrideredirect(True)
        self.configure(bg=BG_BASE)
        self.attributes("-topmost", True)

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = 440, 170
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        tk.Frame(self, bg=CYAN, height=1).pack(fill=tk.X)

        body = tk.Frame(self, bg=BG_BASE, padx=20, pady=16)
        body.pack(fill=tk.BOTH, expand=True)

        # Header row — drag handle + close
        hdr = tk.Frame(body, bg=BG_BASE)
        hdr.pack(fill=tk.X, pady=(0, 10))

        title_lbl = tk.Label(hdr, text="SCREENSHOT CAPTURED",
                 font=(FONT_MONO, 9, "bold"),
                 fg=TEXT_SECONDARY, bg=BG_BASE)
        title_lbl.pack(side=tk.LEFT)

        close = tk.Label(hdr, text="×", font=(FONT_MONO, 11),
                         fg=TEXT_MUTED, bg=BG_BASE, cursor="hand2")
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda e: self.destroy())
        close.bind("<Enter>",    lambda e: close.configure(fg=RED))
        close.bind("<Leave>",    lambda e: close.configure(fg=TEXT_MUTED))

        for w in (hdr, title_lbl):
            w.bind("<ButtonPress-1>",  self._drag_start)
            w.bind("<B1-Motion>",      self._drag_motion)

        tk.Label(body, text="ask a question or press enter to describe",
                 font=(FONT_MONO, 8),
                 fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 12))

        input_frame = tk.Frame(body, bg=BG_SURFACE2,
                               highlightthickness=1,
                               highlightbackground=BORDER,
                               highlightcolor=CYAN)
        input_frame.pack(fill=tk.X)

        self.entry = tk.Entry(
            input_frame, bg=BG_SURFACE2, fg=TEXT_PRIMARY,
            font=(FONT_MONO, 10), relief=tk.FLAT,
            insertbackground=CYAN, selectbackground=CYAN_DIM,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=8)

        tk.Frame(input_frame, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)

        send_btn = tk.Label(
            input_frame, text="↵",
            font=(FONT_MONO, 11, "bold"),
            fg=CYAN, bg=BG_SURFACE2, cursor="hand2",
            padx=10, pady=4
        )
        send_btn.pack(side=tk.RIGHT, fill=tk.Y)
        send_btn.bind("<Button-1>", lambda e: self._submit())
        send_btn.bind("<Enter>", lambda e: send_btn.configure(fg=CYAN_HOVER))
        send_btn.bind("<Leave>", lambda e: send_btn.configure(fg=CYAN))

        self.entry.focus_set()
        self.entry.bind("<Return>", lambda e: self._submit())
        self.bind("<Escape>", lambda e: self.destroy())
        self._on_submit = on_submit
        self._img = img

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_motion(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(f"+{x}+{y}")

    def _submit(self):
        prompt = self.entry.get().strip() or "answer any question/provide information."
        self.destroy()
        self._on_submit(self._img, prompt)


# ── Message Bubble — terminal log strip ───────────────────────────────────────

class MessageBubble(tk.Frame):
    def __init__(self, parent, role: str, label: str, ts: str = ""):
        bg = USER_BG if role == "user" else ASST_BG
        accent = CYAN if role == "user" else BORDER
        super().__init__(parent, bg=bg)
        self.pack(fill=tk.X, padx=0, pady=0)

        # Left accent line
        tk.Frame(self, bg=accent, width=2).pack(side=tk.LEFT, fill=tk.Y)

        inner = tk.Frame(self, bg=bg, padx=16, pady=10)
        inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Role row with right-aligned timestamp
        role_row = tk.Frame(inner, bg=bg)
        role_row.pack(fill=tk.X, pady=(0, 4))

        role_color = CYAN if role == "user" else TEXT_SECONDARY
        role_text  = "YOU" if role == "user" else "APEX"
        tk.Label(role_row, text=role_text,
                 font=(FONT_MONO, 8, "bold"),
                 fg=role_color, bg=bg).pack(side=tk.LEFT)

        if ts:
            tk.Label(role_row, text=ts,
                     font=(FONT_MONO, 7),
                     fg=TEXT_MUTED, bg=bg).pack(side=tk.RIGHT)

        self._text = tk.Text(
            inner, wrap=tk.WORD,
            bg=bg, fg=TEXT_PRIMARY,
            font=(FONT_MONO, 9),
            relief=tk.FLAT, state=tk.DISABLED,
            padx=0, pady=0, height=1,
            selectbackground=CYAN_DIM,
            spacing1=2, spacing3=2,
            cursor="arrow",
        )
        self._text.pack(fill=tk.X)

    def append(self, text: str, tag="body"):
        self._text.configure(state=tk.NORMAL)
        self._text.insert(tk.END, text)
        lines = int(self._text.index(tk.END).split(".")[0])
        self._text.configure(height=max(lines, 1), state=tk.DISABLED)

    def set_image(self, photo):
        self._text.configure(state=tk.NORMAL)
        self._text.image_create(tk.END, image=photo)
        self._text.insert(tk.END, "\n")
        lines = int(self._text.index(tk.END).split(".")[0]) + 6
        self._text.configure(height=max(lines, 8), state=tk.DISABLED)


# ── Main Chat Window ──────────────────────────────────────────────────────────

class ChatWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.geometry("860x680")
        self.minsize(620, 460)
        self.configure(bg=BG_BASE)
        self.protocol("WM_DELETE_WINDOW", self.iconify)

        # Remove OS title bar while keeping taskbar presence + iconify support.
        # On Windows: use the GWL_STYLE trick to strip the caption/border.
        # On other platforms: fall back to overrideredirect (no taskbar needed there).
        self._using_overrideredirect = False
        if sys.platform == "win32":
            self._remove_titlebar_win32()
        else:
            self.overrideredirect(True)
            self._using_overrideredirect = True

        # Drag state
        self._drag_x = 0
        self._drag_y = 0
        self._resizing = False

        self.conversation: list = []
        self._overlay: FloatingOverlay | None = None
        self._current_bubble: MessageBubble | None = None
        self._images = []

        self._screenshots: list = []
        self._active_page: str = ""
        self._pages: dict = {}
        self._sidebar_buttons: dict = {}
        self._ss_empty = None
        self._ss_hotkey_label = None   # updated when model changes

        self._build_ui()
        self._set_taskbar_title_and_icon()
        self._start_hotkeys()
        self._process_queue()
        self.after(3500, lambda: _show_update_dialog_if_pending(self))
        threading.Thread(target=self._fetch_profile, daemon=True).start()

    def _fetch_profile(self):
        """Background thread: fetch full profile and update _user_info with available_models."""
        global _user_info
        try:
            profile = _api_request("GET", "/user/profile", token=_access_token)
            _user_info.update({
                "tier":             profile.get("tier", _user_info.get("tier", "free")),
                "model":            profile.get("model", ""),
                "available_models": profile.get("available_models", []),
            })
            save_auth(_access_token, _refresh_token, _user_info)
        except Exception:
            pass

    def _set_taskbar_title_and_icon(self):
        """Set taskbar title and icon. Uses embedded PNG so no external file needed."""
        self.title("Apex Assistant")

        # Embedded icon as base64 PNG — always available, no file dependency
        ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAABv0lEQVR4nO1bu5LCMAx0GCrDp1zN/9fU9ymH29CQm9hxHsTSbhDaDshY0u5afkwIweH4anTVb3/7HpwHBj/dpN7T5CGrxYdQre209oA5FDVOHfBlcALYCbCRE1DpkuZQ1JgREOOlj7crNiEg4u0aYrxkTTBjo/zRKlJ6/NftPYCdABtOADsBNpwAdgJs0AhI97/FzyicKVFfYBU9BsUBc4UzCPEegA64pjLaBXQHsA9fUAK2qot0AdUBg/pMF8AIeFdVlAtoDihVZ7kAQsBeNREuoDhgTm2GC+jLIBvqBLTaWHsawB2wZnP0NFAlQEo9TRdAHbBVXaQL1AiQVk3LBdALkSNcgJRQcYBWoRrj+j5AekBtm0uPr94DJDr6xyyDqCYnGUe1B0it55r7Am+CUgOh13ipeGoOkLat1jQQIYC1w5OIq+IALbU0xm0mgL2/b40v7gDto6z0+E0EsNUf0JKHqANQFxmScXYTcBT1B+zNR8wB6MtMqXjZaTClR7f1ddlxAkw3vEvE+DXZECr/GbL+vnBJwGQKlA9YQq22ag+wSMJcTbNN0BIJS7UsrgIWSFirYXOBn9YcLYjncADwBH3pqlOxMKQpAAAAAElFTkSuQmCC"

        try:
            import base64 as _b64
            from PIL import Image, ImageTk
            import io as _io
            raw = _b64.b64decode(ICON_B64)
            pil_img = Image.open(_io.BytesIO(raw)).resize((32, 32), Image.LANCZOS)
            self._icon_photo = ImageTk.PhotoImage(pil_img)
            self.wm_iconphoto(True, self._icon_photo)
        except Exception:
            pass

        # Also try .ico file if sitting next to the script (better quality on Win)
        icon_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "apex_icon.ico"),
            os.path.join(os.getcwd(), "apex_icon.ico"),
        ]
        for p in icon_paths:
            if os.path.exists(p):
                try:
                    self.iconbitmap(default=p)
                except Exception:
                    pass
                break

    def _remove_titlebar_win32(self):
        """Strip the title bar on Windows using SetWindowLong while keeping
        the window in the taskbar and allowing iconify/deiconify."""
        import ctypes
        import ctypes.wintypes as wt

        GWL_STYLE      = -16
        WS_CAPTION     = 0x00C00000
        WS_THICKFRAME  = 0x00040000
        WS_SYSMENU     = 0x00080000
        WS_MAXIMIZEBOX = 0x00010000

        self.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        style &= ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MAXIMIZEBOX)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)

        SWP_NOMOVE      = 0x0002
        SWP_NOSIZE      = 0x0001
        SWP_NOZORDER    = 0x0004
        SWP_FRAMECHANGED= 0x0020
        ctypes.windll.user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
        )

        # Hook WM_NCHITTEST so Windows thinks the topbar is the native title bar.
        # This gives us OS-native drag with ZERO lag — no Python event loop involved.
        self._setup_nchittest_hook(hwnd)

    def _setup_nchittest_hook(self, hwnd):
        """Subclass the window proc to intercept WM_NCHITTEST.
        When the cursor is in the topbar region, return HTCAPTION so Windows
        handles dragging natively — buttery smooth, no Tkinter involvement."""
        import ctypes
        import ctypes.wintypes as wt

        WM_NCHITTEST = 0x0084
        HTCAPTION    = 2
        GWLP_WNDPROC = -4

        # Must use c_int64 / UINT_PTR for 64-bit Windows — plain c_long truncates
        prototype = ctypes.WINFUNCTYPE(
            ctypes.c_int64,
            ctypes.c_int64,  # HWND
            ctypes.c_uint,   # UINT msg
            ctypes.c_int64,  # WPARAM
            ctypes.c_int64,  # LPARAM
        )

        # Retrieve original proc as a pointer-sized int
        ctypes.windll.user32.GetWindowLongPtrW.restype  = ctypes.c_int64
        ctypes.windll.user32.GetWindowLongPtrW.argtypes = [ctypes.c_int64, ctypes.c_int]
        original_proc = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)

        # Set up CallWindowProcW with correct argtypes so no overflow
        cwp = ctypes.windll.user32.CallWindowProcW
        cwp.restype  = ctypes.c_int64
        cwp.argtypes = [
            ctypes.c_int64,  # lpPrevWndFunc
            ctypes.c_int64,  # hWnd
            ctypes.c_uint,   # Msg
            ctypes.c_int64,  # wParam
            ctypes.c_int64,  # lParam
        ]

        def wnd_proc(h, msg, wparam, lparam):
            if msg == WM_NCHITTEST:
                cx = ctypes.c_int16(lparam & 0xFFFF).value
                cy = ctypes.c_int16((lparam >> 16) & 0xFFFF).value
                rect = wt.RECT()
                ctypes.windll.user32.GetWindowRect(h, ctypes.byref(rect))
                rel_y = cy - rect.top
                rel_x = cx - rect.left
                win_w = rect.right - rect.left
                if 0 <= rel_y <= TOPBAR_H + 2 and rel_x < win_w - 90:
                    return HTCAPTION
            return cwp(original_proc, h, msg, wparam, lparam)

        self._wnd_proc_ref = prototype(wnd_proc)
        ctypes.windll.user32.SetWindowLongPtrW.restype  = ctypes.c_int64
        ctypes.windll.user32.SetWindowLongPtrW.argtypes = [ctypes.c_int64, ctypes.c_int, ctypes.c_int64]
        ctypes.windll.user32.SetWindowLongPtrW(
            hwnd, GWLP_WNDPROC,
            ctypes.cast(self._wnd_proc_ref, ctypes.c_void_p).value
        )

    def _build_ui(self):
        self._build_topbar()
        self._main_frame = tk.Frame(self, bg=BG_BASE)
        self._main_frame.pack(fill=tk.BOTH, expand=True)
        self._build_sidebar(self._main_frame)
        tk.Frame(self._main_frame, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)
        self._content_frame = tk.Frame(self._main_frame, bg=BG_BASE)
        self._content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_pages(self._content_frame)
        self._switch_page("chat")

    def _build_topbar(self):
        bar = tk.Frame(self, bg=BG_SIDEBAR, height=TOPBAR_H)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        logo = tk.Frame(bar, bg=BG_SIDEBAR, padx=14)
        logo.pack(side=tk.LEFT, fill=tk.Y)

        dot = tk.Canvas(logo, width=8, height=8,
                        bg=BG_SIDEBAR, highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 8))
        dot.create_oval(0, 0, 8, 8, fill=CYAN, outline="")

        apex_lbl = tk.Label(logo, text="APEX",
                 font=(FONT_MONO, 11, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_SIDEBAR)
        apex_lbl.pack(side=tk.LEFT)

        # v2 chip
        v_chip = tk.Frame(logo, bg=BG_SURFACE2, padx=5, pady=1)
        v_chip.pack(side=tk.LEFT, padx=8)
        tk.Label(v_chip, text="v2",
                 font=(FONT_MONO, 7),
                 fg=TEXT_MUTED, bg=BG_SURFACE2).pack()

        # Window controls — right side
        ctrl = tk.Frame(bar, bg=BG_SIDEBAR, padx=10)
        ctrl.pack(side=tk.RIGHT, fill=tk.Y)

        close_btn = tk.Label(ctrl, text="×", font=(FONT_MONO, 14),
                             fg=TEXT_MUTED, bg=BG_SIDEBAR, cursor="hand2", padx=6)
        close_btn.pack(side=tk.RIGHT, fill=tk.Y)
        close_btn.bind("<Button-1>", lambda e: self.destroy())
        close_btn.bind("<Enter>",    lambda e: close_btn.configure(fg=RED))
        close_btn.bind("<Leave>",    lambda e: close_btn.configure(fg=TEXT_MUTED))

        min_btn = tk.Label(ctrl, text="–", font=(FONT_MONO, 14),
                           fg=TEXT_MUTED, bg=BG_SIDEBAR, cursor="hand2", padx=6)
        min_btn.pack(side=tk.RIGHT, fill=tk.Y)
        min_btn.bind("<Button-1>", lambda e: self._minimize())
        min_btn.bind("<Enter>",    lambda e: min_btn.configure(fg=TEXT_PRIMARY))
        min_btn.bind("<Leave>",    lambda e: min_btn.configure(fg=TEXT_MUTED))

        max_btn = tk.Label(ctrl, text="□", font=(FONT_MONO, 11),
                           fg=TEXT_MUTED, bg=BG_SIDEBAR, cursor="hand2", padx=6)
        max_btn.pack(side=tk.RIGHT, fill=tk.Y)
        max_btn.bind("<Button-1>", lambda e: self._toggle_maximize())
        max_btn.bind("<Enter>",    lambda e: max_btn.configure(fg=TEXT_PRIMARY))
        max_btn.bind("<Leave>",    lambda e: max_btn.configure(fg=TEXT_MUTED))

        # Drag via topbar
        for w in (bar, logo, dot, apex_lbl, v_chip):
            w.bind("<ButtonPress-1>",  self._drag_start)
            w.bind("<B1-Motion>",      self._drag_motion)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_motion(self, event):
        nx = event.x_root - self._drag_x
        ny = event.y_root - self._drag_y
        if sys.platform == "win32":
            # Pure Win32 move — never call geometry() during drag,
            # that triggers full Tkinter layout recalc and causes stutter
            self._hwnd_move(nx, ny)
        else:
            self.geometry(f"+{nx}+{ny}")

    def _hwnd_move(self, x, y):
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
        ctypes.windll.user32.SetWindowPos(
            hwnd, None, x, y, 0, 0,
            0x0001 | 0x0004 | 0x0010  # NOSIZE | NOZORDER | NOACTIVATE
        )

    def _toggle_maximize(self):
        if self.winfo_width() >= self.winfo_screenwidth() - 10:
            self.geometry("860x680")
        else:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            self.geometry(f"{sw}x{sh}+0+0")

    def _build_sidebar(self, parent):
        self._sidebar_frame = tk.Frame(parent, bg=BG_SIDEBAR, width=SIDEBAR_W)
        self._sidebar_frame.pack(side=tk.LEFT, fill=tk.Y)
        self._sidebar_frame.pack_propagate(False)

        for name, icon, label in [
            ("chat",        "✦",  "Chat"),
            ("history",     "☰",  "History"),
            ("screenshots", "⬚",  "Shots"),
            ("settings",    "⚙",  "Settings"),
            ("about",       "◈",  "About"),
        ]:
            self._make_sidebar_btn(name, icon, label)

    def _make_sidebar_btn(self, name: str, icon: str, label: str):
        row = tk.Frame(self._sidebar_frame, bg=BG_SIDEBAR, height=52)
        row.pack(fill=tk.X)
        row.pack_propagate(False)

        indicator = tk.Frame(row, bg=BG_SIDEBAR, width=2)
        indicator.pack(side=tk.LEFT, fill=tk.Y)
        indicator.pack_propagate(False)

        btn_area = tk.Frame(row, bg=BG_SIDEBAR)
        btn_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        icon_lbl = tk.Label(btn_area, text=icon,
                            font=(FONT_MONO, 15),
                            fg=TEXT_MUTED, bg=BG_SIDEBAR,
                            cursor="hand2")
        icon_lbl.pack(expand=True)

        widgets = (row, btn_area, icon_lbl)

        def on_enter(_=None):
            if self._active_page != name:
                for w in widgets: w.configure(bg=BG_HOVER)

        def on_leave(_=None):
            if self._active_page != name:
                for w in widgets: w.configure(bg=BG_SIDEBAR)

        def on_click(_=None):
            self._switch_page(name)

        for w in widgets:
            w.bind("<Enter>",    on_enter)
            w.bind("<Leave>",    on_leave)
            w.bind("<Button-1>", on_click)

        self._sidebar_buttons[name] = (row, indicator, btn_area, icon_lbl)

    def _switch_page(self, name: str):
        if self._active_page and self._active_page in self._sidebar_buttons:
            row, ind, area, icon = self._sidebar_buttons[self._active_page]
            for w in (row, area, icon): w.configure(bg=BG_SIDEBAR)
            icon.configure(fg=TEXT_MUTED)
            ind.configure(bg=BG_SIDEBAR)

        if name in self._sidebar_buttons:
            row, ind, area, icon = self._sidebar_buttons[name]
            for w in (row, area, icon): w.configure(bg=CYAN_ACTIVE)
            icon.configure(fg=TEXT_PRIMARY)
            ind.configure(bg=CYAN)

        if name == "history":
            self._refresh_history()

        if name in self._pages:
            self._pages[name].lift()
        self._active_page = name

    def _build_pages(self, parent):
        for name, builder in [
            ("chat",         self._build_chat_page),
            ("history",      self._build_history_page),
            ("screenshots",  self._build_screenshots_page),
            ("settings",     self._build_settings_page),
            ("about",        self._build_about_page),
        ]:
            frame = tk.Frame(parent, bg=BG_BASE)
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            builder(frame)
            self._pages[name] = frame

    # ── Chat page ─────────────────────────────────────────────────────────────

    def _build_chat_page(self, container):
        # Header — matches History/Screenshots style
        hdr = tk.Frame(container, bg=BG_BASE, padx=20, pady=14)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="CHAT",
                 font=(FONT_MONO, 10, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_BASE).pack(side=tk.LEFT)
        tk.Label(hdr, text="ask anything",
                 font=(FONT_MONO, 8), fg=TEXT_MUTED,
                 bg=BG_BASE).pack(side=tk.LEFT, padx=10)

        tk.Frame(container, bg=BORDER, height=1).pack(fill=tk.X)

        chat_area = tk.Frame(container, bg=BG_BASE)
        chat_area.pack(fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(chat_area, bg=BG_BASE,
                                 highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(chat_area, orient=tk.VERTICAL,
                                 command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._msg_frame = tk.Frame(self._canvas, bg=BG_BASE)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._msg_frame, anchor="nw")

        self._msg_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>",    self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>",   self._on_mousewheel)
        self._canvas.bind("<Button-4>", lambda e: self._canvas.yview_scroll(-1, "units"))
        self._canvas.bind("<Button-5>", lambda e: self._canvas.yview_scroll(1, "units"))

        # Empty state — invisible placeholder, no text
        self._empty_label = tk.Label(self._msg_frame, text="", bg=BG_BASE)
        self._empty_label.pack(expand=True, pady=4)

        self._build_chat_bottom(container)

    def _build_history_page(self, container):
        hdr = tk.Frame(container, bg=BG_BASE, padx=20, pady=14)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="HISTORY",
                 font=(FONT_MONO, 10, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_BASE).pack(side=tk.LEFT)
        tk.Label(hdr, text="saved chats",
                 font=(FONT_MONO, 8), fg=TEXT_MUTED,
                 bg=BG_BASE).pack(side=tk.LEFT, padx=10)

        tk.Frame(container, bg=BORDER, height=1).pack(fill=tk.X)

        hist_canvas = tk.Canvas(container, bg=BG_BASE, highlightthickness=0)
        hist_scroll = tk.Scrollbar(container, orient=tk.VERTICAL,
                                   command=hist_canvas.yview)
        hist_canvas.configure(yscrollcommand=hist_scroll.set)
        hist_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        hist_canvas.pack(fill=tk.BOTH, expand=True)

        self._hist_list_frame = tk.Frame(hist_canvas, bg=BG_BASE)
        hist_win = hist_canvas.create_window(
            (0, 0), window=self._hist_list_frame, anchor="nw")

        self._hist_list_frame.bind("<Configure>",
            lambda e: hist_canvas.configure(scrollregion=hist_canvas.bbox("all")))
        hist_canvas.bind("<Configure>",
            lambda e: hist_canvas.itemconfig(hist_win, width=e.width))
        hist_canvas.bind("<MouseWheel>",
            lambda e: hist_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        self._hist_empty = tk.Label(
            self._hist_list_frame,
            text="no chats saved yet",
            font=(FONT_MONO, 9), fg=TEXT_MUTED, bg=BG_BASE,
        )
        self._hist_empty.pack(expand=True, pady=60)
        self._refresh_history()

    def _refresh_history(self):
        for w in self._hist_list_frame.winfo_children():
            w.destroy()
        files = get_chat_history_files()
        if not files:
            tk.Label(self._hist_list_frame,
                     text="no chats saved yet",
                     font=(FONT_MONO, 9), fg=TEXT_MUTED, bg=BG_BASE
                     ).pack(expand=True, pady=60)
            return
        for filename in files:
            self._add_history_card(filename)

    def _add_history_card(self, filename: str):
        filepath = os.path.join(CHAT_HISTORY_DIR, filename)
        title = filename.replace(".json", "")

        card = tk.Frame(self._hist_list_frame, bg=BG_SURFACE)
        card.pack(fill=tk.X, padx=10, pady=(4, 0))

        # Cyan left accent
        tk.Frame(card, bg=BORDER, width=2).pack(side=tk.LEFT, fill=tk.Y)

        inner = tk.Frame(card, bg=BG_SURFACE, padx=14, pady=10)
        inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def load_this(_=None):
            messages = load_chat_from_file(filepath)
            if messages:
                # Clear current chat first (save it if needed)
                if self.conversation:
                    self._save_current_chat()
                self.conversation.clear()
                for w in self._msg_frame.winfo_children():
                    w.destroy()
                self._empty_label = None
                self._current_bubble = None
                # Load the selected conversation
                self.conversation = messages
                self._display_loaded_chat()
                self._switch_page("chat")

        def delete_this(_=None):
            try:
                os.remove(filepath)
                self._refresh_history()
            except Exception: pass

        title_lbl = tk.Label(inner, text=title,
                             font=(FONT_MONO, 9),
                             fg=TEXT_PRIMARY, bg=BG_SURFACE,
                             cursor="hand2")
        title_lbl.pack(anchor=tk.W)
        title_lbl.bind("<Button-1>", load_this)

        messages = load_chat_from_file(filepath)
        msg_count = len([m for m in messages if m["role"] == "user"])
        tk.Label(inner, text=f"{msg_count} exchanges",
                 font=(FONT_MONO, 7), fg=TEXT_MUTED, bg=BG_SURFACE).pack(anchor=tk.W)

        del_btn = tk.Label(card, text="×", font=(FONT_MONO, 11),
                           fg=TEXT_MUTED, bg=BG_SURFACE,
                           cursor="hand2", padx=10)
        del_btn.pack(side=tk.RIGHT)
        del_btn.bind("<Button-1>", delete_this)
        del_btn.bind("<Enter>", lambda e: del_btn.configure(fg=RED))
        del_btn.bind("<Leave>", lambda e: del_btn.configure(fg=TEXT_MUTED))

        for w in [card, inner, title_lbl]:
            w.bind("<Enter>", lambda e, c=card, i=inner: (
                c.configure(bg=BG_HOVER), i.configure(bg=BG_HOVER)))
            w.bind("<Leave>", lambda e, c=card, i=inner: (
                c.configure(bg=BG_SURFACE), i.configure(bg=BG_SURFACE)))

    def _display_loaded_chat(self):
        self._remove_empty()
        for msg in self.conversation:
            if msg["role"] == "user":
                b = self._add_user_bubble()
            else:
                b = self._add_assistant_bubble()
            text = msg.get("content", "")
            b.append(text if isinstance(text, str) else str(text))
        self._scroll_bottom()
        # Re-bind scroll on everything after full render
        self.after(100, lambda: self._bind_mousewheel(self._msg_frame))

    def _build_chat_bottom(self, container):
        tk.Frame(container, bg=BORDER, height=1).pack(fill=tk.X)

        bottom = tk.Frame(container, bg=BG_SIDEBAR, padx=14, pady=10)
        bottom.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="")
        self._status_lbl = tk.Label(
            bottom, textvariable=self.status_var,
            font=(FONT_MONO, 8), fg=CYAN, bg=BG_SIDEBAR
        )
        self._status_lbl.pack(anchor=tk.W, pady=(0, 6))

        input_frame = tk.Frame(bottom, bg=BG_SURFACE2,
                               highlightthickness=1,
                               highlightbackground=BORDER,
                               highlightcolor=CYAN)
        input_frame.pack(fill=tk.X)

        self.input_box = tk.Text(
            input_frame, height=3, wrap=tk.WORD,
            bg=BG_SURFACE2, fg=TEXT_PRIMARY,
            font=(FONT_MONO, 10),
            insertbackground=CYAN,
            relief=tk.FLAT, padx=12, pady=8,
            selectbackground=CYAN_DIM,
        )
        self.input_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_box.bind("<Return>",       self._on_enter)
        self.input_box.bind("<Shift-Return>", lambda e: None)
        self.input_box.bind("<FocusIn>",
            lambda e: input_frame.configure(highlightbackground=CYAN))
        self.input_box.bind("<FocusOut>",
            lambda e: input_frame.configure(highlightbackground=BORDER))

        tk.Frame(input_frame, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)

        self._send_btn = tk.Label(
            input_frame, text="↑", font=(FONT_MONO, 14, "bold"),
            fg=CYAN, bg=BG_SURFACE2, width=3, cursor="hand2"
        )
        self._send_btn.pack(side=tk.RIGHT, fill=tk.Y)
        self._send_btn.bind("<Button-1>", lambda e: self._send_text())
        self._send_btn.bind("<Enter>", lambda e: self._send_btn.configure(fg=CYAN_HOVER))
        self._send_btn.bind("<Leave>", lambda e: self._send_btn.configure(fg=CYAN))

        btn_row = tk.Frame(bottom, bg=BG_SIDEBAR)
        btn_row.pack(fill=tk.X, pady=(6, 0))

        clear_btn = tk.Label(btn_row, text="clear",
                             font=(FONT_MONO, 7), fg=TEXT_MUTED,
                             bg=BG_SIDEBAR, cursor="hand2")
        clear_btn.pack(side=tk.LEFT)
        clear_btn.bind("<Button-1>", lambda e: self._clear_chat())
        clear_btn.bind("<Enter>", lambda e: clear_btn.configure(fg=TEXT_SECONDARY))
        clear_btn.bind("<Leave>", lambda e: clear_btn.configure(fg=TEXT_MUTED))

        save_btn = tk.Label(btn_row, text="save",
                            font=(FONT_MONO, 7), fg=TEXT_MUTED,
                            bg=BG_SIDEBAR, cursor="hand2")
        save_btn.pack(side=tk.RIGHT)
        save_btn.bind("<Button-1>", lambda e: self._save_current_chat())
        save_btn.bind("<Enter>", lambda e: save_btn.configure(fg=TEXT_SECONDARY))
        save_btn.bind("<Leave>", lambda e: save_btn.configure(fg=TEXT_MUTED))

    def _build_screenshots_page(self, container):
        hdr = tk.Frame(container, bg=BG_BASE, padx=20, pady=14)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="SCREENSHOTS",
                 font=(FONT_MONO, 10, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_BASE).pack(side=tk.LEFT)
        tk.Label(hdr, text="this session",
                 font=(FONT_MONO, 8), fg=TEXT_MUTED,
                 bg=BG_BASE).pack(side=tk.LEFT, padx=10)

        tk.Frame(container, bg=BORDER, height=1).pack(fill=tk.X)

        ss_canvas = tk.Canvas(container, bg=BG_BASE, highlightthickness=0)
        ss_scroll = tk.Scrollbar(container, orient=tk.VERTICAL,
                                 command=ss_canvas.yview)
        ss_canvas.configure(yscrollcommand=ss_scroll.set)
        ss_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        ss_canvas.pack(fill=tk.BOTH, expand=True)

        self._ss_list_frame = tk.Frame(ss_canvas, bg=BG_BASE)
        ss_win = ss_canvas.create_window(
            (0, 0), window=self._ss_list_frame, anchor="nw")

        self._ss_list_frame.bind("<Configure>",
            lambda e: ss_canvas.configure(scrollregion=ss_canvas.bbox("all")))
        ss_canvas.bind("<Configure>",
            lambda e: ss_canvas.itemconfig(ss_win, width=e.width))
        ss_canvas.bind("<MouseWheel>",
            lambda e: ss_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        self._ss_empty = tk.Label(
            self._ss_list_frame,
            text="no screenshots yet\nCtrl+Shift+S to capture",
            font=(FONT_MONO, 9), fg=TEXT_MUTED, bg=BG_BASE,
        )
        self._ss_empty.pack(expand=True, pady=60)

    def _add_screenshot_card(self, photo, prompt: str, ts: str):
        if self._ss_empty:
            self._ss_empty.destroy()
            self._ss_empty = None

        card = tk.Frame(self._ss_list_frame, bg=BG_SURFACE)
        card.pack(fill=tk.X, padx=10, pady=(4, 0))

        tk.Frame(card, bg=CYAN_DIM, width=2).pack(side=tk.LEFT, fill=tk.Y)

        img_lbl = tk.Label(card, image=photo, bg=BG_SURFACE)
        img_lbl.pack(side=tk.LEFT, padx=8, pady=8)

        info = tk.Frame(card, bg=BG_SURFACE, padx=8)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(info, text=ts,
                 font=(FONT_MONO, 7), fg=TEXT_MUTED,
                 bg=BG_SURFACE).pack(anchor=tk.W)
        tk.Label(info, text=prompt,
                 font=(FONT_MONO, 8), fg=TEXT_SECONDARY, bg=BG_SURFACE,
                 wraplength=300, justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

    def _build_settings_page(self, container):
        canvas = tk.Canvas(container, bg=BG_BASE, highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient=tk.VERTICAL,
                                 command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(fill=tk.BOTH, expand=True)

        body = tk.Frame(canvas, bg=BG_BASE, padx=28, pady=20)
        canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        tk.Label(body, text="SETTINGS",
                 font=(FONT_MONO, 10, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_BASE).pack(anchor=tk.W)
        tk.Frame(body, bg=BORDER, height=1).pack(fill=tk.X, pady=(8, 16))

        def section_label(text):
            f = tk.Frame(body, bg=BG_BASE)
            f.pack(fill=tk.X, pady=(0, 8))
            tk.Label(f, text=text,
                     font=(FONT_MONO, 7, "bold"),
                     fg=TEXT_MUTED, bg=BG_BASE).pack(side=tk.LEFT)
            tk.Frame(f, bg=BORDER, height=1).pack(side=tk.LEFT, fill=tk.X,
                                                   expand=True, padx=(8, 0), pady=6)

        # ACCOUNT section
        section_label("ACCOUNT")
        email = _user_info.get("email", "—")
        tier  = _user_info.get("tier",  "free").upper()
        tk.Label(body, text=email, font=(FONT_MONO, 9),
                 fg=TEXT_PRIMARY, bg=BG_BASE).pack(anchor=tk.W)
        tk.Label(body, text=f"plan  ·  {tier}",
                 font=(FONT_MONO, 8), fg=CYAN, bg=BG_BASE).pack(anchor=tk.W, pady=(2, 12))

        # Sign out button
        so_outer = tk.Frame(body, bg=BORDER, padx=1, pady=1)
        so_outer.pack(anchor=tk.W, pady=(0, 20))
        so_btn = tk.Label(so_outer, text="  sign out  ",
                          font=(FONT_MONO, 8), fg=TEXT_SECONDARY,
                          bg=BG_BASE, cursor="hand2", padx=8, pady=5)
        so_btn.pack()

        def do_signout(_=None):
            try:
                _api_request("POST", "/auth/logout",
                             {"refresh_token": _refresh_token},
                             token=_access_token)
            except Exception:
                pass
            clear_auth()
            self.destroy()
            login = LoginScreen(on_complete=lambda: ChatWindow().mainloop())
            login.mainloop()

        so_btn.bind("<Button-1>", do_signout)
        so_btn.bind("<Enter>",    lambda e: (so_btn.configure(bg=RED, fg=BG_BASE),
                                             so_outer.configure(bg=RED)))
        so_btn.bind("<Leave>",    lambda e: (so_btn.configure(bg=BG_BASE, fg=TEXT_SECONDARY),
                                             so_outer.configure(bg=BORDER)))

        # MODEL section
        tk.Frame(body, bg=BORDER, height=1).pack(fill=tk.X, pady=(0, 0))
        section_label("MODEL")

        available = _user_info.get("available_models", [])
        tier_name = _user_info.get("tier", "free")

        if len(available) <= 1:
            # Free / basic — fixed model, just show it
            fixed_id   = available[0]["id"] if available else "gpt-4o-mini"
            fixed_name = MODEL_DISPLAY.get(fixed_id, fixed_id)
            tk.Label(body, text=fixed_name,
                     font=(FONT_MONO, 9), fg=CYAN, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 4))
            tk.Label(body,
                     text="upgrade to pro or power to choose your model",
                     font=(FONT_MONO, 7), fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 12))
        else:
            # Pro / power — selectable dropdown
            model_ids   = [m["id"]   for m in available]
            model_names = [MODEL_DISPLAY.get(m["id"], m["id"]) +
                           ("  ·  no vision" if not m["vision"] else "")
                           for m in available]

            current = _preferred_model if _preferred_model in model_ids else model_ids[0]
            sel_var = tk.StringVar(value=MODEL_DISPLAY.get(current, current))

            menu_frame = tk.Frame(body, bg=BG_SURFACE2,
                                  highlightthickness=1, highlightbackground=BORDER)
            menu_frame.pack(fill=tk.X, pady=(0, 6))

            opt = tk.OptionMenu(menu_frame, sel_var, *model_names)
            opt.configure(bg=BG_SURFACE2, fg=TEXT_PRIMARY, font=(FONT_MONO, 9),
                          activebackground=CYAN_DIM, activeforeground=TEXT_PRIMARY,
                          highlightthickness=0, relief=tk.FLAT, bd=0,
                          indicatoron=True)
            opt["menu"].configure(bg=BG_SURFACE2, fg=TEXT_PRIMARY,
                                  font=(FONT_MONO, 9), activebackground=CYAN_DIM)
            opt.pack(fill=tk.X, padx=2, pady=2)

            model_status = tk.Label(body, text="", font=(FONT_MONO, 7),
                                    fg=CYAN, bg=BG_BASE)
            model_status.pack(anchor=tk.W, pady=(0, 12))

            def on_model_change(*_):
                global _preferred_model
                chosen_name = sel_var.get().split("  ·")[0].strip()
                chosen_id   = next(
                    (mid for mid, mname in zip(model_ids, model_names)
                     if mname.split("  ·")[0].strip() == chosen_name),
                    model_ids[0]
                )
                _preferred_model = chosen_id
                save_auth(_access_token, _refresh_token, _user_info)
                model_status.configure(text=f"model set to {chosen_name.lower()}", fg=CYAN)
                self.after(2000, lambda: model_status.configure(text=""))
                self._update_ss_hotkey_color()

            sel_var.trace_add("write", on_model_change)

        # SCREENSHOTS section
        tk.Frame(body, bg=BORDER, height=1).pack(fill=tk.X, pady=(0, 0))
        section_label("SCREENSHOTS")

        ss_path_lbl = tk.Label(body,
                               text=f"saved to  {SCREENSHOTS_DIR}",
                               font=(FONT_MONO, 7), fg=TEXT_MUTED, bg=BG_BASE,
                               wraplength=380, justify=tk.LEFT)
        ss_path_lbl.pack(anchor=tk.W, pady=(0, 10))

        del_outer = tk.Frame(body, bg=RED, padx=1, pady=1)
        del_outer.pack(anchor=tk.W, pady=(0, 6))
        del_btn = tk.Label(del_outer, text="  delete all screenshots  ",
                           font=(FONT_MONO, 8),
                           fg=RED, bg=BG_BASE, cursor="hand2",
                           padx=8, pady=5)
        del_btn.pack()

        self._ss_status = tk.Label(body, text="",
                                   font=(FONT_MONO, 8), fg=TEXT_MUTED, bg=BG_BASE)
        self._ss_status.pack(anchor=tk.W)

        def do_delete_screenshots(_=None):
            n = delete_all_screenshots()
            self._ss_status.configure(
                text=f"deleted {n} screenshot{'s' if n != 1 else ''}.", fg=RED)
            self.after(3000, lambda: self._ss_status.configure(text=""))

        del_btn.bind("<Button-1>", do_delete_screenshots)
        del_btn.bind("<Enter>",
            lambda e: (del_btn.configure(bg=RED, fg=BG_BASE),
                       del_outer.configure(bg=RED)))
        del_btn.bind("<Leave>",
            lambda e: (del_btn.configure(bg=BG_BASE, fg=RED),
                       del_outer.configure(bg=RED)))

    def _update_ss_hotkey_color(self):
        """Dim the screenshot shortcut label if current model has no vision."""
        if not self._ss_hotkey_label:
            return
        model  = _preferred_model or _user_info.get("model", "")
        vision = VISION_CAPABLE_CLIENT.get(model, True)
        self._ss_hotkey_label.configure(
            fg=RED if not vision else TEXT_PRIMARY,
        )

    def _build_about_page(self, container):
        body = tk.Frame(container, bg=BG_BASE, padx=28, pady=24)
        body.pack(fill=tk.BOTH, expand=True)

        # Big wordmark
        model_name = MODEL_DISPLAY.get(_preferred_model or _user_info.get("model", ""), "AI")
        tk.Label(body, text="APEX",
                 font=(FONT_MONO, 28, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_BASE).pack(anchor=tk.W)
        tk.Label(body, text=f"v{VERSION} — {model_name.lower()}",
                 font=(FONT_MONO, 8),
                 fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 20))

        tk.Label(body,
                 text="desktop ai assistant with screenshot, highlight,\nand quick-ask capabilities.",
                 font=(FONT_MONO, 9), fg=TEXT_SECONDARY, bg=BG_BASE,
                 justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 24))

        tk.Frame(body, bg=BORDER, height=1).pack(fill=tk.X, pady=(0, 14))

        tk.Label(body, text="SHORTCUTS",
                 font=(FONT_MONO, 7, "bold"),
                 fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 10))

        active_model = _preferred_model or _user_info.get("model", "")
        vision_ok    = VISION_CAPABLE_CLIENT.get(active_model, True)

        for keys, desc in [
            ("Ctrl+Shift+S", "capture screenshot and ask"),
            ("Ctrl+Shift+H", "send highlighted text"),
            ("Ctrl+Shift+A", "open quick ask"),
            ("Ctrl+Shift+Q", "quit"),
        ]:
            is_ss = keys == "Ctrl+Shift+S"
            row   = tk.Frame(body, bg=BG_BASE)
            row.pack(fill=tk.X, pady=3)
            chip_bg   = BG_SURFACE2 if (vision_ok or not is_ss) else "#2a1010"
            chip_fg   = TEXT_PRIMARY if (vision_ok or not is_ss) else RED
            chip      = tk.Frame(row, bg=chip_bg, padx=8, pady=3)
            chip.pack(side=tk.LEFT)
            lbl = tk.Label(chip, text=keys, font=(FONT_MONO, 8),
                           fg=chip_fg, bg=chip_bg)
            lbl.pack()
            if is_ss:
                self._ss_hotkey_label = lbl
            suffix = "  (no vision — model can't see screenshots)" if is_ss and not vision_ok else ""
            tk.Label(row, text=desc + suffix,
                     font=(FONT_MONO, 8),
                     fg=RED if (is_ss and not vision_ok) else TEXT_SECONDARY,
                     bg=BG_BASE).pack(side=tk.LEFT, padx=12)

    # ── Scroll helpers ────────────────────────────────────────────────────────

    def _on_frame_configure(self, _=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._canvas.itemconfig(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_mousewheel(self, widget):
        """Bind mousewheel scroll on widget and all its descendants."""
        widget.bind("<MouseWheel>",
                    lambda e: self._canvas.yview_scroll(int(-1*(e.delta/120)), "units"),
                    add="+")
        widget.bind("<Button-4>",
                    lambda e: self._canvas.yview_scroll(-1, "units"), add="+")
        widget.bind("<Button-5>",
                    lambda e: self._canvas.yview_scroll(1, "units"), add="+")
        for child in widget.winfo_children():
            self._bind_mousewheel(child)

    def _scroll_bottom(self):
        self._canvas.update_idletasks()
        self._canvas.yview_moveto(1.0)

    # ── Message helpers ───────────────────────────────────────────────────────

    def _remove_empty(self):
        if self._empty_label:
            self._empty_label.destroy()
            self._empty_label = None

    def _add_user_bubble(self, label="You") -> MessageBubble:
        self._remove_empty()
        ts = datetime.now().strftime("%H:%M")
        b = MessageBubble(self._msg_frame, "user", label, ts)
        self.after(50, lambda: self._bind_mousewheel(b))
        return b

    def _add_assistant_bubble(self) -> MessageBubble:
        self._remove_empty()
        ts = datetime.now().strftime("%H:%M")
        b = MessageBubble(self._msg_frame, "assistant", "Apex", ts)
        self.after(50, lambda: self._bind_mousewheel(b))
        return b

    def _add_system_note(self, text: str):
        self._remove_empty()
        f = tk.Frame(self._msg_frame, bg=BG_BASE, pady=4)
        f.pack(fill=tk.X, padx=20)
        lbl = tk.Label(f, text=f"// {text}", font=(FONT_MONO, 7),
                 fg=TEXT_MUTED, bg=BG_BASE)
        lbl.pack(anchor=tk.CENTER)
        self._bind_mousewheel(f)
        self._scroll_bottom()

    def _minimize(self):
        self.iconify()

    def _restore(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide(self): self._minimize()

    def show(self):
        self._restore()
        self._switch_page("chat")
        self.input_box.focus_set()

    def _clear_chat(self):
        if self.conversation:
            self._save_current_chat()
        self.conversation.clear()
        for w in self._msg_frame.winfo_children():
            w.destroy()
        self._empty_label = tk.Label(self._msg_frame, text="", bg=BG_BASE)
        self._empty_label.pack(expand=True, pady=4)
        self._current_bubble = None

    def _save_current_chat(self):
        if not self.conversation: return
        if not any(m["role"] == "user" for m in self.conversation): return
        filepath = save_chat_to_file(self.conversation)
        if filepath:
            self._add_system_note("conversation saved")

    def _new_overlay(self) -> FloatingOverlay:
        if self._overlay and not self._overlay._dismissed:
            try: self._overlay._dismiss()
            except: pass
        ov = FloatingOverlay(self)
        self._overlay = ov
        return ov

    def _on_enter(self, event):
        if not event.state & 0x1:
            self._send_text()
            return "break"

    def _send_text(self):
        text = self.input_box.get("1.0", tk.END).strip()
        if not text: return
        self.input_box.delete("1.0", tk.END)
        b = self._add_user_bubble("You")
        b.append(text)
        self._scroll_bottom()
        self.conversation.append({"role": "user", "content": text})
        self._run_claude()

    def send_screenshot(self, img: Image.Image, prompt: str):
        # 1. Save to disk
        save_screenshot_to_disk(img, prompt)

        # 2. Add thumbnail card to screenshots page (background, no console open)
        ts = datetime.now().strftime("%H:%M")
        ss_thumb = img.copy(); ss_thumb.thumbnail((100, 70))
        card_photo = ImageTk.PhotoImage(ss_thumb)
        self._images.append(card_photo)
        self._add_screenshot_card(card_photo, prompt, ts)

        # 3. Store in conversation silently (no bubble, no restore)
        b64 = pil_to_b64(img)
        self.conversation.append({
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64",
                            "media_type": "image/png",
                            "data": b64}},
                {"type": "text", "text": prompt}
            ]
        })

        # 4. Stream response to overlay only — console stays minimised
        self._run_claude_overlay_only(self.conversation[:])

    def _run_claude_overlay_only(self, messages: list):
        """Run Claude and stream result to overlay only — console stays hidden."""
        overlay = self._new_overlay()
        overlay.append("thinking...")

        def worker():
            try:
                first = [True]
                full_reply = []
                def on_chunk(chunk):
                    if first[0]:
                        msg_queue.put(("overlay_clear", None))
                        first[0] = False
                    full_reply.append(chunk)
                    msg_queue.put(("overlay_chunk", chunk))
                ask_claude(messages, on_chunk=on_chunk)
                # Append assistant reply to real conversation for context continuity
                reply_text = "".join(full_reply)
                if reply_text:
                    self.conversation.append({"role": "assistant", "content": reply_text})
                msg_queue.put(("overlay_done", None))
            except Exception as e:
                msg_queue.put(("overlay_error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def send_highlighted_text(self, text: str):
        MAX_CHARS = 8000
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n\n[truncated — {len(text) - MAX_CHARS} chars omitted]"
        # Store in conversation silently — no bubble, no restore
        self.conversation.append({
            "role": "user",
            "content": (
                f'The user highlighted this text:\n\n"""\n{text}\n"""\n\n'
                "Please explain, summarise, answer, or help with it."
            )
        })
        # Stream response to overlay only — console stays minimised
        self._run_claude_overlay_only(self.conversation[:])

    def _run_claude(self):
        self.status_var.set("thinking...")
        ab = self._add_assistant_bubble()
        self._current_bubble = ab
        overlay = self._new_overlay()
        overlay.append("thinking...")

        def worker():
            try:
                first = [True]
                def on_chunk(chunk):
                    if first[0]:
                        msg_queue.put(("overlay_clear", None))
                        first[0] = False
                    msg_queue.put(("chunk", chunk))
                full = ask_claude(self.conversation, on_chunk=on_chunk)
                self.conversation.append({"role": "assistant", "content": full})
                msg_queue.put(("done", None))
            except Exception as e:
                msg_queue.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _process_queue(self):
        try:
            while True:
                kind, data = msg_queue.get_nowait()
                if kind == "chunk":
                    if self._current_bubble:
                        self._current_bubble.append(data)
                        self._scroll_bottom()
                    if self._overlay and not self._overlay._dismissed:
                        self._overlay.append(data)
                elif kind == "overlay_clear":
                    if self._overlay and not self._overlay._dismissed:
                        self._overlay.clear_text()
                elif kind == "done":
                    self.status_var.set("")
                    self._current_bubble = None
                elif kind == "overlay_chunk":
                    if self._overlay and not self._overlay._dismissed:
                        self._overlay.append(data)
                elif kind == "overlay_done":
                    pass  # overlay auto-dismisses via timer
                elif kind == "overlay_error":
                    if self._overlay and not self._overlay._dismissed:
                        self._overlay.clear_text()
                        self._overlay.append(f"error: {data}")
                elif kind == "screenshot":
                    img, prompt = data
                    self.send_screenshot(img, prompt)
                elif kind == "highlight":
                    self.send_highlighted_text(data)
                elif kind == "show":
                    self.after(0, self._open_mini_chat)
                elif kind == "error":
                    msg = str(data)
                    self.status_var.set("")
                    if "Session expired" in msg:
                        clear_auth()
                        self.destroy()
                        login = LoginScreen(on_complete=lambda: ChatWindow().mainloop())
                        login.mainloop()
                        return
                    self._add_system_note(f"error: {msg}")
                    if self._overlay and not self._overlay._dismissed:
                        self._overlay.clear_text()
                        self._overlay.append(f"error: {msg}")
        except queue.Empty:
            pass
        self.after(50, self._process_queue)

    def _open_mini_chat(self, note=None):
        def on_send(text):
            # Store in conversation — no bubble, no restore
            self.conversation.append({"role": "user", "content": text})
            self._run_claude_overlay_only(self.conversation[:])
        MiniChat(self, on_send, note=note)

    def _start_hotkeys(self):
        current_keys = set()
        SCREENSHOT_CHARS = {'\x13', 's', 'S'}
        HIGHLIGHT_CHARS  = {'\x08', 'h', 'H'}
        CHAT_CHARS       = {'\x01', 'a', 'A'}
        QUIT_CHARS       = {'\x11', 'q', 'Q'}

        def normalize(k):
            if k == keyboard.Key.ctrl_r:  return keyboard.Key.ctrl_l
            if k == keyboard.Key.shift_r: return keyboard.Key.shift
            if k == keyboard.Key.alt_r:   return keyboard.Key.alt_l
            return k

        def get_char(key):
            try:    return key.char
            except: return None

        def is_ctrl_shift(keys):
            return keyboard.Key.ctrl_l in keys and keyboard.Key.shift in keys

        def on_press(key):
            current_keys.add(normalize(key))
            char = get_char(key)
            if is_ctrl_shift(current_keys):
                if char in SCREENSHOT_CHARS:
                    current_keys.clear()
                    self.after(0, self._trigger_screenshot)
                elif char in HIGHLIGHT_CHARS:
                    current_keys.clear()
                    self.after(0, self._trigger_highlight)
                elif char in CHAT_CHARS:
                    current_keys.clear()
                    self.after(0, self._open_mini_chat)
                elif char in QUIT_CHARS:
                    self.after(0, self.destroy)

        def on_release(key):
            current_keys.discard(normalize(key))

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()

    def _trigger_screenshot(self):
        if self._overlay and not self._overlay._dismissed:
            try: self._overlay._dismiss()
            except: pass
        self.iconify(); self.update()
        time.sleep(0.2)

        def got_image(img):
            def on_submit(i, p):
                msg_queue.put(("screenshot", (i, p)))
                # do NOT restore the main console — answer goes to overlay only
            dlg = PromptDialog(self, img, on_submit)
            dlg.protocol = lambda *a: None
            dlg.after(80, lambda: (
                dlg.lift(),
                dlg.attributes("-topmost", True),
                dlg.entry.focus_force()
            ))
        ScreenshotSelector(self, got_image)

    def _trigger_highlight(self):
        from pynput.keyboard import Controller, Key
        import platform
        kb = Controller()
        old_clip = ""
        try:    old_clip = pyperclip.paste()
        except: pass
        for k in (Key.ctrl_l, Key.ctrl_r, Key.shift, Key.shift_r):
            try: kb.release(k)
            except: pass
        time.sleep(0.15)
        if platform.system() == "Darwin":
            with kb.pressed(Key.cmd):
                kb.press('c'); kb.release('c')
        else:
            with kb.pressed(Key.ctrl_l):
                kb.press('c'); kb.release('c')
        time.sleep(0.4)
        try:    new_clip = pyperclip.paste()
        except: new_clip = ""
        if new_clip and new_clip.strip() and new_clip != old_clip:
            msg_queue.put(("highlight", new_clip))
        else:
            self.after(100, lambda: self._open_mini_chat(
                note="no text detected — highlight first, then Ctrl+Shift+H"))



# ── Setup Screen ──────────────────────────────────────────────────────────────


# ── Login Screen ──────────────────────────────────────────────────────────────

class LoginScreen(tk.Tk):
    """Replaces SetupScreen — users log in or register via the Apex backend."""

    def __init__(self, on_complete):
        super().__init__()
        self.on_complete  = on_complete
        self._drag_x = 0
        self._drag_y = 0
        self.configure(bg=BG_BASE, highlightthickness=1, highlightbackground=CYAN)
        self.title("Apex Assistant")
        self.geometry("480x460")
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"480x460+{(sw-480)//2}+{(sh-460)//2}")
        if sys.platform == "win32":
            self._remove_titlebar_win32()

        # ── Topbar ──────────────────────────────────────────────────────────
        top = tk.Frame(self, bg=BG_SIDEBAR, height=36)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        dot = tk.Canvas(top, width=8, height=8, bg=BG_SIDEBAR, highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(14, 6), pady=14)
        dot.create_oval(0, 0, 8, 8, fill=CYAN, outline="")

        title_lbl = tk.Label(top, text="APEX", font=(FONT_MONO, 9, "bold"),
                             fg=TEXT_SECONDARY, bg=BG_SIDEBAR)
        title_lbl.pack(side=tk.LEFT)

        close_btn = tk.Label(top, text="×", font=(FONT_MONO, 14),
                             fg=TEXT_MUTED, bg=BG_SIDEBAR, cursor="hand2", padx=12)
        close_btn.pack(side=tk.RIGHT, fill=tk.Y)
        close_btn.bind("<Button-1>", lambda e: self.destroy())
        close_btn.bind("<Enter>",    lambda e: close_btn.configure(fg=RED))
        close_btn.bind("<Leave>",    lambda e: close_btn.configure(fg=TEXT_MUTED))

        for w in (top, dot, title_lbl):
            w.bind("<ButtonPress-1>",  self._drag_start)
            w.bind("<B1-Motion>",      self._drag_motion)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X)

        # ── Body ────────────────────────────────────────────────────────────
        body = tk.Frame(self, bg=BG_BASE, padx=36, pady=28)
        body.pack(fill=tk.BOTH, expand=True)

        tk.Label(body, text="APEX", font=(FONT_MONO, 22, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_BASE).pack(anchor=tk.W)
        tk.Label(body, text="sign in to continue",
                 font=(FONT_MONO, 8), fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 20))

        # Email
        tk.Label(body, text="EMAIL", font=(FONT_MONO, 7, "bold"),
                 fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 4))
        email_frame = tk.Frame(body, bg=BG_SURFACE2,
                               highlightthickness=1, highlightbackground=BORDER)
        email_frame.pack(fill=tk.X, pady=(0, 12))
        self._email = tk.Entry(email_frame, bg=BG_SURFACE2, fg=TEXT_PRIMARY,
                               font=(FONT_MONO, 10), relief=tk.FLAT,
                               insertbackground=CYAN, selectbackground=CYAN_DIM)
        self._email.pack(fill=tk.X, padx=10, pady=7)
        self._email.bind("<FocusIn>",  lambda e: email_frame.configure(highlightbackground=CYAN))
        self._email.bind("<FocusOut>", lambda e: email_frame.configure(highlightbackground=BORDER))

        # Password
        tk.Label(body, text="PASSWORD", font=(FONT_MONO, 7, "bold"),
                 fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 4))
        pw_frame = tk.Frame(body, bg=BG_SURFACE2,
                            highlightthickness=1, highlightbackground=BORDER)
        pw_frame.pack(fill=tk.X, pady=(0, 16))
        self._password = tk.Entry(pw_frame, show="•", bg=BG_SURFACE2, fg=TEXT_PRIMARY,
                                  font=(FONT_MONO, 10), relief=tk.FLAT,
                                  insertbackground=CYAN, selectbackground=CYAN_DIM)
        self._password.pack(fill=tk.X, padx=10, pady=7)
        self._password.bind("<FocusIn>",  lambda e: pw_frame.configure(highlightbackground=CYAN))
        self._password.bind("<FocusOut>", lambda e: pw_frame.configure(highlightbackground=BORDER))
        self._password.bind("<Return>",   lambda e: self._do_login())

        # Buttons row
        btn_row = tk.Frame(body, bg=BG_BASE)
        btn_row.pack(fill=tk.X, pady=(0, 10))

        # Sign in button
        login_outer = tk.Frame(btn_row, bg=CYAN, padx=1, pady=1)
        login_outer.pack(side=tk.LEFT)
        login_btn = tk.Label(login_outer, text="  sign in  ",
                             font=(FONT_MONO, 8), fg=BG_BASE, bg=CYAN, cursor="hand2",
                             padx=8, pady=5)
        login_btn.pack()
        login_btn.bind("<Button-1>", lambda e: self._do_login())
        login_btn.bind("<Enter>",    lambda e: login_btn.configure(bg=CYAN_HOVER, fg=BG_BASE))
        login_btn.bind("<Leave>",    lambda e: login_btn.configure(bg=CYAN, fg=BG_BASE))

        # Register button
        reg_outer = tk.Frame(btn_row, bg=BORDER, padx=1, pady=1)
        reg_outer.pack(side=tk.LEFT, padx=(10, 0))
        reg_btn = tk.Label(reg_outer, text="  create account  ",
                           font=(FONT_MONO, 8), fg=CYAN, bg=BG_BASE, cursor="hand2",
                           padx=8, pady=5)
        reg_btn.pack()
        reg_btn.bind("<Button-1>", lambda e: self._do_register())
        reg_btn.bind("<Enter>",    lambda e: (reg_btn.configure(bg=CYAN, fg=BG_BASE),
                                              reg_outer.configure(bg=CYAN)))
        reg_btn.bind("<Leave>",    lambda e: (reg_btn.configure(bg=BG_BASE, fg=CYAN),
                                              reg_outer.configure(bg=BORDER)))

        # Status / error label
        self._status = tk.Label(body, text="", font=(FONT_MONO, 8),
                                fg=RED, bg=BG_BASE, wraplength=360, justify=tk.LEFT)
        self._status.pack(anchor=tk.W)

        self._buttons = [login_btn, reg_btn]
        self._email.focus_set()
        self.after(3500, lambda: _show_update_dialog_if_pending(self))

    # ── Titlebar strip + native drag (Windows) ──────────────────────────────
    def _remove_titlebar_win32(self):
        import ctypes
        import ctypes.wintypes as wt

        GWL_STYLE        = -16
        WS_CAPTION       = 0x00C00000
        WS_THICKFRAME    = 0x00040000
        WS_SYSMENU       = 0x00080000
        WS_MAXIMIZEBOX   = 0x00010000
        SWP_NOMOVE       = 0x0002
        SWP_NOSIZE       = 0x0001
        SWP_NOZORDER     = 0x0004
        SWP_FRAMECHANGED = 0x0020

        self.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        style &= ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MAXIMIZEBOX)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
        ctypes.windll.user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
        self.update_idletasks()

        # Native drag via WM_NCHITTEST — same approach as ChatWindow
        WM_NCHITTEST = 0x0084
        HTCAPTION    = 2
        GWLP_WNDPROC = -4
        TOPBAR_H     = 36

        prototype = ctypes.WINFUNCTYPE(
            ctypes.c_int64, ctypes.c_int64, ctypes.c_uint,
            ctypes.c_int64, ctypes.c_int64,
        )
        ctypes.windll.user32.GetWindowLongPtrW.restype  = ctypes.c_int64
        ctypes.windll.user32.GetWindowLongPtrW.argtypes = [ctypes.c_int64, ctypes.c_int]
        original_proc = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)

        cwp = ctypes.windll.user32.CallWindowProcW
        cwp.restype  = ctypes.c_int64
        cwp.argtypes = [ctypes.c_int64, ctypes.c_int64, ctypes.c_uint,
                        ctypes.c_int64, ctypes.c_int64]

        def wnd_proc(h, msg, wparam, lparam):
            if msg == WM_NCHITTEST:
                cx = ctypes.c_int16(lparam & 0xFFFF).value
                cy = ctypes.c_int16((lparam >> 16) & 0xFFFF).value
                rect = wt.RECT()
                ctypes.windll.user32.GetWindowRect(h, ctypes.byref(rect))
                if 0 <= (cy - rect.top) <= TOPBAR_H:
                    return HTCAPTION
            return cwp(original_proc, h, msg, wparam, lparam)

        self._wnd_proc_ref = prototype(wnd_proc)
        ctypes.windll.user32.SetWindowLongPtrW.restype  = ctypes.c_int64
        ctypes.windll.user32.SetWindowLongPtrW.argtypes = [ctypes.c_int64, ctypes.c_int, ctypes.c_int64]
        ctypes.windll.user32.SetWindowLongPtrW(
            hwnd, GWLP_WNDPROC,
            ctypes.cast(self._wnd_proc_ref, ctypes.c_void_p).value,
        )

    # ── Drag (fallback for non-Windows) ─────────────────────────────────────
    def _drag_start(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_motion(self, event):
        self.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    # ── Auth ─────────────────────────────────────────────────────────────────
    def _set_status(self, msg: str, color: str = RED):
        self._status.configure(text=msg, fg=color)
        self.update()

    def _set_buttons_enabled(self, enabled: bool):
        state = tk.NORMAL if enabled else tk.DISABLED
        for w in self._buttons:
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _handle_http_error(self, e: "urllib.error.HTTPError", fallback: str) -> str:
        try:
            body = json.loads(e.read())
            return body.get("detail", fallback)
        except Exception:
            return fallback

    def _do_login(self):
        email = self._email.get().strip()
        pw    = self._password.get()
        if not email or not pw:
            self._set_status("Please enter email and password.")
            return
        self._set_status("signing in...", CYAN)
        self._set_buttons_enabled(False)

        def worker():
            try:
                res = _api_request("POST", "/auth/login", {"email": email, "password": pw})
                self.after(0, lambda: self._on_auth_success(res))
            except urllib.error.HTTPError as e:
                msg = self._handle_http_error(e, "Login failed.")
                self.after(0, lambda m=msg: (self._set_status(m), self._set_buttons_enabled(True)))
            except Exception as e:
                msg = f"Cannot connect to server.\n{e}"
                self.after(0, lambda m=msg: (self._set_status(m), self._set_buttons_enabled(True)))

        threading.Thread(target=worker, daemon=True).start()

    def _do_register(self):
        email = self._email.get().strip()
        pw    = self._password.get()
        if not email or not pw:
            self._set_status("Please enter email and password.")
            return
        if len(pw) < 8:
            self._set_status("Password must be at least 8 characters.")
            return
        self._set_status("creating account...", CYAN)
        self._set_buttons_enabled(False)

        def worker():
            try:
                res = _api_request("POST", "/auth/register", {"email": email, "password": pw})
                self.after(0, lambda: self._on_auth_success(res))
            except urllib.error.HTTPError as e:
                msg = self._handle_http_error(e, "Registration failed.")
                self.after(0, lambda m=msg: (self._set_status(m), self._set_buttons_enabled(True)))
            except Exception as e:
                msg = f"Cannot connect to server.\n{e}"
                self.after(0, lambda m=msg: (self._set_status(m), self._set_buttons_enabled(True)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_auth_success(self, res: dict):
        save_auth(res["access_token"], res["refresh_token"], res["user"])
        self.destroy()
        self.on_complete()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Check for updates in background — never blocks startup
    update_thread = threading.Thread(target=check_for_updates, daemon=True)
    update_thread.start()

    def launch_app():
        app = ChatWindow()
        app.mainloop()

    # Try loading saved tokens first
    if load_auth():
        # Validate by refreshing — if it works, go straight to app
        if refresh_access_token():
            launch_app()
        else:
            clear_auth()
            login = LoginScreen(on_complete=launch_app)
            login.mainloop()
    else:
        login = LoginScreen(on_complete=launch_app)
        login.mainloop()
