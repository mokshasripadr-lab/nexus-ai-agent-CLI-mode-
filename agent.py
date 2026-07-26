"""Atlas — autonomous agent core: explicit state machine per PRD §4.

Planner: rule-based "mock model" by default so everything runs offline.
If ANTHROPIC_API_KEY is set and `anthropic` is installed, ClaudePlanner is used.
"""
from __future__ import annotations
import json, os, re, time
from enum import Enum
from pathlib import Path

from memory import Memory
from tools import ToolRegistry, PolicyViolation, ApprovalRequired

# minimal YAML reader (flat + one nesting level) to avoid a hard pyyaml dependency
def load_config(path="config.yaml") -> dict:
    try:
        import yaml
        return yaml.safe_load(Path(path).read_text())
    except ImportError:
        cfg, section = {}, None
        for raw in Path(path).read_text().splitlines():
            line = raw.split("#")[0].rstrip()
            if not line.strip() or ":" not in line:
                continue
            key, _, val = line.partition(":")
            indent, key, val = len(key) - len(key.lstrip()), key.strip(), val.strip()
            if indent == 0:
                if val:
                    cfg[key] = _coerce(val)
                else:
                    cfg[key] = {}
                    section = cfg[key]
            elif section is not None:
                if val.startswith("{"):
                    inner = dict(kv.split(":") for kv in val.strip("{} ").split(",") if ":" in kv)
                    section[key] = {k.strip(): _coerce(v.strip()) for k, v in inner.items()}
                elif val:
                    section[key] = _coerce(val)
        return cfg

def _coerce(v: str):
    try:
        return int(v)
    except ValueError:
        return v

class State(str, Enum):
    IDLE = "IDLE"; PLANNING = "PLANNING"; ACTING = "ACTING"; OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"; REFLECTING = "REFLECTING"; DONE = "DONE"; HALTED = "HALTED"


class MockPlanner:
    """Deterministic planner. Deliberately naive where lessons haven't taught it better —
    that's what the improvement loop is for."""

    def plan(self, goal: str, lessons: list[dict]) -> list[dict]:
        g = goal.lower()
        rules = " ".join(l["rule"].lower() for l in lessons)

        m = re.search(r"calculate\s+(.+)", g)
        if m:
            return [{"tool": "calculator", "args": {"expression": m.group(1).strip()}}]

        m = re.search(r"write (?:a )?file (\S+) with content (.+)", goal, re.I)
        if m:
            return [{"tool": "write_file", "args": {"path": m.group(1), "content": m.group(2)}},
                    {"tool": "read_file", "args": {"path": m.group(1)}}]

        m = re.search(r"read (?:the )?file (\S+)", goal, re.I)
        if m:
            path = m.group(1)
            steps = []
            if "list_dir" in rules or "verify the path exists" in rules:
                # Guard must list the file's PARENT dir and check the basename;
                # listing "." for nested paths falsely reported existing files missing.
                parent, _, name = path.rpartition("/")
                steps.append({"tool": "list_dir", "args": {"path": parent or "."},
                              "guard_for": name or path})   # lesson-driven guard step
            steps.append({"tool": "read_file", "args": {"path": path}})
            return steps

        m = re.search(r"search (?:for )?(.+)", g)
        if m:
            return [{"tool": "web_search", "args": {"query": m.group(1).strip()}}]

        m = re.search(r"count files in (\S+)", g)
        if m:
            path, steps = m.group(1), []
            if path not in (".", "./") and "list_dir" in rules:
                # lesson-driven guard: missing dirs abort cleanly instead of crashing
                parent, _, name = path.rpartition("/")
                steps.append({"tool": "list_dir", "args": {"path": parent or "."},
                              "guard_for": name or path})
            steps.append({"tool": "list_dir", "args": {"path": path}})
            return steps

        m = re.search(r"fetch (https?://\S+)", goal, re.I)
        if m:
            return [{"tool": "web_fetch", "args": {"url": m.group(1)}}]

        m = re.search(r"schedule (?:a )?(?:task )?['\"]?(.+?)['\"]? (?:at|for|on) (.+)", goal, re.I)
        if m:
            return [{"tool": "schedule_task",
                     "args": {"name": m.group(1).strip(), "when": m.group(2).strip(),
                              "goal": m.group(1).strip()}}]

        m = re.search(r"book (.+)", goal, re.I)
        if m:
            return [{"tool": "book", "args": {"request": m.group(1).strip()}}]

        return []  # planner has no strategy → will fail and generate a lesson


