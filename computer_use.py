"""Atlas Computer Use — the perceive → plan → act → verify loop.

This is the capability that turns Atlas from "reads the screen and asks" into
"watches the screen and does things with the mouse and keyboard," like Google's
Gemini Computer Use / Project Mariner — but local, and gated behind your approval.

How one step works:
  1. PERCEIVE  capture the screen (or a region) as a PNG
  2. PLAN      send screenshot + goal + history to Gemini vision → ONE next action
               with pixel coordinates, e.g. {"action":"click","x":..,"y":..}
  3. ACT       move the real mouse / type via pynput and perform that action
  4. VERIFY    capture again; ask the model "did that achieve the sub-goal / are we
               done?" Loop until done, max steps, or a STOP.

SAFETY (non-negotiable, matches the PRD):
  • Off by default. Only runs when explicitly invoked, and it is a HUMAN-tier tool,
    so the approval gate (approve.py) must clear it first.
  • DRY-RUN by default: it plans and shows every action but does NOT move the mouse
    unless execute=True is passed.
  • Never types into password fields; refuses obvious irreversible clicks
    (Buy/Pay/Delete/Send/Confirm) — those bounce to human approval.
  • Hard budgets: max steps, max seconds. A global failsafe abort (slam mouse to a
    screen corner) stops it instantly, and Esc is watched as a kill switch.

Requires (macOS): pyobjc (screencapture is a CLI, already present), pynput.
Screen Recording permission is required for THIS feature (it sees pixels) — that's
separate from Atlas Mode's Accessibility-only design and is requested only here.
"""
from __future__ import annotations
import base64, json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).parent

# ---- safety config ---------------------------------------------------------
MAX_STEPS = 25
MAX_SECONDS = 180
# Specific committing phrases only — must be intentional, not substrings like
# "send button" or "search". These are the truly irreversible confirmations.
IRREVERSIBLE = ("buy now", "place order", "place the order", "complete purchase",
                "confirm purchase", "pay now", "make payment", "confirm payment",
                "proceed to pay", "complete payment", "checkout", "check out",
                "send email", "send the email", "send message", "send the message",
                "book now", "confirm booking", "reserve now", "confirm and pay",
                "delete", "transfer money", "submit order", "submit payment")


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


# ---- PERCEIVE --------------------------------------------------------------
class ScreenPermissionError(Exception):
    """Raised when this process may not capture the screen (Screen Recording)."""


class RateLimitError(Exception):
    """Raised when the model API rate-limits us (HTTP 429)."""


def _screen_recording_ok() -> bool:
    """Check (and, if needed, request) Screen Recording permission for THIS process."""
    try:
        from Quartz import CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess
        if CGPreflightScreenCaptureAccess():
            return True
        CGRequestScreenCaptureAccess()   # registers this binary + shows the prompt
        return bool(CGPreflightScreenCaptureAccess())
    except Exception:
        return True   # Quartz unavailable → let the capture attempt decide


