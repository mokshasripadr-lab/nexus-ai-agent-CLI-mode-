"""Atlas v2 — multi-agent orchestration.

Orchestrator decomposes a goal into subtasks, routes each to a specialist
sub-agent (researcher / executor / scheduler / booker), and a critic checks
every result with one retry. Sub-agents share one memory (lessons apply to all)
but each runs with a narrowed tool allowlist — a booker can never write files,
an executor can never book.

Run: python3 subagents.py "search for flights to goa then book flight to goa for aug 12"
"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path

from agent import Agent, ROLE_TOOLS


class Orchestrator:
    def __init__(self, root: str | Path = "."):
        self.root = Path(root)
        self.crew = {role: Agent(root, role=role) for role in ROLE_TOOLS}
        self.lead = Agent(root)              # full-allowlist agent for decomposition fallback

    # ---- decomposition ----------------------------------------------------
    def decompose(self, goal: str) -> list[dict]:
        """Split a compound goal into routed subtasks. Uses Gemini when available,
        deterministic heuristics otherwise."""
        planner = self.lead.planner
        if hasattr(planner, "_call"):        # LLM available
            try:
                text = planner._call(
                    "Decompose this user goal into sequential subtasks for a crew of "
                    "agents. Roles: researcher (web search/fetch/analysis), executor "
                    "(files/calculations), scheduler (schedule future tasks), booker "
                    "(prepare bookings/reservations). Goal: " + goal +
                    '\nReply ONLY JSON: [{"role": ..., "goal": ...}]')
                subtasks = json.loads(text)
                if (isinstance(subtasks, list) and subtasks and
                        all(s.get("role") in ROLE_TOOLS for s in subtasks)):
                    return subtasks
            except Exception:
                pass
        return self._heuristic_decompose(goal)

    def _heuristic_decompose(self, goal: str) -> list[dict]:
        parts = [p.strip() for p in
                 re.split(r"\s*(?:;|,? then |,? and then |,? after that )\s*", goal, flags=re.I)
                 if p.strip()]
        return [{"role": self._route(p), "goal": p} for p in parts]

    @staticmethod
    def _route(subgoal: str) -> str:
        g = subgoal.lower()
        if re.search(r"\bbook|reserve|ticket\b", g):
            return "booker"
        if re.search(r"\bschedule|remind|every day|daily at\b", g):
            return "scheduler"
        if re.search(r"\bsearch|find|research|fetch|look up|compare\b", g):
            return "researcher"
        if re.search(r"\bfile|write|read|calculate|count|compute\b", g):
            return "executor"
        return "generalist"     # anything else: full toolset, no capability gaps

    # ---- execution with critic ---------------------------------------------
    def run(self, goal: str) -> dict:
        subtasks = self.decompose(goal)
        results = []
        for st in subtasks:
            agent = self.crew[st["role"]]
            r = agent.run(st["goal"])
            if not r["success"] and not r.get("awaiting_approval"):
                r = agent.run(st["goal"])            # critic: one retry (lessons may now apply)
                r["retried"] = True
            results.append({"role": st["role"], "goal": st["goal"], **r})
            if not r["success"] and not r.get("awaiting_approval"):
                break                                 # don't build on a failed step
        ok = all(r["success"] for r in results) and len(results) == len(subtasks)
        pending = [r for r in results if r.get("awaiting_approval")]
        return {"success": ok, "subtasks": results,
                "pending_approvals": len(pending),
                "summary": self._summarize(results, pending)}

    @staticmethod
    def _summarize(results: list[dict], pending: list[dict]) -> str:
        lines = [f"[{r['role']}] {'OK' if r['success'] else 'FAIL'} — {r['goal']}"
                 + (f" → {str(r['answer'])[:90]}" if r.get("answer") else "")
                 for r in results]
        if pending:
            lines.append(f"{len(pending)} action(s) await your approval: python3 approve.py")
        return "\n".join(lines)


if __name__ == "__main__":
    orch = Orchestrator(Path(__file__).parent)
    goal = " ".join(sys.argv[1:]) or "calculate 2+2"
    out = orch.run(goal)
    print(out["summary"])
    print(json.dumps({k: v for k, v in out.items() if k != "summary"}, indent=2, default=str))
