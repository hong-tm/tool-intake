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

Or, if your agent's loader permits it, link this checkout into its skill directory. If you use several agents or projects, keep one maintained source and enable it at the intended scope; see [Shared skill stores](#shared-skill-stores).

## Why

Agents and people install tools from a one-line description. That is how a stack ends up with two skills doing the same job, a project-specific skill loaded globally, and an install that breaks the host because it needed root, a browser engine, or a runtime that is not on the service PATH.

This skill replaces improvisation with a fixed procedure and an evidence step.

## What the skill enforces

1. Run the audit script and look at the evidence before judging.
2. Read the candidate's full text, never just the blurb. One-line registry descriptions mislead.
3. One copy per function. If something installed already covers it, decline or state the concrete difference.
4. Pick a scope: user-global, project-level, or decline. Store shared skills once and link only the scopes that need them.
5. Preflight compatibility: output protocol versus the viewing surface, privilege requirements, resident footprint, runtime reachability under systemd and cron.
6. Install, then verify the real loader picks the skill up with no name collisions or access blocks, and unrelated workspaces gain no unintended skills. A general health check is insufficient unless it verifies actual skill loading.
7. Record the decision durably. Decisions that live only in chat get re-litigated.

## The audit script

`scripts/intake_audit.py` is read-only and standard-library only (Python 3.10+). It never modifies files and never installs anything.

```bash
python3 scripts/intake_audit.py https://github.com/owner/repo
python3 scripts/intake_audit.py ./local/path --keywords secret,scan,redact
python3 scripts/intake_audit.py https://github.com/owner/repo --roots /opt/skills
python3 scripts/intake_audit.py --keywords webhook,retry --json
python3 scripts/intake_audit.py https://github.com/owner/repo --no-network
```

It reports:

- **Upstream vitality**: stars, license, last push, archived flag, open issues (unauthenticated GitHub API; `--no-network` skips it).
- **Preflight mentions** in the project text: privileges, heavy dependencies, daemons, credentials, and runtimes. The report labels these hits `red flags`. Read the context to decide whether a mention is an actual requirement.
- **Installed-skill inventory**: auto-detected skill roots plus any `--roots` extras, including an ancestor walk from the current directory. Resolves symlinks, counts each physical `SKILL.md` once, and retains every discovered entrypoint. Reports broken links and same-name files at different real paths.
- **Overlap candidates**: keywords from the full retrieved text compared with each installed skill's name and description. Rarity-weighted scores set reading order, and each row includes the skill's source path. The calling agent compares actual behavior before deciding whether functions overlap.
- **Content evidence**: document sources, UTF-8 byte lengths, and SHA-256 hashes; installed `SKILL.md` hashes and same-name content comparisons. Equal skill-file hashes do not prove that bundled scripts and references match.

For reproducible local evidence, supply explicit roots and an audit date:

```bash
python3 scripts/intake_audit.py ./candidate \
  --only-roots --roots ./installed-skills \
  --as-of 2026-10-14 --json --strict
```

Use the intended paths and date. `--only-roots` disables automatic discovery and requires at least one `--roots`. Supply one directory per `--roots`, repeating the option for more roots. Identical local files, root order, arguments, and date produce identical JSON. GitHub responses remain live and can change.

The JSON report includes a `schema_version` and `collection_status`. `complete` means evidence collection completed; `partial` flags missing evidence or inventory diagnostics; `inventory_only` means no candidate was requested. None of these approves installation. `--strict` returns nonzero when requested evidence is incomplete or inventory diagnostics exist. The default exit status remains compatible with exploratory use. See `--help` for argument details.

Schema version 2 removes the heuristic `strength` labels and adds overlap `canonical_path` and `agent_review`. Every candidate report has `agent_review.status = required` and `decision = null`, even with complete collection or no lexical matches. Inventory-only reports use `not_requested`. The script returns this evidence on stdout and makes no model calls.

The calling agent reviews the JSON with the user's intended use and environment, reads the source documents, and investigates uncertain findings. It returns an install / decline / defer recommendation with source-backed reasons, scope, and open questions. Missing evidence that could change the outcome means defer with a next check. Jev or a similar available tool may assist; when unavailable, skip it and let the calling agent complete the review directly. See SKILL.md for the review handoff and installation verification steps.

The GitHub path scans the repository README. Local directories scan root README variants and `SKILL.md`; a file target is scanned once. Read candidate skill bodies separately when they were not among the reported documents. The script never installs, moves skills, or writes a decision for you.

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

