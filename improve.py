"""Atlas daily self-improvement cycle (PRD §5).

mine failures → write lessons → consolidate → evaluate → ratchet → report
Run: python improve.py
"""
from __future__ import annotations
import json, time
from pathlib import Path

ROOT = Path(__file__).parent

# failure_class → (trigger, rule). Extend over time; unknown classes get a generic lesson.
LESSON_TEMPLATES = {
    "tool:read_file:FileNotFoundError": (
        "before read_file",
        "Verify the path exists with list_dir before reading; if missing, report cleanly instead of crashing."),
    "planning:no-strategy": (
        "planning",
        "If no strategy matches the goal, decompose the goal into known verbs (calculate/read/write/search/list) before giving up."),
    "verify:failed": (
        "before finish",
        "Re-check the final result against the literal goal text before declaring done; re-plan once if mismatch."),
    "budget:max_steps": (
        "planning",
        "Prefer the shortest plan; drop exploratory steps when a direct tool exists."),
}

EVAL_TASKS = [
    ("calculate 6*7", True),
    ("calculate (100-58)/2", True),
    ("write file note.txt with content hello atlas", True),
    ("read file note.txt", True),
    ("read file does_not_exist_xyz.txt", True),   # must handle gracefully via lesson
    ("count files in .", True),
    ("write file sub/deep.txt with content nested-ok", True),  # nested path stays inside the jail
    ("read file sub/deep.txt", True),   # nested read must return real content, not a false 'missing'
    ("count files in nonexistent_dir", True),  # missing dir must abort cleanly via lesson guard
]


def run_eval(root: Path) -> float:
    from agent import Agent
    agent = Agent(root)
    passed = sum(1 for goal, _ in EVAL_TASKS if agent.run(goal)["success"])
    return passed / len(EVAL_TASKS)


def main():
    from memory import Memory
    mem = Memory(ROOT / "memory")
    state_file = ROOT / "state" / "improve_state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {
        "last_episode_count": 0, "best_score": 0.0, "runs": 0}

    # 1) MINE new failures
    episodes = mem.episodes()
    new = episodes[state["last_episode_count"]:]
    failures = [e for e in new if not e.get("success")]
    escalations, new_lessons = [], []

    for ep in failures:
        fc = ep.get("failure_class") or "unknown"
        if mem.has_lesson(fc):
            # same failure class happened again AFTER a lesson existed → P0
            escalations.append(fc)
            continue
        trigger, rule = LESSON_TEMPLATES.get(
            fc, (fc.split(":")[0], f"Avoid repeat of {fc}: inspect episode {ep['id']} and add a guard."))
        new_lessons.append(mem.add_lesson(fc, trigger, rule, ep["id"]))

    # 2) CONSOLIDATE on a copy (dedupe by failure_class is enforced at write time already)
    # Backup by overwriting file contents (never delete dirs — works on restricted mounts).
    backup = ROOT / "state" / "memory_backup"
    backup.mkdir(parents=True, exist_ok=True)
    mem_dir = ROOT / "memory"
    for f in mem_dir.glob("*.json*"):
        (backup / f.name).write_text(f.read_text())

    # 3) EVALUATE
    score = run_eval(ROOT)

    # 4) RATCHET — never keep a change that makes the agent worse
    if score < state["best_score"]:
        for f in backup.glob("*.json*"):                  # restore by content
            (mem_dir / f.name).write_text(f.read_text())
        outcome = f"ROLLED BACK (score {score:.0%} < best {state['best_score']:.0%})"
        score = state["best_score"]
    else:
        outcome = "committed"
        state["best_score"] = score

    # 5) REPORT
    state.update({"last_episode_count": len(mem.episodes()),
                  "runs": state["runs"] + 1,
                  "last_run": time.strftime("%Y-%m-%d %H:%M")})
    state_file.parent.mkdir(exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))

    report = (ROOT / "logs" / "improvement_report.md")
    report.parent.mkdir(exist_ok=True)
    with report.open("a") as f:
        f.write(f"\n## Run {state['runs']} — {state['last_run']}\n"
                f"- Eval score: **{score:.0%}** ({outcome})\n"
                f"- New failures mined: {len(failures)}\n"
                f"- New lessons: {[l['id'] + ' ' + l['failure_class'] for l in new_lessons] or 'none'}\n"
                f"- Total lessons: {len(mem.lessons())}\n"
                + (f"- ⚠️ **P0 — repeated failure classes:** {escalations}\n" if escalations else ""))

    print(json.dumps({"score": f"{score:.0%}", "outcome": outcome,
                      "new_lessons": len(new_lessons),
                      "repeated_failures_P0": escalations}, indent=2))


if __name__ == "__main__":
    main()
