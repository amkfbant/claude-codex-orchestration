---
name: test-first-dispatch
description: Dispatch a feature, bugfix, behavior, or API task in two Codex phases: Phase 1 writes failing tests only, Phase 2 implements code to make those tests pass, with separated artifacts and semantic review.
allowed-tools: Read Bash(.orchestration/bin/codex-dispatch *--mode test-first*) Bash(python3 .orchestration/scripts/orch.py dispatch *--mode test-first*) Bash(.orchestration/bin/stats *) Bash(.orchestration/bin/summarize-session *)
---

# test-first-dispatch

Use this skill when a spec declares `mode: test-first`, or when the user explicitly asks for test-first / TDD dispatch.

## Procedure

1. Ensure the task has an approved spec and at least one test path in `touched_paths`.
2. Dispatch:

```bash
.orchestration/bin/codex-dispatch <task-id> --mode test-first
```

3. To pause after tests are written:

```bash
.orchestration/bin/codex-dispatch <task-id> --mode test-first --pause
```

4. Inspect artifacts:

```text
.orchestration/tasks/<task-id>/phase1/
.orchestration/tasks/<task-id>/phase2/
```

Phase 1 must add tests and the test command should fail before implementation. Phase 2 must make the tests pass without weakening Phase 1 tests.