Some loaders block project links to external sources. A setting that accepts a directory may also load every skill it contains. Check the target loader's capabilities and preserve scope using separate access permission, explicit project entries, or a supported project-local installation; see [external-link compatibility](references/shared-store.md#check-external-link-support).

The JSON inventory keeps `path` and `root` for the first discovered entrypoint and adds `canonical_path` plus `entrypoints`, a list of `{path, root}` objects. Two links to the same file count as one skill. Two different files with the same skill name remain separate and produce a diagnostic.

Update the maintained source once. Avoid running an installer over a linked entrypoint: it may replace the link with a copy. Package-owned skills stay under their package manager. Home-directory links need recreation on other machines; for public projects, record a source and revision rather than relying on another user's filesystem.

See [the setup and migration guide](references/shared-store.md) for commands, backup checks, version pinning, and loader verification. The audit is read-only and does not create or move links.

### Sample output (abridged)

Home paths are shortened and the middle of the inventory is elided.

```text
# Tool intake audit

- Target: https://github.com/hong-tm/tool-intake
- Generated: 2026-10-14
- Collection status: complete
- Read-only: no file was modified

## 1. Upstream vitality
- Repo: hong-tm/tool-intake
- Stars: 0 | Forks: 0 | Open issues: 0
- License: MIT | Archived: no
- Created: 2026-10-12 | Last push: 2026-10-14 (0 days ago)
- Description: Agent skill: intake procedure and read-only audit script before adding a new tool or skill to an agent stack.

### Preflight red flags in project text
- root/sudo required: 1 kind(s) seen -> sudo
- daemon/service install: 1 kind(s) seen -> systemd
- credential needed: 1 kind(s) seen -> credential
- python runtime: 2 kind(s) seen -> pip install, pipx install
- Evidence: https://raw.githubusercontent.com/hong-tm/tool-intake/main/README.md | UTF-8 bytes: 6770 | SHA-256: 4882a4cfb73c075f3be67d469e27c4b97a714668c16f103cfcf746217187eb02

## 2. Installed skill inventory

- /home/dev/.agents/skills (30 unique skill(s), 30 entrypoint(s))
- /home/dev/.claude/skills (32 unique skill(s), 32 entrypoint(s))
- /home/dev/.codex/skills (0 unique skill(s), 0 entrypoint(s))
- /home/dev/.config/opencode/skills (1 unique skill(s), 1 entrypoint(s))
- Total distinct skills: 37
- Total entrypoints: 63
- asd-ste100: /home/dev/.local/share/agent-skills/asd-ste100/SKILL.md | SHA-256: b81a65a55f002b8ce04f48c0b66b101b84b77eadb6c7c6deffccfe56c603c9fa
  - entrypoint: /home/dev/.agents/skills/asd-ste100/SKILL.md | root: /home/dev/.agents/skills
- codebase-design: /home/dev/.local/share/agent-skills/codebase-design/SKILL.md | SHA-256: 2c20617f87ec8af6a434859f381b2f061a69b530444e74eb39e78bb016a6d1e2
  - entrypoint: /home/dev/.agents/skills/codebase-design/SKILL.md | root: /home/dev/.agents/skills
  - entrypoint: /home/dev/.claude/skills/codebase-design/SKILL.md | root: /home/dev/.claude/skills
- ... (remaining skills and entrypoints omitted in this sample)

## 3. Duplicate-function scan

- Keywords considered: 320 | top matched tokens: across, copies, credentials, enable, entrypoints, guide, install, installed, logs, overlap, protocol, python, redact, scan, scope, secret, secret-sweep, secrets, separate, session, through, whether
- Rarity-weighted scores order reading only; the agent determines functional overlap from source evidence

| skill | source | matched tokens | matched | score |
|---|---|---|---|---|
| tool-intake | /home/dev/tool-intake/SKILL.md | across, copies, entrypoints, install, installed, overlap, scope, separate | 20 | 0.179 |
| secret-sweep | /home/dev/.local/share/agent-skills/secret-sweep/SKILL.md | credentials, logs, redact, scan, secret, secret-sweep, secrets, session | 8 | 0.118 |
| mcp-builder | /home/dev/chat/.agents/skills/mcp-builder/SKILL.md | protocol, python, enable, guide, whether, through | 6 | 0.056 |

## 4. Agent review handoff

- Review status: required
- Decision: unset; collection status and scores never approve or reject installation
- Return this report to the calling agent with the user's intended use and target environment
- The agent reads sources, resolves uncertainty, and returns install / decline / defer with evidence and open questions
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
