"""Atlas memory system — episodic, semantic, and lessons memory (PRD §2).

All memory is human-readable JSON/JSONL under memory/. Writes carry provenance.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

class Memory:
    def __init__(self, root: str | Path = "memory"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.episodes_path = self.root / "episodes.jsonl"
        self.knowledge_path = self.root / "knowledge.json"
        self.lessons_path = self.root / "lessons.json"
        for p, default in [(self.knowledge_path, {}), (self.lessons_path, [])]:
            if not p.exists():
                p.write_text(json.dumps(default, indent=2))

    # ---- episodic -------------------------------------------------------
    def next_episode_id(self) -> str:
        return f"E-{sum(1 for _ in self._iter_episodes()) + 1:04d}"

    def _iter_episodes(self):
        if self.episodes_path.exists():
            for line in self.episodes_path.read_text().splitlines():
                if line.strip():
                    yield json.loads(line)

    def episodes(self) -> list[dict]:
        return list(self._iter_episodes())

    def record_episode(self, episode: dict) -> str:
        episode.setdefault("id", self.next_episode_id())
        episode.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with self.episodes_path.open("a") as f:
            f.write(json.dumps(episode) + "\n")
        return episode["id"]

    # ---- semantic -------------------------------------------------------
    def knowledge(self) -> dict:
        return json.loads(self.knowledge_path.read_text())

    def learn_fact(self, key: str, value, source_episode: str, confidence: float = 0.7):
        kb = self.knowledge()
        kb[key] = {"value": value, "source": source_episode,
                   "confidence": confidence, "last_used": time.strftime("%Y-%m-%d")}
        self.knowledge_path.write_text(json.dumps(kb, indent=2))

    # ---- lessons (anti-mistake system) ----------------------------------
    def lessons(self) -> list[dict]:
        return json.loads(self.lessons_path.read_text())

    def has_lesson(self, failure_class: str) -> bool:
        return any(l["failure_class"] == failure_class for l in self.lessons())

    def add_lesson(self, failure_class: str, trigger: str, rule: str, source_episode: str) -> dict:
        lessons = self.lessons()
        if self.has_lesson(failure_class):           # merge, never duplicate
            for l in lessons:
                if l["failure_class"] == failure_class:
                    l["rule"] = rule
                    l["sources"] = sorted(set(l.get("sources", []) + [source_episode]))
                    self.lessons_path.write_text(json.dumps(lessons, indent=2))
                    return l
        lesson = {"id": f"L-{len(lessons) + 1:04d}", "failure_class": failure_class,
                  "trigger": trigger, "rule": rule, "sources": [source_episode],
                  "created": time.strftime("%Y-%m-%d"), "times_applied": 0}
        lessons.append(lesson)
        self.lessons_path.write_text(json.dumps(lessons, indent=2))
        return lesson

    def lessons_for(self, trigger_substr: str) -> list[dict]:
        """Lessons whose trigger matches the current situation; bumps usage count."""
        lessons = self.lessons()
        hits = [l for l in lessons if trigger_substr in l["trigger"] or l["trigger"] in trigger_substr]
        if hits:
            for l in hits:
                l["times_applied"] = l.get("times_applied", 0) + 1
            self.lessons_path.write_text(json.dumps(lessons, indent=2))
        return hits
