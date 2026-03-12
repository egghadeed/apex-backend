# New Features Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add chat search, custom hotkeys, custom system prompt, overlay follow-up input, voice input, and multi-tab chats to apex_v2.py.

**Architecture:** All new user-configurable settings are stored in `~/.apex_settings` (JSON), loaded at startup via `load_settings()`, saved via `save_settings()`. The settings page gains new sections for each feature. The backend gets two new additions: an optional `system` field on `/chat/stream`, and a new `/chat/transcribe` endpoint for Whisper voice transcription.

**Tech Stack:** Python/Tkinter (client), FastAPI (backend), sounddevice + numpy (audio recording), OpenAI Whisper API (transcription)

---

## Task 1: Custom System Prompt

**Files:**
- Modify: `apex_v2.py` — globals, `load_settings`, `save_settings`, `ask_claude`, `_build_settings_page`
- Modify: `routers/chat.py` — `ChatRequest`, `_stream_anthropic`, `_stream_openai`, `stream_chat`

### Step 1 — Add global and load/save

In `apex_v2.py`, after `_overlay_duration_ms: int = 8000` add:

```python
_custom_system_prompt: str = ""  # empty = use backend default
```

In `load_settings()`, add inside the `try` block after loading `overlay_duration_ms`:

```python
_custom_system_prompt = str(data.get("custom_system_prompt", ""))
```

In `save_settings()`, update the dict:

```python
json.dump({
    "overlay_duration_ms":    _overlay_duration_ms,
    "custom_system_prompt":   _custom_system_prompt,
}, f)
```

### Step 2 — Pass system prompt to backend in ask_claude

In `ask_claude`, after:
```python
if _preferred_model:
    payload["model"] = _preferred_model
```
Add:
```python
if _custom_system_prompt:
    payload["system"] = _custom_system_prompt
```

### Step 3 — Backend: accept system field

In `routers/chat.py`, update `ChatRequest`:
```python
class ChatRequest(BaseModel):
    messages: list[Message]
    model:    Optional[str] = None
    system:   Optional[str] = None   # client-supplied system prompt override
```

In `stream_chat`, after resolving model:
```python
system_prompt = body.system if body.system else None
```

Pass `system_prompt` to both stream functions. Update `_stream_anthropic` signature:
```python
def _stream_anthropic(model: str, messages: list[dict], system: str = None):
```
And change `system=SYSTEM_PROMPT` to `system=system or SYSTEM_PROMPT`.

Update `_stream_openai` signature:
```python
def _stream_openai(model: str, messages: list[dict], system: str = None):
```
And replace all uses of `SYSTEM_PROMPT` inside with `system or SYSTEM_PROMPT`.

Update calls in `stream_chat`:
```python
if _is_openai(model):
    gen = _stream_openai(model, raw_messages, system=system_prompt)
else:
    gen = _stream_anthropic(model, raw_messages, system=system_prompt)
```

### Step 4 — Add settings UI

In `_build_settings_page`, after the POPUP section, add:

```python
tk.Frame(body, bg=BORDER, height=1).pack(fill=tk.X, pady=(12, 0))
section_label("SYSTEM PROMPT")

tk.Label(body,
         text="custom instructions sent with every message\n(leave blank to use default)",
         font=(FONT_MONO, 7), fg=TEXT_MUTED, bg=BG_BASE,
         justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 6))

sp_frame = tk.Frame(body, bg=BG_SURFACE2,
                    highlightthickness=1, highlightbackground=BORDER)
sp_frame.pack(fill=tk.X, pady=(0, 6))

sp_box = tk.Text(sp_frame, height=4, wrap=tk.WORD,
                 bg=BG_SURFACE2, fg=TEXT_PRIMARY,
                 font=(FONT_MONO, 9), relief=tk.FLAT,
                 insertbackground=CYAN, padx=8, pady=6)
sp_box.pack(fill=tk.X)
if _custom_system_prompt:
    sp_box.insert("1.0", _custom_system_prompt)

sp_status = tk.Label(body, text="", font=(FONT_MONO, 7), fg=CYAN, bg=BG_BASE)
sp_status.pack(anchor=tk.W)

def save_sp(_=None):
    global _custom_system_prompt
    _custom_system_prompt = sp_box.get("1.0", tk.END).strip()
    save_settings()
    sp_status.configure(text="saved")
    body.after(1500, lambda: sp_status.configure(text=""))

sp_save = tk.Label(body, text="  save prompt  ",
                   font=(FONT_MONO, 8), fg=BG_BASE, bg=CYAN,
                   cursor="hand2", padx=8, pady=4)
sp_save.pack(anchor=tk.W, pady=(4, 0))
sp_save.bind("<Button-1>", save_sp)
```

