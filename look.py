"""Atlas point-and-look v2 — NO SCREENSHOTS, ever.

Tracks your mouse and reads what's under the cursor through the macOS
Accessibility API — the same text metadata screen readers use (element type,
label, visible text). Zero pixels are captured; nothing is stored.

Hover over anything and press:
  F8  → say what's under the cursor
  F9  → describe it, then type a task about it (routed to the sub-agent crew)
  Esc → quit

Usage:
  python3 look.py             # one-shot: 3s to position the mouse, then describe
  python3 look.py --watch     # hotkey mode (F8/F9/Esc)
  python3 look.py --follow    # continuous: auto-describe whatever you rest the mouse on

Requirements (macOS):
  pip install pynput pyobjc-core pyobjc-framework-ApplicationServices pyobjc-framework-Cocoa
  System Settings → Privacy & Security → Accessibility: allow your terminal
  (No Screen Recording permission needed — nothing is captured.)

Privacy: element text stays on your machine. Only if YOU give a task (F9/--task)
is that text sent to Gemini with your own key as context for the crew.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
DWELL_S = 0.8          # --follow: how long the mouse must rest before describing
DWELL_RADIUS = 8       # pixels of jitter still counting as "resting"


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def cursor_pos() -> tuple[int, int]:
    from pynput.mouse import Controller
    x, y = Controller().position
    return int(x), int(y)


def _ax_attr(element, name):
    from ApplicationServices import AXUIElementCopyAttributeValue
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def element_under_cursor(x: int, y: int) -> dict | None:
    """Read the UI element at (x, y) via Accessibility — text metadata only."""
    from ApplicationServices import (AXUIElementCreateSystemWide,
                                     AXUIElementCopyElementAtPosition)
    err, elem = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), x, y, None)
    if err != 0 or elem is None:
        return None
    info = {"role": _ax_attr(elem, "AXRole"),
            "subrole": _ax_attr(elem, "AXSubrole"),
            "title": _ax_attr(elem, "AXTitle"),
            "value": _ax_attr(elem, "AXValue"),
            "description": _ax_attr(elem, "AXDescription"),
            "help": _ax_attr(elem, "AXHelp")}
    # climb to the window for context
    parent, window_title = elem, None
    for _ in range(12):
        parent = _ax_attr(parent, "AXParent")
        if parent is None:
            break
        if _ax_attr(parent, "AXRole") == "AXWindow":
            window_title = _ax_attr(parent, "AXTitle")
            break
    info["window"] = window_title
    try:
        from AppKit import NSWorkspace
        info["app"] = NSWorkspace.sharedWorkspace().frontmostApplication().localizedName()
    except Exception:
        info["app"] = None
    return info


ROLE_NAMES = {"AXButton": "button", "AXLink": "link", "AXStaticText": "text",
              "AXTextField": "text field", "AXTextArea": "text area",
              "AXImage": "image", "AXMenuItem": "menu item", "AXCell": "cell",
              "AXCheckBox": "checkbox", "AXRadioButton": "radio button",
              "AXPopUpButton": "dropdown", "AXTabGroup": "tab group",
              "AXWebArea": "web page area", "AXGroup": "group"}


def humanize(info: dict) -> str:
    """Local, no-API description of the element."""
    role = ROLE_NAMES.get(str(info.get("role")), str(info.get("role") or "element"))
    label = next((str(v).strip() for v in
                  (info.get("title"), info.get("description"), info.get("value"),
                   info.get("help")) if v and str(v).strip()), None)
    where = " · ".join(str(p) for p in (info.get("app"), info.get("window")) if p)
    text = f"{role.capitalize()}"
    if label:
        clip = label if len(label) <= 160 else label[:157] + "..."
        text += f": “{clip}”"
    if where:
        text += f"  ({where})"
    return text


def notify(text: str):
    print(f"\n👁  {text}\n")
    try:
        safe = text.replace('"', "'")[:180]
        subprocess.run(["osascript", "-e",
                        f'display notification "{safe}" with title "Atlas sees:"'],
                       check=False)
    except Exception:
        pass


def look(task: str | None = None) -> str:
    x, y = cursor_pos()
    info = element_under_cursor(x, y)
    what = humanize(info) if info else \
        "Nothing readable under the cursor (app may not expose accessibility info)."
    notify(what)
    if task and info:
        from subagents import Orchestrator
        context = json.dumps({k: str(v)[:300] for k, v in info.items() if v})
        out = Orchestrator(ROOT).run(
            f"{task} — context, the element under the user's mouse: {context}")
        print(out["summary"])
    return what


def look_then_ask():
    what = look()
    task = input("Task for the crew about this (Enter to skip): ").strip()
    if task:
        from subagents import Orchestrator
        out = Orchestrator(ROOT).run(f"{task} — screen context: {what}")
        print(out["summary"])


def follow():
    """Continuous mode: describe whatever the mouse rests on. All local, no API."""
    print("Atlas is following your mouse (no screenshots — accessibility only). Ctrl+C to stop.")
    last_xy, rest_since, described = None, time.time(), None
    while True:
        x, y = cursor_pos()
        if last_xy and abs(x - last_xy[0]) <= DWELL_RADIUS and abs(y - last_xy[1]) <= DWELL_RADIUS:
            if time.time() - rest_since >= DWELL_S and described != (x // 20, y // 20):
                info = element_under_cursor(x, y)
                if info:
                    notify(humanize(info))
                described = (x // 20, y // 20)
        else:
            rest_since = time.time()
        last_xy = (x, y)
        time.sleep(0.15)


def watch():
    from pynput import keyboard
    print("Atlas is watching (no screenshots). F8 = what is this? · "
          "F9 = describe + do a task · Esc = quit")
    with keyboard.GlobalHotKeys({"<f8>": look, "<f9>": look_then_ask,
                                 "<esc>": lambda: False}) as h:
        h.join()


if __name__ == "__main__":
    _load_env()
    if "--watch" in sys.argv:
        watch()
    elif "--follow" in sys.argv:
        follow()
    elif "--task" in sys.argv:
        look(task=sys.argv[sys.argv.index("--task") + 1])
    else:
        print("Position your mouse over the target... reading in 3s")
        time.sleep(3)
        look()
