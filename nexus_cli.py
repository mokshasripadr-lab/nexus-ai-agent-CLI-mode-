#!/usr/bin/env python3
"""Nexus CLI — a Claude Code-style terminal agent for Nexus AI.

A cozy REPL with a cute mascot, slash commands, engine picker, an autonomous
crew (/do), a cron scheduler (/cron), and your choice of top-tier LLM
(Gemini 2.5 Pro, Claude Sonnet, or GPT-4o). Core chat/crew is pure stdlib.

On first run it asks which model you want and for that provider's API key,
then saves it privately to ~/.nexus/config.json (chmod 600). Keys are never
bundled with the code.

Run:
  python3 nexus_cli.py           # interactive chat
  python3 nexus_cli.py "prompt"  # one-shot answer
"""
from __future__ import annotations
import json, os, sys, threading, time, urllib.request, urllib.error, urllib.parse, itertools
from pathlib import Path

ROOT = Path(__file__).parent
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Portable config in the USER's home dir — works on any machine, no shared keys.
CONFIG_DIR = Path.home() / ".nexus"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Top-tier LLM providers the user can choose from.
PROVIDERS = {
    "1": {"id": "google",    "name": "Google Gemini 2.5 Pro", "model": "gemini-2.5-pro",
          "hint": "AIza…  ·  free key: aistudio.google.com/apikey"},
    "2": {"id": "anthropic", "name": "Anthropic Claude (Sonnet)", "model": "claude-3-5-sonnet-latest",
          "hint": "sk-ant-…  ·  console.anthropic.com"},
    "3": {"id": "openai",    "name": "OpenAI GPT-4o",       "model": "gpt-4o",
          "hint": "sk-…  ·  platform.openai.com"},
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try: return json.loads(CONFIG_FILE.read_text())
        except Exception: pass
    return {"provider": "google", "keys": []}


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    try: os.chmod(CONFIG_FILE, 0o600)
    except Exception: pass

# ---- ANSI colors -----------------------------------------------------------
class C:
    r = "\033[0m"; b = "\033[1m"; dim = "\033[2m"
    orange = "\033[38;5;209m"; clay = "\033[38;5;173m"; cream = "\033[38;5;223m"
    grey = "\033[38;5;245m"; green = "\033[38;5;114m"; red = "\033[38;5;203m"
    box = "\033[38;5;173m"


def load_keys() -> list[str]:
    cfg = load_config()
    keys = list(cfg.get("keys", []))
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"):
        v = os.environ.get(name)
        if v and v not in keys:
            keys.append(v)
    env = ROOT / ".env"
    if not keys and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("GEMINI_API_KEY") and "=" in line:
                v = line.split("=", 1)[1].strip()
                if v and v not in keys: keys.append(v)
    return keys


def first_run_setup() -> list[str]:
    """Let the user pick a top-tier LLM and paste that provider's key."""
    print(f"{C.cream}{C.b}Welcome to Nexus CLI!{C.r}  Pick your AI model:\n")
    for k, p in PROVIDERS.items():
        print(f"  {C.b}{k}{C.r}  {p['name']}  {C.grey}{p['hint']}{C.r}")
    choice = input(f"\n{C.orange}choose 1-3 ›{C.r} ").strip() or "1"
    prov = PROVIDERS.get(choice, PROVIDERS["1"])
    key = input(f"{C.orange}Paste your {prov['name']} API key ›{C.r} ").strip()
    if not key:
        print(f"{C.dim}No key entered — add one later with /model.{C.r}\n")
        return []
    save_config({"provider": prov["id"], "model": prov["model"], "keys": [key]})
    print(f"{C.green}✓ {prov['name']} set as your main model. Saved to ~/.nexus.{C.r}\n")
    return [key]


def banner():
    cfg = load_config()
    label = cfg.get("model") or "top LLMs"
    print(
        f"{C.orange}      \\  |  /        {C.cream}{C.b}Nexus CLI{C.r}\n"
        f"{C.orange}     .-'''-.        {C.grey}your terminal agent{C.r}\n"
        f"{C.orange}    ( ^   ^ )       {C.grey}powered by {label}{C.r}\n"
        f"{C.orange}     '-. .-'        {C.dim}type /help for commands{C.r}\n"
        f"{C.orange}      /  |  \\{C.r}\n"
    )


def spinner(stop: threading.Event):
    for ch in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
        if stop.is_set():
            break
        sys.stdout.write(f"\r{C.clay}{ch}{C.r} {C.dim}thinking…{C.r}")
        sys.stdout.flush()
        time.sleep(0.08)
    sys.stdout.write("\r" + " " * 24 + "\r")
    sys.stdout.flush()


# ---- Engines ---------------------------------------------------------------
ENGINES = {
    "1": ("Swift", "instant answers", "#FBBC05",
          "You are Swift, the instant-answer engine. Answer in the fewest words that "
          "fully help — one line is great. No preamble."),
    "2": ("NextAgent", "code-first", "#5CCFB0",
          "You are NextAgent, a code-first engine. Reply with: (1) working code in a "
          "fenced block, (2) ONE line explaining the approach, (3) 'Run:' with the "
          "exact command. Minimal prose."),
    "3": ("Atlas Deep", "multi-step reasoning", "#A78BFA",
          "You are Atlas Deep, a careful multi-step reasoning engine. Think step by "
          "step, be rigorous, cite concrete facts, and if you can't verify something "
          "say so honestly. End with a short synthesis."),
}
current = {"key": "1"}   # selected engine


def engine_menu():
    print(f"{C.cream}{C.b}Pick an engine:{C.r}")
    for k, (name, tag, col, _) in ENGINES.items():
        mark = f"{C.orange}●{C.r}" if k == current["key"] else f"{C.dim}○{C.r}"
        print(f"  {mark} {C.b}{k}{C.r}  {name} {C.grey}— {tag}{C.r}")
    choice = input(f"{C.orange}choose 1-3 (Enter = keep) ›{C.r} ").strip()
    if choice in ENGINES:
        current["key"] = choice
    name = ENGINES[current["key"]][0]
    print(f"{C.green}→ using {name}{C.r}\n")


# ---- Actions: open apps / sites from the CLI -------------------------------
SITES = {
    "email": "https://mail.google.com", "gmail": "https://mail.google.com",
    "docs": "https://docs.google.com", "google docs": "https://docs.google.com",
    "sheets": "https://sheets.google.com", "slides": "https://slides.google.com",
    "drive": "https://drive.google.com", "calendar": "https://calendar.google.com",
    "youtube": "https://youtube.com", "github": "https://github.com",
    "maps": "https://maps.google.com", "gemini": "https://gemini.google.com",
    "chatgpt": "https://chat.openai.com", "translate": "https://translate.google.com",
}


def open_url(url: str):
    import webbrowser
    webbrowser.open(url)


def do_action(text: str) -> bool:
    """Handle 'open X' and 'search X' locally by launching the browser/app.
    Returns True if it handled the command."""
    t = text.strip()
    low = t.lower()

    if low.startswith("open "):
        target = t[5:].strip().rstrip("?.")
        key = target.lower()
        if key in SITES:
            open_url(SITES[key]); print(f"{C.green}✦ opening {target}…{C.r}\n"); return True
        if key.startswith("http://") or key.startswith("https://") or "." in key.split()[0]:
            url = key if key.startswith("http") else "https://" + key.split()[0]
            open_url(url); print(f"{C.green}✦ opening {url}…{C.r}\n"); return True
        # try to open a local app (mac/linux/win)
        import platform
        try:
            if platform.system() == "Darwin":
                subprocess.run(["open", "-a", target], check=True)
            elif platform.system() == "Windows":
                subprocess.run(["cmd", "/c", "start", "", target], check=True)
            else:
                subprocess.run(["xdg-open", target], check=True)
            print(f"{C.green}✦ opening {target}…{C.r}\n"); return True
        except Exception:
            # not a known app → search the web for it
            open_url("https://www.google.com/search?q=" + urllib.parse.quote(target))
            print(f"{C.green}✦ searching the web for {target}…{C.r}\n"); return True

    if low.startswith("search "):
        q = t[7:].strip()
        open_url("https://www.google.com/search?q=" + urllib.parse.quote(q))
        print(f"{C.green}✦ searching: {q}{C.r}\n"); return True

    return False


# ---- Autonomous crew + cron (needs the full atlas-agent folder) ------------
def run_crew(task: str):
    """Run the multi-agent crew: it decomposes the goal, researches, executes,
    schedules, and prepares bookings (booking stops for your approval)."""
    try:
        sys.path.insert(0, str(ROOT))
        from subagents import Orchestrator
    except Exception:
        print(f"{C.red}Autonomous mode needs the full atlas-agent folder "
              f"(subagents.py, agent.py, tools.py). Chat still works here.{C.r}\n")
        return
    print(f"{C.dim}running the crew…{C.r}")
    try:
        out = Orchestrator(ROOT).run(task)
        print(f"{C.clay}✦ result{C.r}\n{render(out.get('summary', ''))}\n")
    except Exception as e:
        print(f"{C.red}crew error: {e}{C.r}\n")


def add_cron(task: str):
    """Schedule a task and install a system cron entry so it runs automatically."""
    when = input(f"{C.orange}when? (e.g. 'every day 9am', '2027-01-01 08:00') ›{C.r} ").strip() or "daily"
    sched_file = ROOT / "state" / "schedule.json"
    sched_file.parent.mkdir(exist_ok=True)
    sched = json.loads(sched_file.read_text()) if sched_file.exists() else []
    sched.append({"id": f"S-{len(sched)+1:03d}", "when": when, "goal": task, "status": "scheduled"})
    sched_file.write_text(json.dumps(sched, indent=2))
    print(f"{C.green}✦ scheduled: {task}  ({when}){C.r}")
    # install a crontab entry that runs the scheduler every 5 minutes (once)
    try:
        line = f"*/5 * * * * cd {ROOT} && /usr/bin/env python3 run_scheduled.py >> {ROOT}/logs/cron.log 2>&1"
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        if "run_scheduled.py" not in existing:
            subprocess.run(["bash", "-c", f'(crontab -l 2>/dev/null; echo "{line}") | crontab -'], check=True)
            print(f"{C.dim}installed background scheduler (runs every 5 min while your Mac is on).{C.r}\n")
        else:
            print(f"{C.dim}background scheduler already installed.{C.r}\n")
    except Exception:
        print(f"{C.dim}(couldn't auto-install cron — the task is saved; run 'python3 run_scheduled.py' to execute due tasks.){C.r}\n")


def ask(prompt: str, history: list[dict], keys: list[str]) -> str:
    """Route the turn to whichever top model the user picked in setup."""
    SYSTEM = ENGINES[current["key"]][3]
    cfg = load_config()
    provider = cfg.get("provider", "google")
    model = cfg.get("model") or MODEL
    if provider == "anthropic":
        return _call_anthropic(SYSTEM, prompt, history, keys, model)
    if provider == "openai":
        return _call_openai(SYSTEM, prompt, history, keys, model)
    return _call_gemini(SYSTEM, prompt, history, keys, model)


def _call_gemini(system, prompt, history, keys, model):
    contents = []
    for h in history[-8:]:
        role = "model" if h["role"] in ("model", "assistant") else "user"
        contents.append({"role": role, "parts": [{"text": h["text"]}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.4},
    }).encode()
    for ki, key in enumerate(keys):
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 403) and ki < len(keys) - 1:
                continue
            if e.code == 429:
                return f"{C.red}Rate limit hit on all keys — wait ~1 min or add more keys with /key.{C.r}"
            return f"{C.red}Gemini error {e.code}. Check your key with /model.{C.r}"
        except Exception as e:
            return f"{C.red}Error: {e}{C.r}"
    return f"{C.red}No key found. Run /model to set one up.{C.r}"