### Step 5 — Verify
- Set a custom system prompt in settings (e.g. "Always reply in ALL CAPS")
- Send a message in chat — response should follow the instruction
- Clear and save — response should return to normal

### Step 6 — Commit
```bash
git add apex_v2.py routers/chat.py
git commit -m "feat: custom system prompt in settings, passed to backend"
```

---

## Task 2: Custom Hotkeys

**Files:**
- Modify: `apex_v2.py` — globals, `load_settings`, `save_settings`, `_start_hotkeys`, `_build_settings_page`

### Step 1 — Add globals

After `_custom_system_prompt`, add:
```python
_hotkeys: dict = {
    "screenshot": "s",
    "highlight":  "h",
    "chat":       "a",
    "quit":       "q",
    "voice":      "v",   # for Task 5
}
```

### Step 2 — Update load_settings

```python
loaded_hk = data.get("hotkeys", {})
for k in _hotkeys:
    if k in loaded_hk and len(str(loaded_hk[k])) == 1:
        _hotkeys[k] = str(loaded_hk[k]).lower()
```

### Step 3 — Update save_settings

Add `"hotkeys": _hotkeys` to the dict passed to `json.dump`.

### Step 4 — Update _start_hotkeys

Replace the hardcoded char sets with dynamic lookup:
```python
def _start_hotkeys(self):
    current_keys = set()

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

    def matches(char, action):
        k = _hotkeys.get(action, "")
        if not char or not k: return False
        # Match bare letter (with or without Shift/Ctrl modifiers on char value)
        return char.lower() == k.lower() or (
            ord(char) < 32 and chr(ord(char) + 64).lower() == k.lower()
        )

    def on_press(key):
        current_keys.add(normalize(key))
        char = get_char(key)
        if is_ctrl_shift(current_keys):
            if matches(char, "screenshot"):
                current_keys.clear()
                self.after(0, self._trigger_screenshot)
            elif matches(char, "highlight"):
                current_keys.clear()
                self.after(0, self._trigger_highlight)
            elif matches(char, "chat"):
                current_keys.clear()
                self.after(0, self._open_mini_chat)
            elif matches(char, "quit"):
                self.after(0, self.destroy)
            elif matches(char, "voice"):
                current_keys.clear()
                self.after(0, self._trigger_voice)

    def on_release(key):
        current_keys.discard(normalize(key))

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
```

### Step 5 — Add settings UI