class GeminiPlanner:
    """Google Gemini planner. Reads GEMINI_API_KEY (from env or .env).
    Falls back to MockPlanner on any API failure so the agent never stalls."""

    PROMPT = ("You are the planner for an autonomous agent. Allowed tools: "
              "calculator(expression), read_file(path), write_file(path, content), "
              "list_dir(path), web_search(query). "
              "Before read_file, add a list_dir step with an extra key \"guard_for\" set to "
              "the filename, so missing files are handled gracefully. "
              "Apply these learned lessons: {lessons}\n"
              "Goal: {goal}\n"
              "Reply with ONLY a JSON array of steps: [{{\"tool\": ..., \"args\": {{...}}}}]")

    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key                # primary (kept for compatibility)
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.fallback = MockPlanner()

    def _keys(self) -> list[str]:
        keys = [self.api_key] if self.api_key else []
        for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
            v = os.environ.get(name)
            if v and v.strip() and v.strip() not in keys:
                keys.append(v.strip())
        for v in os.environ.get("GEMINI_API_KEYS", "").split(","):
            if v.strip() and v.strip() not in keys:
                keys.append(v.strip())
        return keys

    def _call(self, prompt: str) -> str:
        import urllib.request, urllib.error
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "temperature": 0.0}}).encode()
        keys = self._keys()
        for ki, key in enumerate(keys):       # rotate through keys on quota/auth
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{self.model}:generateContent?key={key}")
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                if e.code in (429, 403) and ki < len(keys) - 1:
                    continue
                raise
        raise RuntimeError("no Gemini keys configured")

    def plan(self, goal: str, lessons: list[dict]) -> list[dict]:
        try:
            text = self._call(self.PROMPT.format(lessons=json.dumps(lessons), goal=goal))
            steps = json.loads(text)
            assert isinstance(steps, list) and all("tool" in s and "args" in s for s in steps)
            return steps
        except Exception:
            return self.fallback.plan(goal, lessons)   # never stall on API failure


class ClaudePlanner:
    """Real-model planner (M2). Same interface as MockPlanner."""
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic()

    def plan(self, goal: str, lessons: list[dict]) -> list[dict]:
        msg = self.client.messages.create(
            model="claude-sonnet-5", max_tokens=1024,
            system=("Plan tool calls for this goal. Allowed tools: calculator(expression), "
                    "read_file(path), write_file(path,content), list_dir(path), "
                    "web_search(query). Apply these learned lessons: "
                    + json.dumps(lessons) +
                    ". Reply ONLY with a JSON list of {tool, args} steps."),
            messages=[{"role": "user", "content": goal}])
        return json.loads(msg.content[0].text)


ROLE_TOOLS = {
    "researcher": ["web_search", "web_fetch", "calculator", "read_file", "write_file", "list_dir"],
    "executor":   ["read_file", "write_file", "list_dir", "run_python", "calculator"],
    "scheduler":  ["schedule_task", "list_dir", "read_file"],
    "booker":     ["web_search", "web_fetch", "book"],
    "generalist": ["read_file", "write_file", "list_dir", "run_python", "calculator",
                   "web_search", "web_fetch", "schedule_task", "book"],
}


