# Shared skill stores

Keep one maintained source for a skill, with individual symlinks in the agent or project roots that should load it. The source directory is storage; the links control activation. This convention uses normal directories and symlinks, not a new loader.

## Choose a source and scope

Use a directory outside automatic skill discovery, such as `~/.local/share/agent-skills/`. This path is a convention, not an agent standard. Do not register the whole store as a global skill root: doing so enables project-specific skills everywhere.

```text
~/.local/share/agent-skills/
  tool-intake/                 # maintained source or link to its checkout
  support-triage/

~/.agents/skills/
  tool-intake -> shared source

~/support/.agents/skills/
  support-triage -> shared source

~/support/.claude/skills/
  support-triage -> the same shared source
```

An agent that already discovers `.agents/skills` may need no additional tool-specific link. Check its actual loader rather than creating directories for every agent. Link the whole skill folder, including scripts and references, so relative resources remain available.

## Check external-link support

Filesystem access, loader permission, and skill activation are separate checks. A valid symlink can still be rejected when its resolved target lies outside a trusted project root. Check the installed loader's documented behavior or implementation before choosing a layout; settings and trust rules differ across tools and versions.

- If it supports external-directory permission independently of discovery, grant the smallest required target and retain the intended entrypoints.
- If a path setting also discovers skills, treat it as activation. Prefer project-scoped entries for individual skills when supported. Registering a shared store globally can expose every skill in that store to unrelated projects.
- Explicit entries and automatic discovery may both examine the same link. If necessary, use the loader's supported exclusions for those specific automatic entries while retaining the explicit entries; verify the final skill list and diagnostics rather than hiding warnings.
- If the loader cannot use external sources at the intended scope, use its supported project-local installation or a pinned copy. Record source, revision, and update ownership for any extra copy. If no supported method preserves scope, report the limitation and defer installation.

Keep tool-specific keys and commands in the installation record. Apply the real-loader and unrelated-workspace checks in Step 5 of SKILL.md to each affected tool; success in one loader does not establish compatibility with another.

## Scoped symlink example

The Skills CLI's default symlink mode already shares a canonical copy among agents within an installation scope. Its canonical directory is `~/.agents/skills` for global installs or `<project>/.agents/skills` for project installs. A cross-project store outside those roots requires an explicit source and update policy.

## Example: enable this skill from one checkout

For a new installation on a system supporting symlinks:

```sh
mkdir -p "$HOME/.local/share/agent-skills"
git clone https://github.com/hong-tm/tool-intake.git \
  "$HOME/.local/share/agent-skills/tool-intake"

# Run inside the project that should use it.
mkdir -p .agents/skills
ln -s "$HOME/.local/share/agent-skills/tool-intake" .agents/skills/tool-intake
```

For global activation, create the link in `~/.agents/skills` instead. A second project can link to the same source. The example intentionally does not overwrite an existing destination; inspect and back it up before replacing it. If symlinks are unavailable, use the agent's supported installation method and record that copies need separate updates.

## Migrate existing installations

1. Inventory source directories, links, names, and loading scope. Resolve links to distinguish aliases from physical copies. Compare same-name copies before selecting a source; preserve local changes and divergent versions.
2. Save a backup and a source-to-entrypoint manifest. Include the loaded skill names for each affected workspace. Move whole folders, checking nested symlinks and resources that depend on their old location.
3. Put maintained sources in the store and replace intended entrypoints with individual links. Keep package-managed skills in their packages. Preserve enabled scopes unless a scope change is explicitly part of the task.
4. Run the audit and each available real loader. Confirm names and scopes match the baseline, aliases count once, relative resources work, and name collisions or broken links are explained. Restore from the backup if the loader cannot follow the links.

The audit inventories paths; it cannot prove how every agent loads them. An explicit `--roots` pointing at inactive storage adds inventory visibility, not activation.

## Update and portability

- Update the maintained source once, then verify the affected entrypoints. For a clean Git checkout following its upstream branch, `git -C <source> pull --ff-only` is one update method. Check local changes first and record the revision.
- Do not blindly run an installer over linked entrypoints. An installer may remove the link and copy a new directory in its place. Stage and review downloaded updates at the source, preserving local changes and checking any installer lockfile against the chosen ownership model.
- Shared mutable sources update all consumers together. Projects needing different versions should link to separate pinned checkouts and record that deliberate exception.
- A symlink records a path, not the target files. Home-directory links do not travel with a public repository. Record the source and revision in a setup manifest and recreate links on each machine, or use a reproducible project-local installation for collaborators and CI.
- Moving credentials, hooks, or service configuration is outside this layout. Only skill folders and their required resources belong in the store.