After the SYSTEM PROMPT section add a HOTKEYS section:
```python
tk.Frame(body, bg=BORDER, height=1).pack(fill=tk.X, pady=(12, 0))
section_label("HOTKEYS")
tk.Label(body, text="all hotkeys use Ctrl+Shift+<key>",
         font=(FONT_MONO, 7), fg=TEXT_MUTED, bg=BG_BASE).pack(anchor=tk.W, pady=(0, 8))

hk_entries = {}
hk_status  = tk.Label(body, text="", font=(FONT_MONO, 7), fg=CYAN, bg=BG_BASE)

for action, label_text in [
    ("screenshot", "Screenshot"),
    ("highlight",  "Highlight"),
    ("chat",       "Quick Ask"),
    ("quit",       "Quit"),
    ("voice",      "Voice Input"),
]:
    row = tk.Frame(body, bg=BG_BASE)
    row.pack(fill=tk.X, pady=2)
    tk.Label(row, text=f"Ctrl+Shift+", font=(FONT_MONO, 8),
             fg=TEXT_MUTED, bg=BG_BASE, width=14, anchor=tk.W).pack(side=tk.LEFT)
    entry_frame = tk.Frame(row, bg=BG_SURFACE2, highlightthickness=1,
                           highlightbackground=BORDER)
    entry_frame.pack(side=tk.LEFT)
    ent = tk.Entry(entry_frame, width=3, bg=BG_SURFACE2, fg=CYAN,
                   font=(FONT_MONO, 10), relief=tk.FLAT,
                   insertbackground=CYAN, justify=tk.CENTER)
    ent.insert(0, _hotkeys.get(action, ""))
    ent.pack(padx=6, pady=3)
    hk_entries[action] = ent
    tk.Label(row, text=label_text, font=(FONT_MONO, 8),
             fg=TEXT_SECONDARY, bg=BG_BASE, padx=8).pack(side=tk.LEFT)

hk_status.pack(anchor=tk.W, pady=(4, 0))

def save_hotkeys(_=None):
    global _hotkeys
    used = set()
    for action, ent in hk_entries.items():
        val = ent.get().strip()[:1].lower()
        if val and val not in used:
            _hotkeys[action] = val
            used.add(val)
    save_settings()
    hk_status.configure(text="saved — restart hotkey listener to apply")
    body.after(2500, lambda: hk_status.configure(text=""))

hk_save = tk.Label(body, text="  save hotkeys  ",
                   font=(FONT_MONO, 8), fg=BG_BASE, bg=CYAN,
                   cursor="hand2", padx=8, pady=4)
hk_save.pack(anchor=tk.W, pady=(4, 0))
hk_save.bind("<Button-1>", save_hotkeys)
```

### Step 6 — Verify
- Change screenshot key to "x" in settings, save
- Quit and relaunch app (hotkey listener reads on start)
- Ctrl+Shift+X should trigger screenshot

### Step 7 — Commit
```bash
git add apex_v2.py
git commit -m "feat: custom hotkeys configurable in settings"
```

---

## Task 3: Chat Search

**Files:**
- Modify: `apex_v2.py` — `_build_chat_page`, new `_toggle_search`, `_do_search`, `_clear_search`

### Step 1 — Add search bar to chat page

In `_build_chat_page`, after the header block and the `tk.Frame(container, bg=BORDER, height=1)` separator, add:

```python
# Search bar (hidden by default)
self._search_frame = tk.Frame(container, bg=BG_SURFACE, padx=12, pady=6)
# NOT packed yet — toggled by button

search_input_frame = tk.Frame(self._search_frame, bg=BG_SURFACE2,
                               highlightthickness=1, highlightbackground=BORDER)
search_input_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

self._search_var = tk.StringVar()
self._search_entry = tk.Entry(
    search_input_frame, textvariable=self._search_var,
    bg=BG_SURFACE2, fg=TEXT_PRIMARY, font=(FONT_MONO, 9),
    relief=tk.FLAT, insertbackground=CYAN, padx=8, pady=5,
)
self._search_entry.pack(fill=tk.X)
self._search_entry.bind("<Return>",  lambda e: self._do_search())
self._search_entry.bind("<Escape>",  lambda e: self._clear_search())
self._search_entry.bind("<KeyRelease>", lambda e: self._do_search())

self._search_status = tk.Label(self._search_frame,
    text="", font=(FONT_MONO, 7), fg=TEXT_MUTED,
    bg=BG_SURFACE, padx=8)
self._search_status.pack(side=tk.LEFT)

close_s = tk.Label(self._search_frame, text="×", font=(FONT_MONO, 10),
                   fg=TEXT_MUTED, bg=BG_SURFACE, cursor="hand2", padx=6)
close_s.pack(side=tk.RIGHT)
close_s.bind("<Button-1>", lambda e: self._clear_search())
```

### Step 2 — Add search toggle button to header

In `_build_chat_page` header block, after the "ask anything" label:
```python
search_btn = tk.Label(hdr, text="⌕", font=(FONT_MONO, 10),
                      fg=TEXT_MUTED, bg=BG_BASE, cursor="hand2", padx=6)
search_btn.pack(side=tk.RIGHT)
search_btn.bind("<Button-1>", lambda e: self._toggle_search())
search_btn.bind("<Enter>", lambda e: search_btn.configure(fg=CYAN))
search_btn.bind("<Leave>", lambda e: search_btn.configure(fg=TEXT_MUTED))
self._search_visible = False
```

