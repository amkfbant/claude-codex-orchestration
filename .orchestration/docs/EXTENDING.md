# Extending the orchestration kit

This kit ships with a fixed set of engine files (`.orchestration/scripts/`, `.orchestration/bin/`, `.orchestration/schemas/`, `.orchestration/templates/`, `.orchestration/docs/`) and a fixed set of skills under `.claude/skills/`. Those are owned by the upstream kit and will be **replaced in place** by `upgrade.sh`. Anything you customise inside those paths can be overwritten on the next upgrade.

This document explains where to put project-specific customisations so they survive `upgrade.sh` and never collide with future kit releases.

## Adding project-specific skills

Claude Code loads skills from `<repo>/.claude/skills/<name>/SKILL.md`. Subdirectory grouping is not supported — every skill must be a direct child directory of `.claude/skills/`. To keep project skills visually separated from kit skills:

**Use a prefix in the directory name.** Recommended:

- `proj-<name>` — generic, works for any project (e.g. `proj-deploy-runner`)
- `<project-name>-<purpose>` — if your project has a short name (e.g. `myapp-deploy-runner`)

Kit skills use a verb+noun pattern (`codex-dispatch`, `spec-author`, `merge-arbiter`). Avoid these names. Using a `proj-` prefix guarantees no collision now or in future kit versions.

### Procedure

```
cd /path/to/your-project
mkdir -p .claude/skills/proj-deploy-runner
$EDITOR .claude/skills/proj-deploy-runner/SKILL.md
```

Minimum SKILL.md template:

```
---
name: proj-deploy-runner
description: |
  When the user asks about deploying, releasing, or pushing to production,
  follow this skill's procedure to verify env, migrations, and smoke tests
  before any irreversible action.
---

# proj-deploy-runner

## When to use this skill
- The user mentions "deploy", "release", or "push to prod".
- A task spec declares `kind: deploy` or touches `infra/`.

## Procedure

1. Verify required env vars are set in `.env.production`.
2. Confirm pending migrations are documented in `migrations/CHANGELOG.md`.
3. Run smoke tests: `pnpm smoke:prod`.
4. Only then proceed with deployment commands.
```

The `description` field is what Claude Code uses to decide when to load this skill, so write concretely about triggers.

Commit:

```
git add .claude/skills/proj-deploy-runner/
git commit -m "Add proj-deploy-runner skill"
```

### Upgrade safety

`upgrade.sh` uses `rsync -a` without `--delete` for `.claude/skills/`. Project skills that the kit does not know about are preserved across upgrades. After `./upgrade.sh /path/to/your-project`, verify with:

```
cd /path/to/your-project
ls .claude/skills/proj-*
git status
```

To preview what an upgrade will touch without applying:

```
./upgrade.sh /path/to/your-project --dry-run
```

## Other extension points

### `AGENTS.md` — project context

Your primary place for project-specific information. Document the stack, conventions, commands, monorepo layout, code style, sensitive paths. Claude reads this on every manager session. This is a policy file and is never modified by `upgrade.sh`.

### `CLAUDE.md` — manager allow-list

Add project-specific paths to the "Claude may directly edit" or "Claude must never edit" lists if your project has unusual structure. Keep additions minimal; most project context belongs in `AGENTS.md`.

### `.claude/settings.json` — Claude Code permissions

If your stack requires bash patterns the kit doesn't allow by default (`gradle build`, `make deploy`, etc.), add them to `permissions.allow`. Policy file, never overwritten by upgrade.

### Project tooling — your own scripts

Do not place custom scripts under `.orchestration/bin/`. That directory is engine-owned and reserved for kit wrappers. Put project utilities in your project's existing `scripts/`, `bin/`, or equivalent location, and reference them from `AGENTS.md` so Claude knows they exist.

### Claude Code hooks

The kit installs a PreToolUse hook via `orch.py hook-pretool` (declared in `.claude/settings.json`). To add your own project hook, write a script in your project's tooling directory and declare it in `.claude/settings.json` alongside the kit's hook. Both hooks will run.

### Codex CLI profiles

Define alternate model configurations in `.codex/config.toml` for specific dispatch purposes:

```
model = "<your-default>"

[profiles.review]
model = "<reviewer-model>"

[profiles.fast]
model = "<faster-cheaper-model>"
```

Dispatch with the profile: `codex-dispatch <task-id> --profile fast`.

## What you must NOT modify

These files are engine-owned. `upgrade.sh` replaces them in place. Local modifications will be lost on the next upgrade:

- `.orchestration/scripts/orch.py` — main engine
- `.orchestration/scripts/install_orchestration.sh` — install helper
- `.orchestration/bin/*` — wrapper scripts
- `.orchestration/schemas/*` — JSON schemas
- `.orchestration/templates/*` — spec and lesson templates
- `.orchestration/docs/*` — kit documentation
- Kit-shipped skills under `.claude/skills/` (any directory not using your project prefix)

If you find yourself wanting to modify any of these, prefer one of these instead:

- Open an issue / propose a kit change upstream.
- Wrap the behavior in a new project skill that calls the kit's commands and adds your logic around them.
- Write a separate project script that wraps the kit's CLI (`orch.py` or `.orchestration/bin/*`).

## Cheat sheet

| Need | Where it goes | Upgrade-safe? |
|------|---------------|---------------|
| New procedural skill | `.claude/skills/proj-*/SKILL.md` | Yes (rsync no-delete) |
| Project stack / commands / conventions | `AGENTS.md` | Yes (policy file) |
| Manager allow-list additions | `CLAUDE.md` | Yes (policy file) |
| Bash permission additions | `.claude/settings.json` | Yes (policy file) |
| Model profile additions | `.codex/config.toml` | Yes (policy file) |
| Custom tooling scripts | Your project's `scripts/` or `bin/` | Yes (outside kit namespace) |
| Custom Claude Code hooks | Project script, referenced from `.claude/settings.json` | Yes |
| Engine modifications | DON'T — propose upstream instead | No (will be overwritten) |

## Multi-session safety

This kit is safe to run from multiple Claude sessions on the same project on the same machine. See `PARALLEL_SESSIONS.md` for workflow patterns and diagnostic tools.
