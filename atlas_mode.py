"""Atlas Mode — DOUBLE-CLICK anything on screen to have Atlas say what it is
and ask what to do about it. No screenshots (Accessibility text only).

Start:  double-click 'Atlas Mode.command'  (or: python3 atlas_mode.py)
Stop:   close the small Terminal window it opens, or press Ctrl-C in it.

IMPORTANT (macOS): the listener runs ATTACHED to Terminal so it keeps
Terminal's Accessibility permission. That's why the window stays open while
Atlas Mode is on — closing it turns Atlas Mode off.

While ON:
  • Double-click anything → notification says what it is
    → a dialog asks "what should Atlas do about it?" (Cancel = nothing)
  • The task + element context go to the sub-agent crew (subagents.py)
"""
from __future__ import annotations
import json, subprocess, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).parent
LOG_FILE = ROOT / "logs" / "atlas_mode.log"
DOUBLE_CLICK_S = 0.45
DOUBLE_CLICK_PX = 6
COOLDOWN_S = 2.0

sys.path.insert(0, str(ROOT))


def _notify(text: str):
    print(text, flush=True)
    safe = text.replace('"', "'")[:180]
    subprocess.run(["osascript", "-e",
                    f'display notification "{safe}" with title "Atlas Mode"'],
                   check=False)


def _dialog(msg: str):
    safe = msg.replace('"', "'").replace("\\", "")[:480]
    subprocess.run(["osascript", "-e",
                    f'display dialog "{safe}" with title "Atlas Mode" buttons {{"OK"}} default button 1'],
                   capture_output=True, timeout=120)


def _check_accessibility() -> bool:
    """Return True once trusted. Prompts ONCE, then waits (polls) so KeepAlive
    never restarts us into a loop of repeated permission dialogs."""
    try:
        from ApplicationServices import (AXIsProcessTrusted,
                                         AXIsProcessTrustedWithOptions)
    except Exception:
        return True   # frameworks missing is handled elsewhere
    if AXIsProcessTrusted():
        return True
    try:
        AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
    except Exception:
        pass
    _dialog("Atlas Mode needs Accessibility permission (one-time).\n\n"
            "In the prompt that appeared, click 'Open System Settings' and turn ON "
            "the 'python3' entry under Privacy & Security → Accessibility.\n\n"
            "Then just start using it — Atlas Mode is already waiting and will begin "
            "the moment you flip that switch. (You won't see this message again.)")
    # wait up to 30 minutes, checking every second — no restart, no repeat dialog
    for _ in range(1800):
        time.sleep(1)
        try:
            if AXIsProcessTrusted():
                _notify("Atlas Mode is ready — double-click anything on screen.")
                return True
        except Exception:
            pass
    return False


def _gemini_keys() -> list[str]:
    import os as _os
    keys: list[str] = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
        v = _os.environ.get(name)
        if v and v.strip():
            keys.append(v.strip())
    for v in _os.environ.get("GEMINI_API_KEYS", "").split(","):
        if v.strip() and v.strip() not in keys:
            keys.append(v.strip())
    return keys


def _ask_gemini(prompt: str) -> str | None:
    """Direct one-shot Gemini answer (no tools). Rotates through all keys on
    quota/auth failure. Returns None if no key or all fail."""
    import json as _json, os as _os, urllib.request, urllib.error
    keys = _gemini_keys()
    if not keys:
        return None
    model = _os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    body = _json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.2}}).encode()
    for ki, key in enumerate(keys):
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = _json.loads(r.read())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            if e.code in (429, 403) and ki < len(keys) - 1:
                continue                     # quota/rejected → next key
            return None
        except Exception:
            return None
    return None


def _show_answer(text: str):
    """Show the answer as a dialog right where the user is (plus a notification)."""
    safe = text.replace('"', "'").replace("\\", "")
    body = safe if len(safe) <= 900 else safe[:897] + "..."
    subprocess.run(["osascript", "-e",
                    f'display dialog "{body}" with title "Atlas" '
                    f'buttons {{"Copy","OK"}} default button "OK"'],
                   capture_output=True, timeout=120)
    _notify(text.splitlines()[0][:170] if text else "done")


ACTION_WORDS = ("book", "schedule", "remind", "write", "save", "create",
                "search for", "find flights", "send", "download", "buy", "reserve")
QUESTION_WORDS = ("what", "who", "where", "when", "why", "how", "explain",
                  "summarize", "summarise", "describe", "tell", "is this", "meaning")
# On-screen control intents → drive the mouse/keyboard via the vision loop.
CONTROL_WORDS = ("open", "click", "press", "type", "scroll", "go to", "navigate",
                 "switch to", "select", "close", "do it on screen", "control",
                 "launch", "start", "play", "pause")


def _confirm(msg: str) -> bool:
    safe = msg.replace('"', "'").replace("\\", "")[:300]
    out = subprocess.run(["osascript", "-e",
                          f'display dialog "{safe}" with title "Atlas — control screen?" '
                          f'buttons {{"Cancel","Allow"}} default button "Allow" '
                          f'giving up after 30'],
                         capture_output=True, text=True, timeout=45)
    return out.returncode == 0 and "Allow" in out.stdout


