# Nexus CLI — Autonomous Work Agent

A Claude Code-style terminal agent. Pick a top-tier model, then chat, run
autonomous multi-step tasks, and schedule cron jobs — all from one command.

## Install in one paste

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/mokshasripadr-lab/nexus-ai-agent-CLI-mode-/main/install.sh | bash
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/mokshasripadr-lab/nexus-ai-agent-CLI-mode-/main/install.ps1 | iex
```

That installs the whole agent to `~/.nexus/agent`, creates a global `nexus`
command, and starts it. On first run it asks which model you want
(Gemini 2.5 Pro · Claude Sonnet · GPT-4o) and for that provider's API key.
Your key is saved privately to `~/.nexus/config.json` — it is **never** bundled
with the code or pushed to GitHub.

After that, just type `nexus` anywhere.

## Commands

`/model` switch LLM · `/engine` switch engine (Swift/NextAgent/Atlas Deep) ·
`/do <task>` autonomous crew · `/cron <task>` schedule it · `/key` add another
key · `/keys` `/clear` `/help` `/exit`. Also: `open email`, `open docs`,
`search <thing>` run directly.

---

See `PRD.md` for full architecture. Developer quick start:

```bash
pip install pytest pyyaml
python3 agent.py "calculate 6*7"          # run a task
python3 -m pytest test_agent.py -v       # regression suite (12 tests)
python3 improve.py                        # daily self-improvement cycle
```

Files: `agent.py` (state machine + planner) · `memory.py` (episodic/semantic/lessons) · `tools.py` (allowlisted tools, path jail) · `improve.py` (mine failures → lessons → eval → ratchet) · `config.yaml` (human-owned safety policy — the agent can never edit it).

Inspect what the agent believes: `memory/lessons.json`, `memory/episodes.jsonl`, `logs/improvement_report.md`.

**Real intelligence:** the planner auto-selects based on available keys (checked in order): `ANTHROPIC_API_KEY` → Claude planner (needs `pip install anthropic`); `GEMINI_API_KEY` → Gemini planner (no extra install; set `GEMINI_MODEL` to override the default `gemini-2.5-flash`); neither → offline mock. Keys go in `.env` in this folder — keep it private, never commit it. If an API call fails, the agent falls back to the mock planner instead of stalling. Safety, memory, verification, and ratchet are identical regardless of planner.

**Daily improvement:** the Cowork scheduled task `atlas-daily-improvement` runs every day at 7:07 AM.
