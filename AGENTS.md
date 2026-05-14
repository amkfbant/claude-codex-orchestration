# AGENTS.md

This file provides instructions for AI coding agents working in this repository.

## Project overview

Repository type: `<single-repo-or-monorepo>`

Single-repository fields:

```text
Language(s): <TypeScript/Python/Go/Rust/Java/etc>
Runtime(s): <Node.js/Python/Go/etc>
Package manager(s): <pnpm/npm/yarn/bun/pip/poetry/uv/cargo/go>
Framework(s): <Next.js/FastAPI/Rails/etc>
Test framework(s): <Vitest/Jest/Pytest/Go test/etc>
Build system: <Make/Bazel/etc>
```

Monorepo fields:

```text
Workspace tool: <pnpm workspaces/Nx/Turborepo/Rush/Cargo workspace/Go work/etc>
Packages/services: <packages/*, apps/*, services/*>
Root commands: <root-level commands>
Package-scoped commands: <how to run tests for one package>
Shared configs: <schemas, generated files, API contracts>
```

## Required commands

Run the relevant commands before finishing. Replace placeholders during project setup.

```bash
# Install dependencies, if needed
<install-command>

# Lint
<lint-command>

# Typecheck
<typecheck-command>

# Test
<test-command>

# Build
<build-command>
```

If a command is not applicable, explain why in your final JSON response. If the test command is missing, explicitly report that test execution could not be verified.

## Spec-first workflow

Every implementation task is governed by:

```text
.orchestration/tasks/<task-id>/spec.md
```

Before editing code:

1. Read `AGENTS.md`.
2. Read the full `spec.md` embedded in the dispatch prompt.
3. Implement exactly the spec and do not expand scope.
4. Treat `spec.md` and `.orchestration/**` as read-only.
5. If the spec is ambiguous, stop and report the ambiguity instead of guessing.

Spec frontmatter `kind` controls test expectations:

- `feature`, `bugfix`, `behavior`, `api`, `security`, `performance`: tests are required.
- `refactor`, `docs`, `config`: tests may be omitted only when the spec states no behavior change.

## Test policy

Tests are mandatory for new or changed behavior.

You must:

- Add or update tests for every behavior change.
- Map each acceptance criterion in `spec.md` to at least one test or explain why it is impossible.
- Run the relevant test command and report the result.

You must not:

- Omit tests for production code changes.
- Remove assertions to make tests pass.
- Add `.skip`, `.only`, `xit`, or equivalent unless the task explicitly requires test quarantine.
- Delete failing tests instead of fixing code.
- Change expected values just to hide a bug.

## Coding conventions

- Keep changes minimal and focused on the assigned task.
- Preserve public APIs unless the spec explicitly requires changing them.
- Prefer existing project patterns over introducing new abstractions.
- Do not introduce new dependencies unless the spec explicitly requires it.
- If a dependency is necessary, explain package name, version, rationale, and risks.

## Files and directories you must not modify

Do not modify these paths:

```text
.orchestration/**
.env
.env.*
secrets/**
*.pem
*.key
*.p12
*.pfx
.git/**
.github/workflows/**
CODEOWNERS
```

Do not read or print secret values. If a secret-like value is accidentally encountered, stop and report that a secret was encountered without copying it.

## Git rules

You are working in an isolated git worktree created by Claude Code.

Branch naming:

```text
codex/<task-id>-<slug>
```

Commit message format:

```text
chore(codex): complete <task-id> <slug>
```

Forbidden git commands:

```bash
git push --force
git push -f
git reset --hard
git filter-branch
git filter-repo
git rebase -i
```

Do not push to remotes. Do not rewrite history.

## Communication protocol with Claude manager

Claude manages tasks through:

```text
.orchestration/tasks/<task-id>/
```

You must not write to `.orchestration/` directly. The manager wrapper records:

```text
spec.md
prompt.md
codex.stdout.jsonl
codex.stderr.log
codex.final.json
exit.json
review.json
validation.log
validation.json
last.patch
codex.review.final.json
```

Your implementation-mode final response must be JSON:

```json
{
  "summary": "What changed and why",
  "changed_files": ["path/to/file"],
  "commands_run": [
    {"command": "npm test", "exit_code": 0}
  ],
  "risks": ["Any remaining risk or empty array"],
  "ready_for_review": true
}
```

## Review mode protocol

When launched as a semantic reviewer, do not edit files. Review the supplied spec, patch, static review, validation result, and AGENTS.md. Return only JSON:

```json
{
  "verdict": "approve",
  "summary": "Review summary",
  "findings": [
    {"severity": "medium", "category": "test_coverage", "file": "src/example.ts", "line": 12, "message": "Missing edge-case test"}
  ],
  "spec_drift": [],
  "missing_tests": [],
  "ready_to_merge": true
}
```

Use `request_changes` when the implementation is directionally correct but needs fixes. Use `reject` when it is unsafe, unrelated, or materially violates the spec.

## Parallel worktree rules

- Treat your current worktree as the only writable workspace.
- Do not edit files outside the current worktree.
- Do not use another task's branch or worktree.
- Do not use shared ports without checking.
- Do not modify shared resources unless assigned:
  - package manifests
  - lockfiles
  - migrations
  - OpenAPI / Swagger schemas
  - GraphQL / Prisma schemas
  - CI workflows
  - repository-wide config

If your task unexpectedly requires a shared resource, stop after making the minimal change and report it clearly.

## Long-running task checkpoints

For long tasks:

1. Make the smallest coherent change first.
2. Run the narrowest relevant test.
3. Continue iteratively.
4. Do not leave broad exploratory edits.
5. If stuck, report what you tried, the exact error, likely cause, and next proposed approach.

## Failure behavior

Stop and report instead of guessing when:

- Required project command is missing.
- The task needs credentials.
- The task needs production network access.
- You find conflicting architecture rules.
- You would need to modify protected files.
- Tests fail for reasons unrelated to your change.
- You cannot determine the correct API from local sources.
- The spec and existing code disagree.

Never install global packages. Never run destructive shell commands. Never hide failing tests by changing tests.

## Learned lessons

Read `.orchestration/LEARNED.md` when present. The manager also injects recent lessons into your prompt inside `<learned_lessons>`. Treat these as project-specific traps to avoid.

## Test-first mode

When the manager dispatches you in test-first mode:

### Phase 1

- Write tests only.
- Do not implement production code.
- The test command is expected to fail because implementation is absent.
- Do not edit `.orchestration/` or the spec.

### Phase 2

- Implement code to satisfy the Phase 1 tests and the spec.
- Do not rewrite Phase 1 tests except minimal non-behavioral adjustments requested by the manager.
- Preserve the test intent. Do not weaken tests, skip tests, delete assertions, or change expectations simply to pass.

## Learned lessons

Read `.orchestration/LEARNED.md` when it is provided in your prompt as `<learned_lessons>`. Treat it as project-specific traps to avoid. Do not edit `.orchestration/LEARNED.md`; Claude manager owns lesson authoring.

## Test-first phases

Some tasks run in test-first mode.

Phase 1:
- Write tests only.
- Do not edit production code.
- The test command is expected to fail before implementation.
- Do not use `skip`, `only`, weak assertions, or placeholder tests.

Phase 2:
- Implement the smallest production change that makes Phase 1 tests pass.
- Do not weaken Phase 1 tests.
- If a test must be adjusted, explain exactly why in final JSON.

Both phases must return JSON only.
