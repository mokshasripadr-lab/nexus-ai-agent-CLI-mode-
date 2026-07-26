"""Executes scheduled tasks from state/schedule.json whose time has come.

Add to cron for true background scheduling, e.g. every 5 minutes:
    */5 * * * * cd /path/to/atlas-agent && python3 run_scheduled.py
Accepts 'when' as ISO datetime (2026-07-04T09:00), date (2026-07-04), or 'now'.
Unparseable times run on the next invocation and are marked done.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent


def due(when: str) -> bool:
    now = datetime.now()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(when.strip(), fmt) <= now
        except ValueError:
            continue
    return True  # 'now', natural language, or unparseable → run on next tick


def main():
    p = ROOT / "state" / "schedule.json"
    if not p.exists():
        print("no schedule.json — nothing to do")
        return
    sched = json.loads(p.read_text())
    from subagents import Orchestrator
    orch = Orchestrator(ROOT)
    ran = 0
    for entry in sched:
        if entry["status"] == "scheduled" and due(entry["when"]):
            print(f"running {entry['id']}: {entry['goal']}")
            result = orch.run(entry["goal"])
            entry["status"] = "done" if result["success"] else "failed"
            entry["last_result"] = result["summary"]
            ran += 1
    p.write_text(json.dumps(sched, indent=2))
    print(f"{ran} task(s) executed")


if __name__ == "__main__":
    main()
