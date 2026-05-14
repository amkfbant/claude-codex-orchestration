---
name: orchestration-init
description: Initialize the complete Claude Code × Codex CLI orchestration kit for either a new or existing project. Use this when the user asks to set up, bootstrap, migrate, or safely add orchestration: it checks Codex CLI preconditions and effective model resolution, initializes Claude-side files (CLAUDE.md, settings.json, skills), Codex-side AGENTS.md and .codex/config.toml, shared ledger/.orchestration/.gitignore, runs stack auto-detection, reports collisions, and performs safe idempotent merge planning.
allowed-tools: Read Grep Glob Bash(codex --version) Bash(codex login status) Bash(python3 .orchestration/scripts/orch.py init-detect *) Bash(python3 .orchestration/scripts/orch.py init *) Bash(python3 .orchestration/scripts/orch.py codex-status *) Bash(.orchestration/bin/orchestration-init *) Bash(.orchestration/bin/codex-status *) Bash(.orchestration/bin/manager-status *) Bash(git diff *) Bash(git status *) Bash(jq *)
---

# orchestration-init

Use this skill to initialize the whole orchestration kit. Do not split this into Claude-only or Codex-only initialization because the manager instructions, Codex instructions, permissions, ledger, schemas, `.codex/config.toml`, and gitignore rules must remain consistent.

## Procedure

0. Check whether another Claude manager may already be operating:

```bash
.orchestration/bin/manager-status
```

If an active manager lock exists, warn the user before changing orchestration state.

## 0. Codex CLI preconditions

Before stack detection, verify Codex CLI is installed and authenticated, and report the effective model resolution.

```bash
codex --version
codex login status
python3 .orchestration/scripts/orch.py init-detect --json --dry-run | jq '.codex_preconditions'
# or
.orchestration/bin/codex-status --suggest
```

Report to the user:

- Is Codex installed? Authenticated?
- Which model will be used by default for `codex-dispatch` from `effective_model` and `effective_model_source`?
- If `effective_model_source == "codex-builtin"` and the user has preferences, propose adding `.codex/config.toml` with a project-scoped `model` value.
- If `trust_state != "trusted"` or is `unknown`, instruct the user to run `codex` once in this directory and accept the trust prompt so `.codex/config.toml` is loaded.

Do not edit `~/.codex/config.toml` or `.codex/config.toml` automatically. Show a copy-paste snippet if the user wants to set a project-level default.

1. Detect the repository. For exploratory first runs, prefer `--dry-run` so no `.orchestration/` files or audit events are created:

```bash
python3 .orchestration/scripts/orch.py init-detect --json --dry-run
```

2. When ready to record detection in the audit log, rerun without `--dry-run` if desired:

```bash
python3 .orchestration/scripts/orch.py init-detect --json
```

3. Summarize:

- Codex CLI preconditions and effective model source
- detected stacks
- monorepo markers
- existing file collisions
- recommended `install/lint/typecheck/test/build` commands
- shared/protected pattern suggestions

4. Ask the user to confirm or edit the recommended commands.

5. After confirmation, initialize idempotently:

```bash
python3 .orchestration/scripts/orch.py init \
  --install "<install>" \
  --lint "<lint>" \
  --typecheck "<typecheck>" \
  --test "<test>" \
  --build "<build>"
```

6. If existing `CLAUDE.md`, `AGENTS.md`, `.codex/config.toml`, or `.claude/settings.json` exists, do not overwrite blindly. Show `git diff` and propose a merge.

7. For `.gitignore`, rely on the orchestration BEGIN/END marker block; repeated runs are safe.

## Brownfield mode

For an existing repository, do not touch application code. Only add or update orchestration files and keep the initial base ref at `HEAD`.

## v3.0 manager lock check

At the start of initialization or re-initialization, check for another active manager:

```bash
.orchestration/bin/manager-status
```

If a valid manager lock exists, warn the user before making orchestration changes. The lock is advisory and does not block dispatch.
