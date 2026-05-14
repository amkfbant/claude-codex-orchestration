# Spec workflow

v2 makes spec-driven development mandatory for new tasks.

## Lifecycle

```text
spec_draft -> spec_review -> spec_approved -> assigned -> running -> review -> codex_review -> review -> merged
```

Backward-compatible v1 path remains available only with explicit flags:

```text
pending -> assigned -> running -> review -> merged
```

## 1. Create a task

```bash
.orchestration/bin/task-ledger new \
  --title "Add user search" \
  --objective "Implement user search by name and email" \
  --acceptance "Search returns matching active users and rejects invalid filters" \
  --paths "src/users,tests/users"
```

## 2. Create and edit the spec

```bash
.orchestration/bin/spec create <task-id> --kind feature
```

Edit:

```text
.orchestration/tasks/<task-id>/spec.md
```

The spec must contain:

- purpose
- user story
- input/output contract
- behavior specification
- non-functional requirements
- acceptance criteria checklist
- out of scope
- expected test cases

## 3. Validate and approve

```bash
.orchestration/bin/spec validate <task-id>
.orchestration/bin/spec approve <task-id>
```

## 4. Dispatch implementation

```bash
.orchestration/bin/codex-dispatch <task-id>
```

The dispatch prompt embeds the full spec. Codex must not edit the spec.

## 5. Static review

`review_diff_impl` checks:

- protected files
- unrelated paths
- dependency/lockfile changes
- test weakening
- production code changes without test changes
- heuristic acceptance-to-test coverage

## 6. Validation

Validation runs:

```text
install -> lint -> typecheck -> test -> build
```

If the test command is empty, validation records a warning.

## 7. Codex semantic review

```bash
.orchestration/bin/codex-review <task-id>
```

The reviewer receives:

- `spec.md`
- `last.patch`
- static `review.json`
- `validation.json`
- `AGENTS.md`

It must return `verdict=approve` and `ready_to_merge=true` before merge queue admission.

## 8. Merge

```bash
.orchestration/bin/merge-arbiter --cleanup
```

Merge arbiter re-checks that Codex review approval exists unless `--allow-legacy` is used for old v1 queue entries.