def _call_anthropic(system, prompt, history, keys, model):
    messages = []
    for h in history[-8:]:
        role = "assistant" if h["role"] in ("model", "assistant") else "user"
        messages.append({"role": role, "content": h["text"]})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "temperature": 0.4,
        "system": system,
        "messages": messages,
    }).encode()
    for ki, key in enumerate(keys):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"Content-Type": "application/json",
                     "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            return "".join(b.get("text", "") for b in data.get("content", []))
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and ki < len(keys) - 1:
                continue
            return f"{C.red}Claude error {e.code}. Check your key with /model.{C.r}"
        except Exception as e:
            return f"{C.red}Error: {e}{C.r}"
    return f"{C.red}No key found. Run /model to set one up.{C.r}"


def _call_openai(system, prompt, history, keys, model):
    messages = [{"role": "system", "content": system}]
    for h in history[-8:]:
        role = "assistant" if h["role"] in ("model", "assistant") else "user"
        messages.append({"role": role, "content": h["text"]})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps({
        "model": model,
        "temperature": 0.4,
        "messages": messages,
    }).encode()
    for ki, key in enumerate(keys):
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and ki < len(keys) - 1:
                continue
            return f"{C.red}OpenAI error {e.code}. Check your key with /model.{C.r}"
        except Exception as e:
            return f"{C.red}Error: {e}{C.r}"
    return f"{C.red}No key found. Run /model to set one up.{C.r}"