def _run_computer_use(task: str):
    """Drive the screen to accomplish `task`, in the background (no Terminal)."""
    if not _confirm(f"Atlas will control the screen to:\n\n{task}\n\n"
                    "It stops before anything irreversible (Buy/Send/Delete). Allow?"):
        _notify("Cancelled — screen not touched.")
        return
    _notify(f"Doing it on screen: {task}")
    try:
        import importlib
        sys.path.insert(0, str(ROOT))
        cu = importlib.import_module("computer_use")
        out = cu.run(task, execute=True)
    except ModuleNotFoundError:
        _show_answer("Screen control isn't loaded yet. Reload Atlas Mode (run the "
                     "Atlas Background.command twice), then try again.")
        return
    except Exception as e:
        _show_answer(f"Couldn't run screen control: {type(e).__name__}: {e}")
        return
    if out.get("ok"):
        _show_answer(f"Done: {out.get('message', 'completed on screen')}")
    elif out.get("needs_screen_recording"):
        _show_answer(out.get("error", "Grant Screen Recording permission and retry."))
    elif out.get("awaiting_approval"):
        _show_answer("Stopped before an irreversible step (needs your approval). "
                     f"Blocked: {out.get('blocked_action', {}).get('reason', '')}")
    elif out.get("rate_limited"):
        _show_answer("Gemini is rate-limiting the key (too many requests). Wait a "
                     "minute and try again, or use a key with a higher quota.")
    else:
        _show_answer(f"Couldn't finish: {out.get('error', 'unknown')}")


def _dialog_ask(seen: str) -> str | None:
    safe = seen.replace('"', "'").replace("\\", "")[:120]
    script = (f'display dialog "I see {safe}.\\n\\n'
              f'Should I perform a task? (type it below, or Cancel)" '
              f'default answer "" with title "Atlas" buttons {{"Cancel","Do it"}} '
              f'default button "Do it" giving up after 45')
    try:
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        for part in out.stdout.split(","):
            if "text returned" in part:
                return part.split(":", 1)[1].strip() or None
    except Exception:
        pass
    return None


def _handle_double_click(x: int, y: int):
    from look import element_under_cursor, notify
    info = element_under_cursor(x, y)
    # Show ONLY the app name to the user; keep the full element as hidden context.
    app_name = (info or {}).get("app") or "the screen"
    notify(f"I see {app_name}")
    task = _dialog_ask(app_name)
    if not task:
        return
    context = json.dumps({k: str(v)[:300] for k, v in (info or {}).items() if v})
    tl = task.lower()
    is_control = any(tl.startswith(w) or f" {w}" in f" {tl}" for w in CONTROL_WORDS)
    is_action = any(w in tl for w in ACTION_WORDS)
    is_question = any(tl.startswith(w) or w in tl for w in QUESTION_WORDS)

    # On-screen control ("open X", "click Y", "type Z") → the vision act loop.
    if is_control:
        _run_computer_use(task)
        return

    # Questions (and anything not clearly an action) → answer directly with Gemini,
    # shown in a dialog right where the user is. No web search needed.
    if is_question or not is_action:
        _notify("Thinking…")
        ans = _ask_gemini(
            f"The user is looking at this on-screen UI element: {context}\n"
            f"The user asks: \"{task}\"\n"
            f"Answer helpfully and concisely (a few sentences). If it's a UI element, "
            f"say what it is and what it does.")
        if ans:
            _show_answer(ans)
            print(ans, flush=True)
            return
        # if the direct answer failed, fall through to the crew

    # Actions (book/schedule/write/search…) → run the sub-agent crew, show the result.
    _notify(f"On it: {task}")
    from subagents import Orchestrator
    out = Orchestrator(ROOT).run(
        f"{task} — context, the element the user double-clicked: {context}")
    _show_answer(out["summary"] or ("Done." if out["success"] else "Couldn't complete that."))
    print(out["summary"], flush=True)


def run_listener():
    from pynput import mouse
    last = {"t": 0.0, "x": 0, "y": 0}
    busy = {"until": 0.0}
    print("\n" + "=" * 52 +
          "\n  ATLAS MODE IS ON — double-click anything on screen."
          "\n  Close this window (or press Ctrl-C) to turn it OFF."
          "\n" + "=" * 52 + "\n", flush=True)
    _notify("Atlas Mode ON — double-click anything to ask Atlas about it.")

    def on_click(x, y, button, pressed):
        if not pressed or button != mouse.Button.left:
            return
        now = time.time()
        if now < busy["until"]:
            return
        if (now - last["t"] <= DOUBLE_CLICK_S and
                abs(x - last["x"]) <= DOUBLE_CLICK_PX and
                abs(y - last["y"]) <= DOUBLE_CLICK_PX):
            busy["until"] = now + COOLDOWN_S
            threading.Thread(target=_handle_double_click,
                             args=(int(x), int(y)), daemon=True).start()
            last["t"] = 0.0
        else:
            last.update(t=now, x=x, y=y)

    try:
        with mouse.Listener(on_click=on_click) as listener:
            listener.join()
    except KeyboardInterrupt:
        pass
    finally:
        _notify("Atlas Mode OFF.")


if __name__ == "__main__":
    from look import _load_env
    _load_env()
    LOG_FILE.parent.mkdir(exist_ok=True)
    # deps are provided by the .venv the launcher sets up; verify import cleanly
    try:
        import pynput  # noqa: F401
        from ApplicationServices import AXIsProcessTrusted  # noqa: F401
    except Exception as e:
        _dialog(f"Atlas Mode components aren't installed.\n\n{type(e).__name__}: {e}\n\n"
                "Re-run 'Atlas Mode.command' — it installs them into a private .venv.")
        sys.exit(1)
    if not _check_accessibility():
        # Under launchd (KeepAlive) this exit makes the service retry shortly —
        # so once you approve the permission it starts on its own, no Terminal.
        time.sleep(3)
        sys.exit(1)
    run_listener()      # headless under launchd, or attached if launched from a window
