---
name: codex-dispatch
description: Dispatch exactly one approved spec-backed ledger task to Codex CLI in an isolated git worktree, include spec.md in the prompt, collect artifacts, run validation, run Codex semantic review, and enqueue only approved branches for merge.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/codex-dispatch *) Bash(python3 .orchestration/scripts/orch.py dispatch *) Bash(.orchestration/bin/spec *) Bash(.orchestration/bin/codex-review *)
---

# codex-dispatch

Use this skill when a single concrete task exists in `.orchestration/ledger.json` and should be implemented by Codex CLI.

## Required precondition

New v2 tasks must have:

```text
.orchestration/tasks/<task-id>/spec.md
```

The spec must validate and be approved before dispatch:

```bash
.orchestration/bin/spec validate <task-id>
.orchestration/bin/spec approve <task-id>
```

Existing v1 tasks may be run with `--allow-legacy`. Emergency audited bypass is `--no-spec`.

## Dispatch

Single-shot mode, the default:

```bash
.orchestration/bin/codex-dispatch <task-id>
```

Test-first mode is available for feature/bugfix/behavior/api specs, but prefer the `test-first-dispatch` skill for the full workflow:

```bash
.orchestration/bin/codex-dispatch <task-id> --mode test-first
```

Optional split reviewer model:

```bash
.orchestration/bin/codex-dispatch <task-id> --review-model "<model>"
```


## Retry with structured context

When retrying after a failed attempt, attach artifacts from the previous task attempt automatically:

```bash
.orchestration/bin/codex-dispatch <task-id> --retry-context <task-id>
```

To add a human retry strategy, pass it with `--prompt-file`; the dispatcher wraps the retry data in XML tags such as `<previous_failure>` and `<retry_strategy>` while preserving the required JSON final output.

## Artifacts

Read:

```text
.orchestration/tasks/<task-id>/exit.json
.orchestration/tasks/<task-id>/review.json
.orchestration/tasks/<task-id>/validation.json
.orchestration/tasks/<task-id>/codex.review.validation.json
```

## Success

A successful dispatch ends with task status `review` and a merge queue entry only when:

- static diff review has no high severity findings
- validation passed
- Codex semantic review returned `verdict=approve` and `ready_to_merge=true`

## Failure

Do not manually patch application code. Narrow the spec, split the task, create a fix-up task, or rerun dispatch according to `CLAUDE.md`.

## v3.0 dispatch modes

Single-phase dispatch remains the default:

```bash
.orchestration/bin/codex-dispatch <task-id>
```

For behavior-changing feature, bugfix, API, or behavior specs, use test-first mode when requested:

```bash
.orchestration/bin/codex-dispatch <task-id> --mode test-first
```

Use `--pause` to stop after Phase 1 failing tests. For retry, keep using structured context:

```bash
.orchestration/bin/codex-dispatch <task-id> --retry-context <task-id>
```