class Agent:
    def __init__(self, root: str | Path = ".", role: str | None = None):
        self.root = Path(root)
        self.role = role
        self.cfg = load_config(self.root / "config.yaml")
        if role:                                   # sub-agents get a narrowed allowlist
            keep = set(ROLE_TOOLS.get(role, [])) | {"verify", "finish"}
            self.cfg = {**self.cfg, "allowed_tools": {
                k: v for k, v in self.cfg["allowed_tools"].items() if k in keep}}
        self.memory = Memory(self.root / self.cfg["paths"]["memory"])
        self.tools = ToolRegistry(self.cfg, self.root / self.cfg["paths"]["workspace"],
                                  self.root / self.cfg["paths"]["state"])
        self.logs = self.root / self.cfg["paths"]["logs"]
        self.state_dir = self.root / self.cfg["paths"]["state"]
        self.logs.mkdir(exist_ok=True); self.state_dir.mkdir(exist_ok=True)
        self._load_dotenv()
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                self.planner = ClaudePlanner()
            except Exception:
                self.planner = MockPlanner()
        elif os.environ.get("GEMINI_API_KEY"):
            self.planner = GeminiPlanner(os.environ["GEMINI_API_KEY"])
        else:
            self.planner = MockPlanner()
        self.state = State.IDLE

    # ---- infrastructure ---------------------------------------------------
    def _load_dotenv(self):
        """Load KEY=value pairs from agent-root .env (never committed/shared)."""
        env = self.root / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

    def _log(self, event: str, **data):
        with (self.logs / "agent.log").open("a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "event": event, **data}) + "\n")

    def _transition(self, new: State, **data):
        self._log("transition", frm=self.state.value, to=new.value, **data)  # write-ahead
        self.state = new

    def _checkpoint(self, wm: dict):
        (self.state_dir / "checkpoint.json").write_text(json.dumps(wm, indent=2, default=str))

    # ---- main loop ----------------------------------------------------------
    def run(self, goal: str) -> dict:
        budgets = self.cfg["budgets"]
        start = time.time()
        wm = {"goal": goal, "steps": [], "replans": 0, "verify_retries": 0}  # working memory
        self._transition(State.PLANNING, goal=goal)

        lessons = self.memory.lessons_for(goal) + self.memory.lessons_for("planning")
        plan = self.planner.plan(goal, self.memory.lessons())
        failure_class, error = None, None

        while True:
            # ---- budget enforcement (in code, not prompts) ----
            if len(wm["steps"]) >= budgets["max_steps"]:
                failure_class, error = "budget:max_steps", "step budget exhausted"
                self._transition(State.HALTED, reason=error); break
            if time.time() - start > budgets["max_seconds"]:
                failure_class, error = "budget:max_seconds", "time budget exhausted"
                self._transition(State.HALTED, reason=error); break
            if not plan:
                failure_class, error = "planning:no-strategy", f"no plan for goal: {goal}"
                break

            step = plan.pop(0)
            self._transition(State.ACTING, tool=step["tool"])
            try:
                result = self.tools.call(step["tool"], **step["args"])
                # lesson-driven guard: if a guard step shows the target is missing, stop cleanly
                if step.get("guard_for") and step["guard_for"] not in result:
                    result = f"guard: '{step['guard_for']}' not found; aborting cleanly"
                    plan = []
                    wm["steps"].append({"tool": step["tool"], "ok": True, "result": result})
                    wm["graceful_missing"] = True
                    break
            except ApprovalRequired as e:
                # PRD: halt-and-ask on irreversible ops. Clean stop, not a failure.
                wm["steps"].append({"tool": step["tool"], "ok": True, "result": str(e)})
                wm["awaiting_approval"] = True
                plan = []
                break
            except PolicyViolation as e:
                failure_class, error = f"policy:{step['tool']}", str(e)
                self._transition(State.HALTED, reason=str(e)); break
            except Exception as e:
                if step.get("guard_for") and isinstance(e, FileNotFoundError):
                    # guard's parent dir is itself missing → target is missing → clean stop
                    result = f"guard: '{step['guard_for']}' not found; aborting cleanly"
                    wm["steps"].append({"tool": step["tool"], "ok": True, "result": result})
                    wm["graceful_missing"] = True
                    break
                failure_class = f"tool:{step['tool']}:{type(e).__name__}"
                error = str(e); break

            self._transition(State.OBSERVING)
            wm["steps"].append({"tool": step["tool"], "ok": True,
                                "result": str(result)[:500]})
            self._checkpoint(wm)
            if not plan:
                break

        # ---- VERIFYING: finish is unreachable without a passing verification ----
        verified, answer = False, None
        if failure_class is None:
            self._transition(State.VERIFYING)
            verified, answer = self._verify(goal, wm)
            if not verified:
                failure_class, error = "verify:failed", f"verification failed for: {goal}"

        # ---- REFLECTING: the only place memory is written ----
        self._transition(State.REFLECTING)
        episode = {"goal": goal, "success": verified, "steps": wm["steps"],
                   "failure_class": failure_class, "error": error,
                   "duration_s": round(time.time() - start, 2),
                   "lessons_used": [l["id"] for l in lessons]}
        eid = self.memory.record_episode(episode)
        self._transition(State.DONE if verified else State.HALTED, episode=eid)
        return {"success": verified, "answer": answer, "episode": eid,
                "failure_class": failure_class, "error": error,
                "awaiting_approval": bool(wm.get("awaiting_approval"))}

    # ---- deterministic verifier (outside the model's editable surface) ----
    def _verify(self, goal: str, wm: dict) -> tuple[bool, str | None]:
        if not wm["steps"]:
            return False, None
        last = wm["steps"][-1]["result"]
        g = goal.lower()
        if "calculate" in g:
            expr = re.search(r"calculate\s+(.+)", g).group(1).strip()
            try:
                expected = self.tools.call("calculator", expression=expr)
                return float(last) == float(expected), last
            except Exception:
                return False, None
        if re.search(r"write (?:a )?file", g):
            m = re.search(r"with content (.+)", goal, re.I)
            return (m and m.group(1) == last), last      # read-back equals intent
        if wm.get("graceful_missing"):
            return True, last                             # lesson-guided clean handling
        if wm.get("awaiting_approval"):
            return True, last                             # clean halt-and-ask, per PRD
        if "schedule" in g:
            return "scheduled" in str(last), str(last)
        if "search" in g and "'title': 'no results'" in str(last):
            return False, str(last)   # empty placeholder is not a verified answer
        if g.startswith("book") or " book " in f" {g} ":
            return "booking" in str(last) and "ready_for_payment" not in str(last) or \
                   "ready for payment" in str(last), str(last)
        return bool(last), str(last)


if __name__ == "__main__":
    import sys
    agent = Agent(Path(__file__).parent)
    goal = " ".join(sys.argv[1:]) or "calculate 2+2"
    print(json.dumps(agent.run(goal), indent=2))
