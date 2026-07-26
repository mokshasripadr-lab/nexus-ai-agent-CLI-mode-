"""Human approval CLI for Atlas human-tier actions (e.g. bookings).

python3 approve.py            # review pending requests interactively
python3 approve.py --grant book --once   # approve, run pending book requests, revoke
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "state"


def main():
    pending_path = STATE / "pending_approvals.json"
    pending = json.loads(pending_path.read_text()) if pending_path.exists() else []
    if not pending:
        print("No pending approvals.")
        return
    print(f"{len(pending)} pending request(s):\n")
    for i, req in enumerate(pending, 1):
        print(f"  {i}. [{req['tool']}] {json.dumps(req['args'])[:120]}  ({req['ts']})")

    once = "--once" in sys.argv
    if "--grant" in sys.argv:
        tool = sys.argv[sys.argv.index("--grant") + 1]
        answer = "y"
    else:
        tool = pending[0]["tool"]
        answer = input(f"\nApprove and execute pending '{tool}' request(s)? [y/N] ").strip().lower()

    if answer != "y":
        print("Denied. Requests remain pending.")
        return

    # grant approval, replay the pending calls, then (optionally) revoke
    approvals_path = STATE / "approvals.json"
    approvals = json.loads(approvals_path.read_text()) if approvals_path.exists() else {}
    approvals[tool] = True
    approvals_path.write_text(json.dumps(approvals, indent=2))

    from agent import Agent
    agent = Agent(ROOT)
    remaining = []
    for req in pending:
        if req["tool"] != tool:
            remaining.append(req)
            continue
        result = agent.tools.call(req["tool"], **req["args"])
        print(f"executed: {result}")
    pending_path.write_text(json.dumps(remaining, indent=2))

    if once:
        approvals[tool] = False
        approvals_path.write_text(json.dumps(approvals, indent=2))
        print(f"(approval for '{tool}' revoked again — one-time grant)")


if __name__ == "__main__":
    main()