def render(text: str) -> str:
    """Light markdown coloring for the terminal (headers, code fences, bullets)."""
    out, in_code = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(f"{C.dim}{line}{C.r}")
        elif in_code:
            out.append(f"{C.green}{line}{C.r}")
        elif line.startswith("#"):
            out.append(f"{C.b}{C.cream}{line.lstrip('# ')}{C.r}")
        elif line.strip().startswith(("- ", "* ")):
            out.append(f"  {C.clay}•{C.r} {line.strip()[2:]}")
        else:
            out.append(line)
    return "\n".join(out)


HELP = f"""{C.cream}{C.b}Commands{C.r}
  {C.clay}/help{C.r}    show this help
  {C.clay}/clear{C.r}   clear the conversation
  {C.clay}/keys{C.r}    how many API keys are loaded
  {C.clay}/key{C.r}     add another API key (same provider)
  {C.clay}/model{C.r}   switch LLM provider (Gemini / Claude / GPT)
  {C.clay}/engine{C.r}  switch engine (Swift / NextAgent / Atlas Deep)
  {C.clay}/do{C.r}      autonomous multi-step task (research, book, etc.)
  {C.clay}/cron{C.r}    schedule a task to run automatically
  {C.clay}/exit{C.r}    quit (or Ctrl-C)
Also: 'open email', 'open docs', 'search <thing>' work directly."""