### Step 3 — Add search methods

```python
def _toggle_search(self):
    self._search_visible = not self._search_visible
    if self._search_visible:
        self._search_frame.pack(fill=tk.X, after=self._search_frame.master.winfo_children()[1])
        self._search_entry.focus_set()
    else:
        self._clear_search()

def _do_search(self):
    query = self._search_var.get().strip().lower()
    if not query:
        self._search_status.configure(text="")
        return
    matches = [
        i for i, m in enumerate(self.conversation)
        if query in (
            m["content"] if isinstance(m["content"], str)
            else " ".join(p.get("text", "") for p in m["content"]
                          if isinstance(p, dict) and p.get("type") == "text")
        ).lower()
    ]
    self._search_status.configure(
        text=f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
    )
    if matches:
        # Scroll to first match — each bubble is a child of _msg_frame
        # Count non-empty children to find approximate position
        children = [w for w in self._msg_frame.winfo_children()
                    if isinstance(w, MessageBubble)]
        if matches[0] < len(children):
            target = children[matches[0]]
            self._canvas.update_idletasks()
            frame_h = self._msg_frame.winfo_height()
            target_y = target.winfo_y()
            if frame_h > 0:
                self._canvas.yview_moveto(target_y / frame_h)

def _clear_search(self):
    self._search_visible = False
    self._search_var.set("")
    self._search_status.configure(text="")
    try:
        self._search_frame.pack_forget()
    except Exception:
        pass
```

### Step 4 — Verify
- Send a few messages, then click ⌕ in chat header
- Type part of a previous message — status should show match count
- Search bar closes on Escape or ×

### Step 5 — Commit
```bash
git add apex_v2.py
git commit -m "feat: chat search bar with match count and scroll-to-first"
```

---

## Task 4: Follow-up Input on Overlay

**Files:**
- Modify: `apex_v2.py` — globals, `load_settings`, `save_settings`, `FloatingOverlay.__init__`, `_build_settings_page`

### Step 1 — Add setting global

After `_custom_system_prompt` add:
```python
_overlay_followup: bool = True
```

In `load_settings` add:
```python
_overlay_followup = bool(data.get("overlay_followup", True))
```

In `save_settings` add `"overlay_followup": _overlay_followup` to the dict.

### Step 2 — Add input row to FloatingOverlay

`FloatingOverlay.__init__` needs a `on_followup` callback parameter:
```python
def __init__(self, master, on_followup=None):
    ...
    self._on_followup = on_followup
```

At the end of `__init__`, after the text widget is packed:
```python
if _overlay_followup and on_followup:
    followup_frame = tk.Frame(self._body, bg=BG_BASE, padx=10, pady=6)
    followup_frame.pack(fill=tk.X)
    tk.Frame(self._body, bg=BORDER, height=1).pack(fill=tk.X)

    input_row = tk.Frame(followup_frame, bg=BG_SURFACE2,
                         highlightthickness=1, highlightbackground=BORDER)
    input_row.pack(fill=tk.X)

    self._followup_entry = tk.Entry(
        input_row, bg=BG_SURFACE2, fg=TEXT_PRIMARY,
        font=(FONT_MONO, 9), relief=tk.FLAT,
        insertbackground=CYAN, selectbackground=CYAN_DIM,
    )
    self._followup_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=5)
    self._followup_entry.insert(0, "follow up...")
    self._followup_entry.bind("<FocusIn>",
        lambda e: self._followup_entry.delete(0, tk.END)
            if self._followup_entry.get() == "follow up..." else None)
    self._followup_entry.bind("<FocusOut>",
        lambda e: self._followup_entry.insert(0, "follow up...")
            if not self._followup_entry.get().strip() else None)

    send_fu = tk.Label(input_row, text="↑", font=(FONT_MONO, 10, "bold"),
                       fg=CYAN, bg=BG_SURFACE2, cursor="hand2", padx=6)
    send_fu.pack(side=tk.RIGHT)
    send_fu.bind("<Button-1>", lambda e: self._submit_followup())

    self._followup_entry.bind("<Return>", lambda e: self._submit_followup())
    self._followup_entry.bind("<Escape>", lambda e: self._dismiss())
```