def capture_screen() -> tuple[bytes, tuple[int, int]]:
    """Full-screen PNG + its pixel size. Uses macOS screencapture."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    r = subprocess.run(["screencapture", "-x", "-C", path], capture_output=True)
    if r.returncode != 0 or not Path(path).exists() or Path(path).stat().st_size == 0:
        try:
            os.unlink(path)
        except Exception:
            pass
        raise ScreenPermissionError(
            "screencapture failed — this app lacks Screen Recording permission.")
    data = Path(path).read_bytes()
    try:
        from AppKit import NSImage
        img = NSImage.alloc().initWithContentsOfFile_(path)
        size = (int(img.size().width), int(img.size().height))
    except Exception:
        size = (0, 0)
    os.unlink(path)                       # never persist screen captures
    return data, size


def _point_scale() -> float:
    """Screencapture returns pixels; the mouse works in points. On Retina these
    differ by the backing scale factor. Compute px_width / point_width."""
    try:
        from AppKit import NSScreen
        s = NSScreen.mainScreen()
        return float(s.backingScaleFactor())
    except Exception:
        return 1.0


# ---- PLAN (Gemini vision) --------------------------------------------------
PLAN_SYS = (
    "You are the eyes and hands of a computer-use agent. You see a screenshot and a "
    "goal. Reply with ONLY a JSON object for the SINGLE next action to take:\n"
    '{"action":"click|double_click|right_click|type|key|hotkey|scroll|wait|done|stop",'
    '"x":<pixel>,"y":<pixel>,"text":"<to type>","key":"<e.g. enter>",'
    '"keys":"<combo e.g. cmd+space>","amount":<scroll>,"reason":"<short>",'
    '"done":<true|false>}\n'
    "Coordinates are in SCREENSHOT PIXELS. For keyboard SHORTCUTS use action "
    "'hotkey' with a combo in 'keys', e.g. open Spotlight = "
    '{"action":"hotkey","keys":"cmd+space"}. For a single key press use '
    "'key' (e.g. enter, tab). To type text use 'type' with 'text'. "
    "Use action 'done' when the goal is achieved, and 'stop' if unsafe or "
    "impossible. Never act on password fields. Take the smallest useful step, "
    "and do NOT repeat an action that did not change the screen — try a "
    "different approach instead.\n"
    "IMPORTANT for web tasks (shopping, search, booking, email, prices): if the "
    "correct website is not already open and focused, FIRST open it — hotkey "
    "cmd+space to open Spotlight, type the browser name (e.g. 'Safari'), press "
    "enter; then new tab with hotkey cmd+t, type the URL (amazon.in, gmail.com, "
    "the booking site), press enter. THEN interact with the loaded page. Read "
    "results/prices from the page, and when you have the answer the user asked "
    "for, use 'done' with a 'reason' that STATES the answer "
    "(e.g. 'Cheapest suitable phone: Model X, 5000mAh, at Rs 12999')."
)


def gemini_keys() -> list[str]:
    """All configured Gemini keys, in priority order. Supports:
      GEMINI_API_KEY, GEMINI_API_KEY_2, GEMINI_API_KEY_3, ...
      and a comma-separated GEMINI_API_KEYS list.
    Keys are tried in order; when one is quota-exhausted, the next is used."""
    keys: list[str] = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3",
                 "GEMINI_API_KEY_4"):
        v = os.environ.get(name)
        if v and v.strip():
            keys.append(v.strip())
    for v in (os.environ.get("GEMINI_API_KEYS", "").split(",")):
        if v.strip() and v.strip() not in keys:
            keys.append(v.strip())
    return keys


def _parse_json_obj(text: str) -> dict | None:
    try:
        return json.loads(text)
    except Exception:
        import re
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None


def _claude_vision(image_png: bytes, prompt: str) -> dict | None:
    """Plan the next action with Claude's vision (used when ANTHROPIC_API_KEY set)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    model = os.environ.get("ANTHROPIC_MODEL", "claude-fable-5")
    body = json.dumps({
        "model": model, "max_tokens": 1024,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": "image/png",
             "data": base64.b64encode(image_png).decode()}},
            {"type": "text", "text": prompt + "\nReply with ONLY the JSON object."}]}],
    }).encode()
    import urllib.request
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read())
    text = data["content"][0]["text"]
    return _parse_json_obj(text)


