---
name: tool-intake
description: "Evaluate and decide whether to install a new skill, agent tool, or repo into an agent stack, and at which scope (user-global, project, or decline). Use whenever the user shares a repo, skill, or tool link and asks whether it fits, whether to install it, global vs project scope, whether it overlaps something already installed, or how to record the install decision. Automates the mechanical checks: upstream vitality, duplicate-function scan across installed skills, and install preflight red flags."
---

# Tool Intake

A fixed intake procedure for new tools and skills, instead of improvising every time. Goal: only add things that are real, non-duplicative, and compatible, and leave a decision trail.

## Step 0 — Run the audit script first

Gather evidence mechanically before judging anything:

```bash
python3 <skill-dir>/scripts/intake_audit.py https://github.com/owner/repo
python3 <skill-dir>/scripts/intake_audit.py ./cloned-or-downloaded-path
```

The script (stdlib-only Python) reports, without modifying anything:

1. **Upstream vitality** (for a GitHub URL): stars, license, last push date, archived flag, open issues, and README red flags. A repo with no license, no pushes in a long time, or archived status starts at a disadvantage.
2. **Installed-skill inventory**: auto-detected skill roots (user-level `~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills`, plus `.agents/skills` / `.claude/skills` / `.opencode/skill` found walking up from the current directory). Pass `--roots dir1,dir2` (repeatable) for nonstandard stacks or workspace layers the walk cannot see.
3. **Duplicate-function scan**: scores every installed skill's name + description against the candidate's keywords and lists the closest overlaps. High overlap means an existing skill already covers the function.
4. **Preflight red flags**: README mentions of root/sudo requirements, heavy dependencies (browser engines, Docker, daemons), and mandatory API keys.

Use `--keywords a,b,c` to steer the dedup scan, `--no-network` to skip live GitHub data, `--json` for machine-readable output.

## Step 1 — Judge from the full text, never the blurb

Read the actual README and the candidate's `SKILL.md` body end to end. One-line registry descriptions mislead: a blurb-level pass once cut three skills that a full-text read reinstated. The script's overlap scores are evidence, not a verdict.

## Step 2 — One copy per function

If an installed skill already covers the same function, decline, or state the concrete difference before deciding. Two skills for one function double the always-loaded metadata cost and split improvements across forks.

## Step 3 — Pick the scope

- **User-global** (`~/.agents/skills`, `~/.claude/skills`): only truly cross-project tools. Everything here loads in every session.
- **Project-level** (the repo's `.agents/skills` / `.claude/skills` / `.opencode/skill`): anything bound to one tech stack, one repo, or a workflow that only exists there.
- **Decline or archive**: not useful now. Keep a cold-storage directory outside every skill root for maybes; move it in later if it earns a place.

When in doubt, project-level. Polluting global context with a project-specific skill taxes every future turn.

## Step 4 — Preflight compatibility before installing

- **Output protocol vs the viewing surface**: if the tool renders rich output (graphics protocols, image streams, TUI effects), check the surface the user actually views through supports it. A pixel-protocol tool froze a terminal multiplexer UI on install once.
- **Privileges**: refuses to run without root? That constraint will resurface in cron, CI, and containers.
- **Resident footprint**: daemons and browser engines add memory pressure that can kill neighboring processes.
- **Runtime reachability**: the runtime on the interactive PATH (version managers, nvm) may be invisible to systemd/cron environments.

## Step 5 — Install, then verify loading

Installing is not done until the agent's real loader confirms it: run the loader the way the harness does, at the target working directories, and check the new skill is counted exactly once with zero name collisions. A skill that only exists in the directory but fails to load, or collides with an installed name, is not installed. If the stack has no scriptable loader, start a fresh session and confirm the skill appears and triggers.

## Step 6 — Record the decision

Write one durable line (memory note, decision log, or `AGENTS.md` journal, whichever this machine keeps): date, install or decline, scope, reason, evidence link. Decisions that live only in chat get re-litigated from scratch next time.

## Anti-patterns

- Judging from a README summary instead of the full body.
- Installing a project-specific skill globally.
- Keeping a second skill for an existing function.
- Installing without loader verification.
- Skipping the decision record.

## Script details

`scripts/intake_audit.py` — Python 3.10+, stdlib only. Read-only: it never modifies files or installs anything. GitHub metadata uses the unauthenticated API (rate limits apply; use `--no-network` offline). Skill frontmatter parsing is minimal: it reads `name:` and `description:` from the YAML block and skips malformed entries with a warning rather than guessing.
