"""Atlas tool registry — hard allowlist, risk tiers, path jail (PRD §3)."""
from __future__ import annotations
import ast, io, json, contextlib
from pathlib import Path

class PolicyViolation(Exception):
    """Raised when the agent attempts something outside policy → HALTED."""

class ApprovalRequired(Exception):
    """Raised when a human-tier tool runs without prior human approval."""

class ToolRegistry:
    def __init__(self, config: dict, workspace: str | Path = "workspace",
                 state_dir: str | Path = "state"):
        self.allowed = config["allowed_tools"]
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._impl = {
            "read_file": self._read_file, "write_file": self._write_file,
            "list_dir": self._list_dir, "run_python": self._run_python,
            "web_search": self._web_search, "calculator": self._calculator,
            "web_fetch": self._web_fetch, "schedule_task": self._schedule_task,
            "book": self._book, "computer_use": self._computer_use,
        }

    def _tier(self, name: str) -> str:
        spec = self.allowed[name]
        return spec.get("tier", "safe") if isinstance(spec, dict) else "safe"

    def _approvals(self) -> dict:
        p = self.state_dir / "approvals.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def call(self, tool, /, **kwargs):
        # 'tool' is positional-only so tool kwargs like name= can never collide with it
        if tool not in self.allowed:                      # deny-by-default
            raise PolicyViolation(f"tool '{tool}' is not on the allowlist")
        if tool in ("verify", "finish"):
            raise PolicyViolation(f"'{tool}' is a state-machine action, not a direct tool")
        if self._tier(tool) == "human" and not self._approvals().get(tool):
            # record the pending request for the human to review via approve.py
            pending_path = self.state_dir / "pending_approvals.json"
            pending = json.loads(pending_path.read_text()) if pending_path.exists() else []
            pending.append({"tool": tool, "args": kwargs,
                            "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S")})
            pending_path.write_text(json.dumps(pending, indent=2))
            raise ApprovalRequired(
                f"'{tool}' is a human-tier action — request saved for review (run: python3 approve.py)")
        return self._impl[tool](**kwargs)

    # ---- path jail -------------------------------------------------------
    def _jail(self, path: str) -> Path:
        p = (self.workspace / path).resolve()
        # Proper containment: p must be the workspace itself or a descendant.
        # A raw str.startswith would let siblings like 'workspace_evil' slip past
        # the 'workspace' prefix, so compare path components instead.
        if p != self.workspace and self.workspace not in p.parents:
            raise PolicyViolation(f"path escapes workspace jail: {path}")
        return p

    # ---- tool implementations ---------------------------------------------
    def _read_file(self, path: str) -> str:
        p = self._jail(path)
        if not p.exists():
            raise FileNotFoundError(f"no such file in workspace: {path}")
        return p.read_text()

    def _write_file(self, path: str, content: str) -> str:
        p = self._jail(path)
        if p.exists():                                    # guarded: backup first
            p.with_suffix(p.suffix + ".bak").write_text(p.read_text())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {len(content)} chars to {path}"

    def _list_dir(self, path: str = ".") -> list[str]:
        return sorted(x.name for x in self._jail(path).iterdir())

    def _run_python(self, code: str) -> str:
        """Sandboxed eval: expressions and simple statements, no imports/IO/network."""
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise PolicyViolation("run_python: imports are not allowed")
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise PolicyViolation("run_python: dunder access is not allowed")
        buf = io.StringIO()
        safe_globals = {"__builtins__": {"print": print, "len": len, "range": range,
                        "sum": sum, "min": min, "max": max, "sorted": sorted,
                        "abs": abs, "round": round, "str": str, "int": int,
                        "float": float, "list": list, "dict": dict, "enumerate": enumerate,
                        "zip": zip}}
        with contextlib.redirect_stdout(buf):
            exec(compile(tree, "<agent>", "exec"), safe_globals)  # noqa: S102 (AST-vetted)
        return buf.getvalue().strip() or "(no output)"

    def _web_search(self, query: str) -> list[dict]:
        """Live DuckDuckGo search; falls back to the local corpus offline."""
        try:
            import re as _re, urllib.request, urllib.parse
            url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Atlas)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8", "ignore")
            results = []
            for m in _re.finditer(
                    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html):
                title = _re.sub(r"<[^>]+>", "", m.group(2)).strip()
                results.append({"title": title, "url": m.group(1), "text": title})
                if len(results) >= 5:
                    break
            if results:
                return results
        except Exception:
            pass
        corpus_path = self.workspace / "_search_corpus.json"
        corpus = json.loads(corpus_path.read_text()) if corpus_path.exists() else []
        q = query.lower()
        hits = [d for d in corpus if any(w in d["text"].lower() for w in q.split())]
        return hits[:5] or [{"title": "no results", "text": "", "url": ""}]

    def _web_fetch(self, url: str) -> str:
        """Live page fetch (read-only). Returns readable text, capped at 5000 chars."""
        import re as _re, urllib.request
        if not url.startswith(("http://", "https://")):
            raise PolicyViolation("web_fetch: only http(s) URLs allowed")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Atlas)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(500_000).decode("utf-8", "ignore")
        text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=_re.S | _re.I)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()
        return text[:5000]

    def _schedule_task(self, name: str, when: str, goal: str) -> str:
        """Append a task to state/schedule.json (executed by run_scheduled.py / cron)."""
        p = self.state_dir / "schedule.json"
        sched = json.loads(p.read_text()) if p.exists() else []
        entry = {"id": f"S-{len(sched) + 1:03d}", "name": name, "when": when,
                 "goal": goal, "status": "scheduled"}
        sched.append(entry)
        p.write_text(json.dumps(sched, indent=2))
        return f"scheduled '{name}' ({entry['id']}) for {when}"

    def _book(self, request: str, details: dict | None = None) -> str:
        """Human-tier booking: builds a structured booking record. Reaching this code
        means the human already approved via approve.py. Payment is NEVER automated —
        the record stops at 'ready_for_payment' and the human completes payment."""
        p = self.state_dir / "bookings.json"
        bookings = json.loads(p.read_text()) if p.exists() else []
        rec = {"id": f"B-{len(bookings) + 1:03d}", "request": request,
               "details": details or {}, "status": "ready_for_payment",
               "note": "Atlas prepared this booking; complete payment manually.",
               "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S")}
        bookings.append(rec)
        p.write_text(json.dumps(bookings, indent=2))
        return f"booking {rec['id']} prepared and ready for payment: {request}"

    def _computer_use(self, goal: str, execute: bool = False, max_steps: int = 15):
        """Human-tier: drive the mouse/keyboard by vision to accomplish `goal`.
        Reaching here means approve.py cleared it. Still defaults to a DRY RUN
        (execute=False) — pass execute=True only after review. Irreversible
        actions inside the loop bounce back to approval automatically."""
        import importlib, sys as _sys
        _sys.path.insert(0, str(self.state_dir.parent))
        cu = importlib.import_module("computer_use")
        return cu.run(goal, execute=bool(execute), max_steps=int(max_steps))

    def _calculator(self, expression: str) -> float:
        node = ast.parse(expression, mode="eval")
        for n in ast.walk(node):
            if not isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                                  ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow,
                                  ast.Mod, ast.FloorDiv, ast.USub, ast.UAdd)):
                raise PolicyViolation(f"calculator: disallowed expression node {type(n).__name__}")
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
                # Exponent must be a small literal: computed/huge exponents (e.g. 9**9**9)
                # hang the interpreter in-step, where budgets can't interrupt.
                e = n.right
                if isinstance(e, ast.UnaryOp) and isinstance(e.operand, ast.Constant):
                    e = e.operand
                if not (isinstance(e, ast.Constant) and isinstance(e.value, (int, float))
                        and abs(e.value) <= 1000):
                    raise PolicyViolation("calculator: exponent must be a literal with |e| <= 1000")
        return eval(compile(node, "<calc>", "eval"))  # noqa: S307 (AST-vetted arithmetic only)