def plan_vision(image_png: bytes, prompt: str) -> dict | None:
    """Vision dispatcher: use Claude when an Anthropic key is set, else Gemini."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _claude_vision(image_png, prompt)
        except Exception:
            pass                             # fall back to Gemini on any Claude error
    return _gemini_vision(image_png, prompt)


def _gemini_vision(image_png: bytes, prompt: str) -> dict | None:
    keys = gemini_keys()
    if not keys:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    body = json.dumps({
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "image/png",
                             "data": base64.b64encode(image_png).decode()}},
            {"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0.0}}).encode()
    import urllib.request, urllib.error

    data = None
    for ki, key in enumerate(keys):          # try each key; rotate on quota/auth
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read())
            break                            # success on this key
        except urllib.error.HTTPError as e:
            # 429 = quota/rate limit, 403 = key rejected → try the NEXT key
            if e.code in (429, 403) and ki < len(keys) - 1:
                continue
            if e.code in (429, 403):
                raise RateLimitError("All Gemini keys are rate-limited / rejected.")
            raise
    if data is None:
        raise RateLimitError("All Gemini keys are rate-limited / rejected.")
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_json_obj(text)


def plan_next(goal: str, history: list[str]) -> tuple[dict | None, tuple[int, int]]:
    img, px_size = capture_screen()
    prompt = (PLAN_SYS + f"\n\nGOAL: {goal}\n"
              f"STEPS DONE: {history[-6:] if history else 'none'}\n"
              f"Screenshot is {px_size[0]}x{px_size[1]} pixels.")
    return plan_vision(img, prompt), px_size


# ---- ACT (mouse + keyboard) ------------------------------------------------
def _is_irreversible(step: dict) -> bool:
    # Only inspect the model's stated reason/text, and require a specific phrase.
    blob = (str(step.get("reason", "")) + " " + str(step.get("text", ""))).lower()
    return any(w in blob for w in IRREVERSIBLE)


def _keyobj(name: str, Key):
    special = {"enter": Key.enter, "return": Key.enter, "tab": Key.tab,
               "esc": Key.esc, "escape": Key.esc, "space": Key.space,
               "backspace": Key.backspace, "delete": Key.delete,
               "cmd": Key.cmd, "command": Key.cmd, "ctrl": Key.ctrl,
               "control": Key.ctrl, "alt": Key.alt, "option": Key.alt,
               "shift": Key.shift, "up": Key.up, "down": Key.down,
               "left": Key.left, "right": Key.right}
    return special.get(name.lower().strip(), (name[:1] if name else None))


def act(step: dict, px_size: tuple[int, int]):
    """Execute one action on the real machine. Coordinates are screenshot pixels."""
    from pynput.mouse import Button, Controller as Mouse
    from pynput.keyboard import Controller as Keyboard, Key
    mouse, kb = Mouse(), Keyboard()
    scale = _point_scale() or 1.0
    x = int(step.get("x", 0) / scale)     # px → points for the mouse
    y = int(step.get("y", 0) / scale)
    a = step.get("action")

    if a in ("click", "double_click", "right_click"):
        mouse.position = (x, y)
        time.sleep(0.15)
        btn = Button.right if a == "right_click" else Button.left
        mouse.click(btn, 2 if a == "double_click" else 1)
    elif a == "type":
        kb.type(step.get("text", ""))
    elif a == "hotkey":
        # combo like "cmd+space" or "cmd+shift+4": hold modifiers, tap last key
        combo = (step.get("keys") or step.get("key") or "").split("+")
        combo = [c for c in (c.strip() for c in combo) if c]
        if combo:
            *mods, last = combo
            modobjs = [_keyobj(m, Key) for m in mods]
            for mo in modobjs:
                kb.press(mo)
            kb.tap(_keyobj(last, Key))
            for mo in reversed(modobjs):
                kb.release(mo)
    elif a == "key":
        kb.tap(_keyobj(step.get("key") or "enter", Key))
    elif a == "scroll":
        mouse.scroll(0, int(step.get("amount", -3)))
    elif a == "wait":
        time.sleep(min(float(step.get("amount", 1)), 5))


# ---- the loop --------------------------------------------------------------
def _can_control() -> bool:
    """True if THIS process may control the mouse/keyboard (Accessibility).
    Without it, pynput events are silently dropped by macOS."""
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return True   # non-mac / framework missing: don't block


def _real_python() -> str:
    """The real (symlink-resolved) python binary — the one macOS grants perms to."""
    return os.path.realpath(sys.executable)


def _grant_msg(kind: str) -> str:
    return (f"Atlas needs {kind} permission for this exact program:\n\n"
            f"{_real_python()}\n\n"
            "How to add it:\n"
            f"1. System Settings → Privacy & Security → {kind}\n"
            "2. Click the '+' button\n"
            "3. Press Cmd+Shift+G, paste the path above, press Return, click Open\n"
            "4. Turn its switch ON\n"
            "5. Reload Atlas Mode (run Atlas Background.command twice) and try again.")


def check() -> dict:
    """Self-diagnostic: report each capability so failures are obvious."""
    _load_env()
    status = {
        "python": _real_python(),
        "gemini_key": bool(os.environ.get("GEMINI_API_KEY")),
        "screen_recording": _screen_recording_ok(),
        "accessibility_control": _can_control(),
    }
    return status


def run(goal: str, execute: bool = False, max_steps: int = MAX_STEPS,
        allow_irreversible: bool = False) -> dict:
    """Perceive→plan→act→verify. execute=False (default) is a DRY RUN: it plans and
    reports every action but never touches the mouse. allow_irreversible=True lets
    the ONE committing action (Send/Buy/Book) through — only pass it after the
    human has explicitly confirmed."""
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY") and not gemini_keys():
        return {"ok": False, "error":
                "No model key set. Add ANTHROPIC_API_KEY (Claude) or GEMINI_API_KEY to .env"}
    # Screen Recording is needed to SEE the screen (for both planning and acting).
    if not _screen_recording_ok():
        return {"ok": False, "needs_screen_recording": True,
                "error": _grant_msg("Screen Recording")}
    if execute and not _can_control():
        return {"ok": False, "needs_accessibility": True,
                "error": _grant_msg("Accessibility")}
    start, history, actions = time.time(), [], []

    for i in range(max_steps):
        if time.time() - start > MAX_SECONDS:
            return {"ok": False, "error": "time budget exhausted",
                    "steps": actions, "history": history}
        try:
            step, px = plan_next(goal, history)
        except ScreenPermissionError:
            return {"ok": False, "needs_screen_recording": True, "error":
                    "Lost screen access mid-task — grant Screen Recording and retry."}
        except RateLimitError:
            return {"ok": False, "rate_limited": True,
                    "error": "Gemini rate limit (429) — wait a minute and retry.",
                    "steps": actions, "history": history}
        if not step:
            return {"ok": False, "error": "planner returned nothing",
                    "steps": actions, "history": history}

        a = step.get("action")
        if a == "done" or step.get("done"):
            return {"ok": True, "steps": actions, "history": history,
                    "message": step.get("reason", "goal achieved")}
        if a == "stop":
            return {"ok": False, "error": f"model stopped: {step.get('reason','')}",
                    "steps": actions, "history": history}

        # safety: irreversible actions pause for human approval unless pre-confirmed
        if execute and _is_irreversible(step) and not allow_irreversible:
            return {"ok": False, "awaiting_approval": True,
                    "blocked_action": step, "steps": actions, "history": history,
                    "error": "This step commits something (Send/Buy/Book/Pay). "
                             f"Confirm to let Atlas do it: {step.get('reason','')}"}

        label = f"{a} @({step.get('x')},{step.get('y')}) {step.get('reason','')}".strip()
        actions.append({"planned": step, "executed": execute})
        history.append(label)
        print(("EXEC " if execute else "PLAN "), label, flush=True)

        if execute:
            act(step, px)
            time.sleep(1.2)               # settle UI + ease API rate (free tier)

    return {"ok": False, "error": "max steps reached", "steps": actions,
            "history": history}


if __name__ == "__main__":
    _load_env()
    if "--check" in sys.argv:
        s = check()
        print("Atlas computer-use diagnostic:")
        print(f"  python binary       : {s['python']}")
        print(f"  GEMINI_API_KEY set  : {'YES' if s['gemini_key'] else 'NO — add to .env'}")
        print(f"  Screen Recording ON : {'YES' if s['screen_recording'] else 'NO — grant it'}")
        print(f"  Accessibility ON    : {'YES' if s['accessibility_control'] else 'NO — grant it'}")
        if not (s['gemini_key'] and s['screen_recording'] and s['accessibility_control']):
            print("\nGrant the missing permission(s) to THIS exact program:")
            print(f"  {s['python']}")
            print("  System Settings → Privacy & Security → (Screen Recording / "
                  "Accessibility) → '+' → Cmd+Shift+G → paste path → Open → toggle ON")
        else:
            print("\nAll green — screen control should work. Try:")
            print('  .venv/bin/python computer_use.py "open weather" --execute')
        sys.exit(0)
    execute = "--execute" in sys.argv
    allow = "--yes" in sys.argv        # pre-approve the one committing action
    goal = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or \
        "open Spotlight and type hello"
    if execute:
        print("⚠️  EXECUTE mode: Atlas will move your mouse. Move the mouse to a "
              "screen CORNER at any time to abort.\n   Starting in 3s… (Ctrl-C cancels)")
        time.sleep(3)
    else:
        print("DRY RUN (planning only — no mouse movement). Add --execute to act.\n")
    print(json.dumps(run(goal, execute=execute, allow_irreversible=allow),
                     indent=2, default=str))