Add method to `FloatingOverlay`:
```python
def _submit_followup(self):
    text = self._followup_entry.get().strip()
    if not text or text == "follow up...": return
    self._dismiss()
    if self._on_followup:
        self._on_followup(text)
```

### Step 3 — Pass callback when creating overlay

In `_new_overlay`:
```python
def _new_overlay(self) -> FloatingOverlay:
    if self._overlay and not self._overlay._dismissed:
        try: self._overlay._dismiss()
        except: pass
    ov = FloatingOverlay(self, on_followup=self._handle_overlay_followup)
    self._overlay = ov
    return ov
```

Add the handler:
```python
def _handle_overlay_followup(self, text: str):
    b = self._add_user_bubble("You")
    b.append(text)
    self._scroll_bottom()
    self.conversation.append({"role": "user", "content": text})
    self._run_claude_overlay_only(self.conversation[:])
```

### Step 4 — Add toggle in settings

In POPUP section, after the duration slider, add:
```python
fu_var = tk.BooleanVar(value=_overlay_followup)

def toggle_followup():
    global _overlay_followup
    _overlay_followup = fu_var.get()
    save_settings()

fu_cb = tk.Checkbutton(
    body, text="  show follow-up input on popup",
    variable=fu_var, command=toggle_followup,
    bg=BG_BASE, fg=TEXT_SECONDARY, activebackground=BG_BASE,
    activeforeground=TEXT_PRIMARY, selectcolor=BG_SURFACE2,
    font=(FONT_MONO, 8), cursor="hand2",
)
fu_cb.pack(anchor=tk.W, pady=(10, 0))
```

### Step 5 — Verify
- Take a screenshot, answer appears in overlay with a follow-up input at bottom
- Type a follow-up, press Enter — new response streams to overlay + chat
- Disable in settings — next overlay has no input

### Step 6 — Commit
```bash
git add apex_v2.py
git commit -m "feat: follow-up input on overlay popup, toggle in settings"
```

---

## Task 5: Voice Input

**Files:**
- Modify: `apex_v2.py` — `_build_chat_bottom`, new `_trigger_voice`, `_record_and_transcribe`
- Modify: `routers/chat.py` — new `/transcribe` endpoint
- Modify: `main.py` — ensure `python-multipart` is available

**Dependencies to install:**
```bash
pip install sounddevice numpy
# backend:
pip install python-multipart
```

### Step 1 — Backend transcribe endpoint

Add to `routers/chat.py`:

```python
import tempfile, os
from fastapi import UploadFile, File

@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    user: dict = Depends(require_active_subscription),
):
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    audio_bytes = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(model="whisper-1", file=f)
        return {"text": result.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try: os.unlink(tmp_path)
        except: pass
```

### Step 2 — Client recording function

Add near top of `apex_v2.py` (after imports), a lazy import guard:
```python
_sounddevice_available = None  # checked lazily on first use
```

Add new function after `pil_to_b64`:
```python
def _record_wav(seconds: float = 5.0, samplerate: int = 16000) -> bytes:
    """Record from mic and return raw WAV bytes."""
    import sounddevice as sd
    import numpy as np
    import wave, io
    frames = sd.rec(int(seconds * samplerate), samplerate=samplerate,
                    channels=1, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(frames.tobytes())
    return buf.getvalue()

def _transcribe_wav(wav_bytes: bytes) -> str:
    """POST wav bytes to backend /chat/transcribe, return text."""
    boundary = "ApexAudioBoundary42"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BACKEND_URL}/chat/transcribe", data=body, method="POST"
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {_access_token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get("text", "")
```

### Step 3 — Add mic button to chat bottom

In `_build_chat_bottom`, after the send button is packed but before `btn_row`, add:
```python
# Vertical divider then mic button
tk.Frame(input_frame, bg=BORDER, width=1).pack(side=tk.RIGHT, fill=tk.Y)

self._mic_btn = tk.Label(
    input_frame, text="🎤", font=(FONT_MONO, 11),
    fg=TEXT_MUTED, bg=BG_SURFACE2, cursor="hand2", padx=6,
)
self._mic_btn.pack(side=tk.RIGHT, fill=tk.Y)
self._mic_btn.bind("<Button-1>", lambda e: self._trigger_voice())
self._mic_btn.bind("<Enter>", lambda e: self._mic_btn.configure(fg=CYAN))
self._mic_btn.bind("<Leave>", lambda e: self._mic_btn.configure(fg=TEXT_MUTED))
self._recording = False
```

