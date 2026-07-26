# PRD — "Atlas" Autonomous Work Agent

**Version:** 1.0 · **Date:** 2026-07-02 · **Owner:** Ramananda · **Author role:** AI Principal Architect

---

## 1. Vision & Target

Atlas is a general-purpose autonomous agent that executes work tasks — research, file processing, data analysis, writing, and coding — with maximum accuracy and provable safety, and that **compounds in capability over time** by learning from every mistake it makes and never repeating a known failure class.

### 1.1 Product goals (in priority order)

1. **Safety** — the agent must never take an action outside its allowlisted tool policy, never exceed its resource budgets, and must halt-and-ask on any irreversible operation.
2. **Accuracy** — every task result is self-verified before being reported as done. Target: ≥95% task success on the regression eval suite; a task is "successful" only if the verifier passes.
3. **Capability growth** — a daily improvement cycle mines the agent's logs, converts failures into persistent "lessons," and re-runs the eval suite. Capability must be monotonically non-decreasing: an improvement is only kept if the eval score does not drop (ratchet rule).

### 1.2 Non-goals

- Unsupervised access to money, credentials, or destructive system operations.
- "Unbounded self-modification" — the agent may update its *memory, lessons, and prompts*, never its own safety policy or tool allowlist. Those are human-owned files.

### 1.3 Success metrics

| Metric | Target | Measured by |
|---|---|---|
| Eval suite pass rate | ≥ 95% | `tests/` regression suite, run daily |
| Repeated-mistake rate | 0 (same failure class twice) | lesson-match audit in `improve.py` |
| Safety violations | 0 | policy-enforcement log audit |
| Mean steps per task | ↓ over time | episodic memory stats |

---

## 2. Memory System

Four-layer memory, all persisted as human-readable files under `memory/` so the owner can inspect and edit everything the agent believes.

### 2.1 Working memory (per-task, volatile)

The in-context scratchpad for the current task: goal, plan, step results, open questions. Cleared when the task ends; its summary is written to episodic memory.

### 2.2 Episodic memory (`memory/episodes.jsonl`)

Append-only log of every completed task: goal, plan taken, tools used, outcome (success/failure), verifier result, duration, step count. This is the raw material the improvement cycle mines.

### 2.3 Semantic memory (`memory/knowledge.json`)

Durable facts and preferences the agent has learned ("user prefers concise output," "dataset X has a header row problem"). Key-value with provenance (which episode taught it) and a confidence score. Facts decay: unused low-confidence facts are pruned by the consolidation pass.

### 2.4 Lessons memory (`memory/lessons.json`) — the anti-mistake system

Each lesson is a structured record:

```json
{
  "id": "L-0007",
  "failure_class": "tool:read_file:missing-path-check",
  "trigger": "before calling read_file",
  "rule": "Verify the path exists with list_dir before reading.",
  "source_episode": "E-0042",
  "created": "2026-07-02",
  "times_applied": 3
}
```

**Rules:** lessons are injected into the planner's context at the matching trigger point; a lesson can never be deleted automatically, only merged; if the same `failure_class` occurs twice, that is a P0 defect in the improvement cycle itself.

### 2.5 Memory write rules

- Only the agent's reflection step and `improve.py` may write to memory — never mid-action tool code.
- Every write carries provenance (episode ID).
- `improve.py` runs consolidation: dedupe lessons, merge near-duplicate facts, prune stale entries. Consolidation runs on a copy first; it is committed only if the eval suite still passes (ratchet rule).

---

## 3. Allowed Tools (Tool Policy)

The tool registry is a **hard allowlist** defined in `config.yaml`. A tool not listed does not exist as far as the agent is concerned. Each tool declares a risk tier that governs how it may run.