def chat_loop():
    banner()
    keys = load_keys()
    if not keys:
        keys = first_run_setup()   # portable: prompts + saves to ~/.nexus
    engine_menu()                  # show engines, pick one
    history: list[dict] = []
    while True:
        try:
            eng = ENGINES[current["key"]][0]
            user = input(f"{C.grey}[{eng}]{C.r} {C.orange}{C.b}you ›{C.r} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C.dim}bye 👋{C.r}"); return
        if not user:
            continue
        if user in ("/exit", "/quit"):
            print(f"{C.dim}bye 👋{C.r}"); return
        if user == "/help":
            print(HELP + "\n"); continue
        if user == "/clear":
            history.clear(); print(f"{C.dim}conversation cleared.{C.r}\n"); continue
        if user == "/keys":
            print(f"{C.grey}{len(keys)} key(s) loaded.{C.r}\n"); continue
        if user == "/key":
            cfg = load_config()
            nk = input(f"{C.orange}Paste another {cfg.get('provider','api')} key ›{C.r} ").strip()
            if nk:
                keys = list(dict.fromkeys(keys + [nk]))
                cfg["keys"] = keys; save_config(cfg)
                print(f"{C.green}Key added — {len(keys)} loaded.{C.r}\n")
            continue
        if user == "/model":
            keys = first_run_setup(); history.clear(); continue
        if user == "/engine":
            engine_menu(); history.clear(); continue
        if user.lower().startswith("/do "):
            run_crew(user[4:].strip()); continue          # autonomous complex task
        if user.lower().startswith("/cron "):
            add_cron(user[6:].strip()); continue          # schedule + auto-run

        # "open email", "open docs", "search …" → do it directly (no API call)
        if do_action(user):
            continue

        stop = threading.Event()
        t = threading.Thread(target=spinner, args=(stop,)); t.start()
        answer = ask(user, history, keys)
        stop.set(); t.join()

        history.append({"role": "user", "text": user})
        history.append({"role": "model", "text": answer})
        print(f"{C.clay}✦ nexus{C.r}\n{render(answer)}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ks = load_keys()
        print(render(ask(" ".join(sys.argv[1:]), [], ks)))
    else:
        chat_loop()
