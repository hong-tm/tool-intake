<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.svg">
    <img src="assets/banner-light.svg" alt="tool-intake: check tools before adding them to your AI stack">
  </picture>
</p>

[![skills.sh](https://skills.sh/b/hong-tm/tool-intake)](https://skills.sh/hong-tm/tool-intake)

An agent skill for deciding whether a new tool, skill, or repository belongs in your agent stack, and a read-only script that gathers the evidence first.

## Install

```bash
npx skills add hong-tm/tool-intake
```

Or link this checkout into your agent's skill directory. If you use several agents or projects, keep one maintained source and enable it through scoped symlinks; see [Shared skill stores](#shared-skill-stores).

## Why

Agents and people install tools from a one-line description. That is how a stack ends up with two skills doing the same job, a project-specific skill loaded globally, and an install that breaks the host because it needed root, a browser engine, or a runtime that is not on the service PATH.

This skill replaces improvisation with a fixed procedure and an evidence step.

## What the skill enforces

1. Run the audit script and look at the evidence before judging.
2. Read the candidate's full text, never just the blurb. One-line registry descriptions mislead.
3. One copy per function. If something installed already covers it, decline or state the concrete difference.
4. Pick a scope: user-global, project-level, or decline. Store shared skills once and link only the scopes that need them.
5. Preflight compatibility: output protocol versus the viewing surface, privilege requirements, resident footprint, runtime reachability under systemd and cron.
6. Install, then verify the real loader picks the skill up with no name collisions. A directory entry is not an install.
7. Record the decision durably. Decisions that live only in chat get re-litigated.

## The audit script

`scripts/intake_audit.py` is read-only and standard-library only (Python 3.10+). It never modifies files and never installs anything.

```bash
python3 scripts/intake_audit.py https://github.com/owner/repo
python3 scripts/intake_audit.py ./local/path --keywords secret,scan,redact
python3 scripts/intake_audit.py owner/repo --roots /opt/skills
python3 scripts/intake_audit.py --keywords webhook,retry --json
python3 scripts/intake_audit.py https://github.com/owner/repo --no-network
```

It reports four things:

- **Upstream vitality**: stars, license, last push, archived flag, open issues (unauthenticated GitHub API; `--no-network` skips it).
- **Preflight red flags** in the project text: sudo or root requirements, heavy dependencies, daemons, mandatory credentials.
- **Installed-skill inventory**: auto-detected skill roots plus any `--roots` extras, including an ancestor walk from the current directory. Resolves symlinks, counts each physical `SKILL.md` once, and retains every discovered entrypoint. Reports broken links and same-name files at different real paths.
- **Duplicate-function scan**: overlap between the candidate's keywords and every installed skill's name and description, ranked `strong` / `overlap` / `weak`.

Auto-detected roots: `~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills`, `~/.config/opencode/skills`, `~/.gemini/skills`, plus `.agents/skills`, `.claude/skills`, `.codex/skills`, and `.opencode/skill` found while walking up from the current directory.

### Shared skill stores

Keep skill content in one maintained source, such as `~/.local/share/agent-skills/<name>`, and link individual skills into the roots that should load them. The store itself stays outside automatic discovery. Project-specific skills remain project-specific even when their source is shared.

```text
shared source/tool-intake/
    <- ~/.agents/skills/tool-intake
    <- ~/.claude/skills/tool-intake

shared source/support-triage/
    <- ~/support/.agents/skills/support-triage
```

The Skills CLI already uses canonical copies and symlinks within each installation scope. This additional layout shares sources across projects while keeping activation separate. It does not install a background manager or change any loader.

The JSON inventory keeps `path` and `root` for the first discovered entrypoint and adds `canonical_path` plus `entrypoints`, a list of `{path, root}` objects. Two links to the same file count as one skill. Two different files with the same skill name remain separate and produce a diagnostic.

Update the maintained source once. Avoid running an installer over a linked entrypoint: it may replace the link with a copy. Package-owned skills stay under their package manager. Home-directory links need recreation on other machines; for public projects, record a source and revision rather than relying on another user's filesystem.

See [the setup and migration guide](references/shared-store.md) for commands, backup checks, version pinning, and loader verification. The audit is read-only and does not create or move links.

### Sample output (abridged)

```text
# Tool intake audit

- Target: https://github.com/owner/candidate-tool
- Generated: 2026-10-13 00:07
- Read-only: no file was modified

## 1. Upstream vitality
- Repo: owner/candidate-tool
- Stars: 1 | Forks: 0 | Open issues: 0
- License: Apache-2.0 | Archived: no
- Created: 2026-04-30 | Last push: 2026-09-29 (14 days ago)

### Preflight red flags in project text
- credential needed: 1 kind(s) seen -> credential
- python runtime: 2 kind(s) seen -> pip install, pipx install

## 2. Installed skill inventory
- ~/.agents/skills (2 unique skill(s), 2 entrypoint(s))
- ~/.claude/skills (1 unique skill(s), 1 entrypoint(s))
- Total distinct skills: 2
- Total entrypoints: 3
- tool-intake: /home/dev/.local/share/agent-skills/tool-intake/SKILL.md
  - entrypoint: /home/dev/.agents/skills/tool-intake/SKILL.md | root: /home/dev/.agents/skills
  - entrypoint: /home/dev/.claude/skills/tool-intake/SKILL.md | root: /home/dev/.claude/skills
- secret-sweep: /home/dev/.local/share/agent-skills/secret-sweep/SKILL.md
  - entrypoint: /home/dev/.agents/skills/secret-sweep/SKILL.md | root: /home/dev/.agents/skills

## 3. Duplicate-function scan
- Keywords considered: 88 | matched: credentials, logs, redact, scan, secrets, session

| skill | root | matched tokens | strength |
|---|---|---|---|
| secret-sweep | ~/.agents/skills | credentials, logs, redact, scan, secrets, session | strong |

## 4. Verdict skeleton (fill in)
- decision: install | decline
- scope: user-global | project | archive
- reason:
- date:
```

## Layout

```text
SKILL.md
scripts/intake_audit.py
references/shared-store.md
tests/
```

Run the inventory regression tests with `python3 -B -m unittest discover -s tests -v`.

## License

MIT