| Tool | Purpose | Risk tier | Constraints |
|---|---|---|---|
| `read_file` | Read a file in the workspace | safe | Workspace-jailed paths only |
| `write_file` | Create/overwrite a file | guarded | Workspace-jailed; never overwrites without backup |
| `list_dir` | List directory contents | safe | Workspace-jailed |
| `run_python` | Execute a Python snippet | guarded | 30s timeout, no network, workspace-jailed FS |
| `web_search` | Search the web | safe | Read-only; results must be cited in output |
| `calculator` | Exact arithmetic | safe | — |
| `finish` | Declare task done with result | safe | Only callable after `verify` passes |
| `verify` | Self-check the result vs. the goal | safe | Mandatory before `finish` |

**Risk tiers:** `safe` = auto-execute · `guarded` = auto-execute with logging + jail enforcement · `human` = requires explicit human approval (reserved for future tools: email send, shell, network writes). **Deny-by-default:** anything else is refused and logged.

**Path jail:** all file tools resolve paths and refuse anything outside the agent workspace directory (blocks `../` escapes and absolute paths).

---

## 4. State Management Rules

The agent is an explicit finite-state machine. All transitions are logged to `logs/agent.log`.

```
IDLE → PLANNING → ACTING ⇄ OBSERVING → VERIFYING → REFLECTING → DONE
                     ↓ (budget/policy breach)         ↓ (verify fail, retries left)
                   HALTED                            PLANNING (re-plan)
```

### 4.1 States

- **PLANNING** — build/revise a step plan; relevant lessons are injected here.
- **ACTING** — execute exactly one allowlisted tool call per step.
- **OBSERVING** — record the tool result into working memory.
- **VERIFYING** — run the `verify` tool against the original goal. `finish` is unreachable without a passing verification.
- **REFLECTING** — write the episode to episodic memory; on failure, draft a candidate lesson.
- **HALTED** — terminal safety state: budget exhausted, policy violation, or unrecoverable error. Never auto-resumes; requires owner review.

### 4.2 Hard budgets (enforced by the state machine, not the model)

- Max **20 steps** per task, max **3 re-plans**, max **2 verify retries**, max **120 s** wall-clock per task, max **1** concurrent task.
- Any breach → HALTED with a full state dump for the improvement cycle to study.

### 4.3 Invariants

1. No tool call outside PLANNING-approved plan steps or the allowlist.
2. Every state transition is logged before it happens (write-ahead logging).
3. Checkpoint after every step → `state/checkpoint.json`; a crashed task is resumable or cleanly abandonable, never half-committed.
4. Memory writes happen only in REFLECTING (or in `improve.py`).
5. The safety policy file and tool allowlist are read-only to the agent at runtime.

---

## 5. Self-Improvement Loop (daily cron)

Runs once daily. Pipeline:

1. **Mine** — read new episodes since last run; cluster failures by failure class.
2. **Learn** — for each new failure class, write a lesson (trigger + rule). If a failure class repeats, escalate it in the report as a P0.
3. **Consolidate** — dedupe/merge lessons and facts (on a copy).
4. **Evaluate** — run the full regression eval suite against the updated memory.
5. **Ratchet** — commit the changes only if the score ≥ previous score; otherwise roll back and report.
6. **Report** — append to `logs/improvement_report.md`: score trend, new lessons, escalations.

This is the honest version of "gets more powerful every day": monotonic, measured, and audited — not magic.

---

## 6. Risks & mitigations

- **Reward hacking / verifier gaming** → verifier logic lives outside the model's editable surface; checks are deterministic where possible.
- **Memory poisoning** → provenance on every write; owner-readable files; consolidation quarantines contradictory facts.
- **Runaway loops** → hard budgets in code, not prompts.
- **Capability regression** → ratchet rule: no change survives a failing eval.

## 7. Milestones

**M1 (today):** core loop, memory, tools, state machine, eval suite, daily improvement job. **M2:** plug in Claude API planner (`ANTHROPIC_API_KEY`), expand tool set behind `human` tier. **M3:** multi-task queue, richer eval corpus, owner dashboard.
