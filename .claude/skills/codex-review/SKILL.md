---
name: codex-review
description: Run an independent semantic Codex review for a task in review state. Use when a task has a patch, spec.md, static diff-reviewer output, and validation results but has not yet received Codex semantic approval for merge.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/codex-review *) Bash(python3 .orchestration/scripts/orch.py codex-review *) Bash(cat .orchestration/tasks/*/codex.review.validation.json) Bash(jq *)
---

# codex-review

This skill performs semantic review. It complements, but does not replace, `diff-reviewer`.

- `diff-reviewer`: deterministic static checks for protected paths, missing tests, dependency changes, and test weakening.
- `codex-review`: independent Codex process in read-only mode that checks correctness, spec drift, test coverage, security, and merge readiness.

## Run

```bash
.orchestration/bin/codex-review <task-id>
```

Optional model split:

```bash
.orchestration/bin/codex-review <task-id> --review-model "<model>"
```

## Result

Read:

```text
.orchestration/tasks/<task-id>/codex.review.final.json
.orchestration/tasks/<task-id>/codex.review.validation.json
.orchestration/tasks/<task-id>/review-exit.json
```

Only `verdict=approve` and `ready_to_merge=true` allows merge queue admission unless the manager explicitly used an audited bypass.