### Step 4 — Add trigger method

```python
def _trigger_voice(self):
    try:
        import sounddevice  # noqa — check available
    except ImportError:
        self._add_system_note("voice input requires: pip install sounddevice numpy")
        return
    if self._is_generating: return
    if self._recording: return

    self._recording = True
    self._mic_btn.configure(fg=RED)
    self.status_var.set("recording 5s...")

    def worker():
        try:
            wav = _record_wav(seconds=5)
            self.after(0, lambda: self.status_var.set("transcribing..."))
            text = _transcribe_wav(wav)
            def fill_input():
                self.status_var.set("")
                self._recording = False
                self._mic_btn.configure(fg=TEXT_MUTED)
                if text:
                    self.input_box.delete("1.0", tk.END)
                    self.input_box.insert("1.0", text)
                    self.input_box.focus_set()
            self.after(0, fill_input)
        except Exception as ex:
            def show_err():
                self.status_var.set("")
                self._recording = False
                self._mic_btn.configure(fg=TEXT_MUTED)
                self._add_system_note(f"voice error: {ex}")
            self.after(0, show_err)

    threading.Thread(target=worker, daemon=True).start()
```

### Step 5 — Hotkey trigger (Ctrl+Shift+V by default — already wired in Task 2)

`_trigger_voice` is already referenced in `_start_hotkeys` from Task 2.
Add a no-op stub if Task 2 not yet done:
```python
def _trigger_voice(self):
    ...  # (already defined above)
```

### Step 6 — Verify
- Click 🎤 in chat, speak for a few seconds
- Status shows "recording..." then "transcribing..."
- Transcribed text appears in the input box, ready to edit/send
- Without sounddevice installed, should show a system note

### Step 7 — Commit
```bash
# backend
git add routers/chat.py
git commit -m "feat: /chat/transcribe endpoint via OpenAI Whisper"
# client
git add apex_v2.py
git commit -m "feat: voice input via mic button and Ctrl+Shift+V hotkey"
```

---

## Task 6: Multiple Chat Tabs

**Files:**
- Modify: `apex_v2.py` — new `ChatSession` class, `ChatWindow.__init__`, `_build_chat_page`, new tab methods, update all `self.conversation` references to use active session

### Step 1 — Add ChatSession class

After `MessageBubble` class definition, add:
```python
class ChatSession:
    def __init__(self, name: str = "Chat 1"):
        self.name         = name
        self.conversation: list = []
```

### Step 2 — Replace self.conversation with session-aware property

In `ChatWindow.__init__`, replace:
```python
self.conversation: list = []
```
With:
```python
self._sessions:       list = [ChatSession("Chat 1")]
self._active_session: int  = 0
```

Add a property so existing code using `self.conversation` still works without mass-refactoring:
```python
@property
def conversation(self) -> list:
    return self._sessions[self._active_session].conversation

@conversation.setter
def conversation(self, val: list):
    self._sessions[self._active_session].conversation = val
```

### Step 3 — Add tab bar to chat page

In `_build_chat_page`, before the scroll canvas, insert a tab bar:
```python
self._tab_bar = tk.Frame(container, bg=BG_SIDEBAR, height=30)
self._tab_bar.pack(fill=tk.X)
self._tab_bar.pack_propagate(False)
self._tab_widgets: list = []   # (btn_frame, label) per tab
self._refresh_tab_bar()
```

### Step 4 — Add tab management methods

