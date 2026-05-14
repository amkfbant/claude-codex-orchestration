# Test-first workflow

v3.0 adds optional two-phase dispatch for behavior-changing specs.

## Applicability

Use test-first for:

- `kind: feature`
- `kind: bugfix`
- `kind: behavior`
- `kind: api`

Avoid it for pure docs, config, and mechanical refactors unless the user explicitly asks.

## State flow

```text
spec_approved
  -> phase1_running
  -> phase1_review
  -> phase1_done
  -> phase2_running
  -> phase2_review
  -> merged
```

The legacy route remains valid:

```text
spec_approved -> assigned -> running -> review -> merged
```

## Phase 1: tests

Codex receives the spec and may edit only test paths. Expected result:

- test files are added or updated
- production files are unchanged
- test command exits non-zero before implementation
- static review passes
- semantic review approves test intent, or returns request_changes without missing test findings

Commit:

```text
chore(codex): <task-id> phase1 tests
```

## Phase 2: implementation

Codex receives the spec and Phase 1 test file contents. Expected result:

- implementation changes are minimal
- Phase 1 tests are preserved
- test command exits zero
- normal static review, validation, and Codex semantic review pass

Commit:

```text
chore(codex): <task-id> phase2 implementation
```

## Artifacts

```text
.orchestration/tasks/<task-id>/phase1/
.orchestration/tasks/<task-id>/phase2/
```

Root task artifacts remain for merge-arbiter compatibility.

## Retry rules

- Phase 1 failure: retry Phase 1.
- Phase 2 failure: retry Phase 2 while preserving Phase 1 tests.
- If Phase 2 modifies Phase 1 tests, manager review is mandatory.