```python
def _refresh_tab_bar(self):
    for w in self._tab_bar.winfo_children():
        w.destroy()
    self._tab_widgets.clear()

    for i, session in enumerate(self._sessions):
        is_active = (i == self._active_session)
        btn_bg = CYAN_ACTIVE if is_active else BG_SIDEBAR

        btn_f = tk.Frame(self._tab_bar, bg=btn_bg, padx=10, pady=0)
        btn_f.pack(side=tk.LEFT, fill=tk.Y)

        if is_active:
            tk.Frame(btn_f, bg=CYAN, height=2).pack(fill=tk.X)

        lbl = tk.Label(btn_f, text=session.name,
                       font=(FONT_MONO, 7),
                       fg=TEXT_PRIMARY if is_active else TEXT_MUTED,
                       bg=btn_bg, cursor="hand2", pady=4)
        lbl.pack(side=tk.LEFT)

        close_t = tk.Label(btn_f, text="×", font=(FONT_MONO, 8),
                           fg=TEXT_MUTED, bg=btn_bg, cursor="hand2", padx=2)
        close_t.pack(side=tk.LEFT)

        idx = i  # capture
        btn_f.bind("<Button-1>", lambda e, n=idx: self._switch_session(n))
        lbl.bind("<Button-1>",   lambda e, n=idx: self._switch_session(n))
        close_t.bind("<Button-1>", lambda e, n=idx: self._close_session(n))
        close_t.bind("<Enter>", lambda e, w=close_t, bg=btn_bg: w.configure(fg=RED))
        close_t.bind("<Leave>", lambda e, w=close_t, bg=btn_bg: w.configure(fg=TEXT_MUTED))

        self._tab_widgets.append((btn_f, lbl))

    # "+" new tab button
    plus = tk.Label(self._tab_bar, text=" + ", font=(FONT_MONO, 8),
                    fg=TEXT_MUTED, bg=BG_SIDEBAR, cursor="hand2")
    plus.pack(side=tk.LEFT, fill=tk.Y, padx=4)
    plus.bind("<Button-1>", lambda e: self._new_session())
    plus.bind("<Enter>", lambda e: plus.configure(fg=CYAN))
    plus.bind("<Leave>", lambda e: plus.configure(fg=TEXT_MUTED))

def _switch_session(self, idx: int):
    if idx == self._active_session: return
    # Save scroll position of current session
    self._sessions[self._active_session]._scroll_pos = self._canvas.yview()[0]

    self._active_session = idx

    # Rebuild message area
    for w in self._msg_frame.winfo_children():
        w.destroy()
    self._current_bubble = None
    self._empty_label    = None

    if not self.conversation:
        self._empty_label = tk.Label(self._msg_frame, text="", bg=BG_BASE)
        self._empty_label.pack(expand=True, pady=4)
    else:
        self._display_loaded_chat()

    # Restore scroll position
    pos = getattr(self._sessions[idx], "_scroll_pos", 1.0)
    self.after(50, lambda: self._canvas.yview_moveto(pos))

    self._refresh_tab_bar()

def _new_session(self):
    n = len(self._sessions) + 1
    self._sessions.append(ChatSession(f"Chat {n}"))
    self._switch_session(len(self._sessions) - 1)

def _close_session(self, idx: int):
    if len(self._sessions) == 1:
        # Don't close last tab — just clear it
        self._clear_chat(); return
    sess = self._sessions.pop(idx)
    if sess.conversation:
        save_chat_to_file(sess.conversation)
    new_idx = min(idx, len(self._sessions) - 1)
    self._active_session = new_idx
    self._switch_session(new_idx)
```

### Step 5 — Update _clear_chat and _save_current_chat

`_clear_chat` already uses `self.conversation` which via the property clears the active session — no change needed.

### Step 6 — Rename session on first message

In `_send_text`, after appending to `self.conversation`, add:
```python
# Auto-name tab from first user message
if len(self.conversation) == 1:
    short = text[:20].strip()
    self._sessions[self._active_session].name = short
    self._refresh_tab_bar()
```

### Step 7 — Verify
- Launch app — one "Chat 1" tab visible
- Send a message — tab renames to first ~20 chars of message
- Click "+" — new tab with empty chat
- Switch between tabs — each has independent conversation
- Click × on a tab — tab closes, saved to history

### Step 8 — Commit
```bash
git add apex_v2.py
git commit -m "feat: multiple chat tabs with independent conversations"
```

---

## Final: Push All

```bash
git push
```

Deploy backend to Render (has new `/chat/transcribe` endpoint and `system` override support).
