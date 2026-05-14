# Design decisions

## v1 decisions retained

### Use `codex exec` wrappers instead of direct raw calls

Adopted: Claude calls `.orchestration/bin/*` wrappers, which call `codex exec` internally.

Rejected alternative: allow Claude to run raw `codex exec`.

Reason: wrappers enforce timeout, worktree isolation, JSONL capture, final JSON validation, static review, semantic review, and merge queue admission. Raw calls are too easy to run without the guardrails.

### Use `mkdir` atomic locks instead of `flock`

Adopted: lock directories under `.orchestration/locks/<resource>.lock`.

Rejected alternative: `flock`-only implementation.

Reason: `mkdir` atomic locking works on macOS and Linux/WSL without relying on util-linux semantics. The PID/host metadata supports stale detection.

### Use git worktree per task

Adopted: `1 task = 1 branch = 1 worktree`.

Rejected alternative: multiple Codex processes sharing the same working tree.

Reason: worktrees isolate filesystem writes and make rollback/cleanup task-scoped.

## v2 decisions

### Spec-driven development is mandatory

Adopted: every new implementation task needs `.orchestration/tasks/<task-id>/spec.md` before dispatch.

Rejected alternative: prompt-only requirements.

Reason: long-running multi-agent work loses context. A per-task spec is durable, reviewable, and can be fed to both implementation Codex and reviewer Codex. Existing v1 tasks remain supported with `--allow-legacy`.

### Tests are mandatory for behavior changes

Adopted: production code changes without test changes are high severity unless spec frontmatter `kind` is `refactor`, `docs`, or `config`.

Rejected alternative: warning-only test omission.

Reason: warning-only policies are easy to ignore in autonomous runs. The high-severity threshold prevents silent behavior changes without test coverage while preserving exceptions for non-behavior changes.

### Acceptance-to-test mapping is heuristic

Adopted: compare acceptance criterion tokens with changed test files and report missing coverage as heuristic findings.

Rejected alternative: require exact traceability IDs in every test name.

Reason: exact IDs are stronger but too invasive for brownfield repositories. The heuristic gives useful signal without forcing existing test naming schemes.

### Codex semantic review runs as a separate process

Adopted: after implementation, a second Codex process runs in `read-only` mode with a review-only prompt and schema.

Rejected alternative: Claude alone performs the semantic review.

Reason: independent review catches implementation blind spots and produces machine-checkable JSON. Claude still arbitrates final strategy, but the implementation worker does not self-approve.

### Semantic reviewer uses read-only sandbox

Adopted: `codex exec --sandbox read-only --ask-for-approval never --json`.

Rejected alternative: workspace-write review mode.

Reason: review must not modify files. Read-only limits accidental edits while still allowing repository inspection and command execution that does not write.

### `orchestration-init` is one skill plus one backend command

Adopted: `.claude/skills/orchestration-init` calls `orch.py init-detect` and `orch.py init`.

Rejected alternative: split into `claude-init` and `codex-init`.

Reason: Claude-side settings, Codex-side AGENTS.md, schemas, gitignore, ledger, and skills are one consistency boundary. Splitting would create mismatched versions and partial installs.

### `orchestration-init` is idempotent and brownfield-safe

Adopted: detect existing files, append `.gitignore` with markers, and report collisions instead of overwriting.

Rejected alternative: overwrite templates every time.

Reason: existing repositories often have handcrafted `CLAUDE.md`, `AGENTS.md`, or settings. The init flow must propose diffs and preserve current work.

### MCP is still a future option, not the default path

Adopted: continue using shell wrappers around `codex exec`.

Rejected alternative: register Codex as a Claude MCP server by default.

Reason: the wrapper path gives direct OS-level control over worktrees, PID timeout, stdout/stderr artifacts, and merge queue state. Codex MCP support is useful for future interactive tool integration, but the long-running autonomous workflow needs process supervision first.


## v2.1 polish decisions

### Hand-rolled validators are the runtime source of truth

Adopted: `validate_spec_file` and `validate_codex_review` are the runtime source of truth for validation, while `spec.schema.json` and `codex-review.schema.json` are documentation and tool-integration contracts that must be kept in sync.

Rejected alternative: install and invoke `jsonschema` at runtime.

Reason: the orchestration kit must remain stdlib-only and offline-safe. Maintenance rule: when a schema changes, update the corresponding hand-rolled validator in the same commit and review both files together. Future option: add `jsonschema` as an optional dev dependency for CI-only contract tests.

### `.claude/settings.json` is not deep-merged automatically

Adopted: initialization presents diffs and lets Claude/user merge settings intentionally.

Rejected alternative: mechanically union `allow`, `ask`, and `deny` arrays during init.

Reason: a silent union can loosen permissions without an operator noticing. Permission changes are security-sensitive, so v2.1 keeps interactive diff review as the safe default.

### Partial XML prompt boundaries

Adopted: long or boundary-sensitive prompt sections use XML-style tags with CDATA, while short objective/acceptance text, fixed role text, and hard constraints remain Markdown/plain text. Codex output remains JSON only via `--output-schema`.

Rejected alternative: full XML for every prompt field.

Reason: full XML would add noise to short, already-clear fields and make prompts harder to read. Partial XML gives durable boundaries for specs, patches, retry context, and parallel-worker contracts without rewriting the whole prompt language.

Rejected alternative: keep every prompt as Markdown fences.

Reason: patches and Markdown files can themselves contain code fences, which weakens section boundaries for reviewer prompts. XML-style tags plus CDATA make boundaries explicit.

### Review patch truncation is explicit

Adopted: v2.1 records truncation metadata in `review-exit.json` and tells the reviewer to request changes when the omitted diff is needed.

Rejected alternative: silently truncate at a fixed byte limit.

Reason: silent truncation can produce false approvals. An explicit attribute and notice make the incompleteness visible and auditable.

## v3.0 decisions

### Metrics are generated from files, not a database

Adopted: `stats` reads `ledger.json`, `progress.jsonl`, and task artifacts. Rejected: a SQLite or external dashboard backend. Reason: the kit remains portable, inspectable, and recoverable in a single repository.

### Disaster recovery replays progress.jsonl but does not overwrite ledger.json by default

Adopted: `rebuild-ledger` writes `ledger.rebuilt.json` and backs up the existing ledger. Rejected: automatic destructive replacement. Reason: recovery output may lack fields such as `touched_paths` if older events did not record them.

### Spec history stores approved versions beside the task

Adopted: `spec.v<N>.md` files live next to `spec.md`. Rejected: global history database. Reason: task-local artifacts are easiest to inspect and copy with the task directory.

### Test-first mode uses two independent `codex exec` processes in the same worktree

Adopted: Phase 1 and Phase 2 are separate Codex invocations connected by git commits and file contents. Rejected: `codex exec resume` as the default. Reason: explicit phase prompts are easier to audit, while the worktree and commits preserve the required state.

### Phase 1 failure is necessary but not sufficient

Adopted: Phase 1 requires changed test files plus a failing test command and static/semantic review. Rejected: exit code non-zero alone. Reason: unrelated failures can create false confidence.

### Manager lock is advisory

Adopted: manager lock warns but does not block dispatch. Rejected: hard lock around all dispatch and merge operations. Reason: stale locks and solo recovery workflows should not block local development; task locks and merge queue locks still protect critical sections.

### Lessons require user approval

Adopted: lessons are append-only but user-approved. Rejected: automatic lesson generation on every blocked task. Reason: unreviewed lessons can pollute future prompts and create stale guidance.

## v3.0 metrics summary

Adopted: derive lightweight metrics from `ledger.json`, `progress.jsonl`, and task artifacts. Rejected: a database-backed dashboard. The file-based dashboard is sufficient for solo local operation and keeps the kit portable.

## v3.0 disaster recovery

Adopted: replay `progress.jsonl` into `ledger.rebuilt.json`. Rejected: overwriting `ledger.json` automatically. Recovery must be inspectable because not all fields are recoverable from progress events.

## v3.0 spec history

Adopted: keep `spec.md` as current and archive approved versions as `spec.v<N>.md`. Rejected: changing the spec schema shape. Optional `spec_history` on the ledger preserves backward compatibility.

## v3.0 session summary

Adopted: `resume-session` remains mechanical repair and `summarize-session` produces narrative context. Rejected: replacing resume-session with narrative output, because repair and handoff have different audiences.

## v3.0 diff-size warning

Adopted: medium severity warnings for large code/test diffs. Rejected: hard rejection by size alone. Large diffs can be legitimate, but manager should split them when possible.

## v3.0 manager lock

Adopted: advisory `manager.lock`. Rejected: hard dispatch lock. A stale hard lock can halt recovery; advisory locking gives Claude situational awareness without making the system brittle.

## v3.0 audit log

Adopted: duplicate high-risk events into `.orchestration/audit.jsonl`. Rejected: moving all progress events to audit. Audit should stay focused and small.

## v3.0 test-first dispatch

Adopted: two consecutive `codex exec` runs in the same worktree, with git commits separating Phase 1 tests and Phase 2 implementation. Rejected: using `codex exec resume` for Phase 2. Separate exec calls avoid hidden transcript dependence; the worktree and artifacts provide explicit continuity.

## v3.0 lessons learned

Adopted: user-approved, append-only `.orchestration/LEARNED.md` with prompt injection cap. Rejected: automatic lesson creation on every escalation. Automatic lessons accumulate garbage and can mislead future workers.

## v3.0 F10 Codex project configuration integration

### Project-scoped `.codex/config.toml` template

Adopted: include a commented `.codex/config.toml` template in the kit and never edit `~/.codex/config.toml`.

Rejected alternative: automatically modify the user's home-level Codex configuration.

Reason: project defaults belong in the repository, while personal credentials and preferences belong outside version control. Editing the home config silently can affect unrelated projects.

### Model names are not hard-coded

Adopted: the template and docs use `<model-name>` placeholders and explain how to inspect current availability.

Rejected alternative: hard-code a currently popular model name in the kit.

Reason: model availability changes over time and differs by subscription/workspace. The orchestration code should not become stale just because a model catalog changes.

### Trust state is best-effort

Adopted: `codex-status` reports `trust_state` using a local heuristic and marks it `unknown` when it cannot prove trust.

Rejected alternative: pretend trust can be determined exactly without a documented CLI query.

Reason: official docs confirm project config is loaded only for trusted projects, but a stable `codex config trust list` style query was not found. Operators should run `codex` once in the repo when trust is unknown.

### Effective model is metadata, not a scheduling constraint

Adopted: `exit.json` and `review-exit.json` record the resolved or inferred effective model.

Rejected alternative: fail dispatch when the effective model is unknown.

Reason: Codex built-in defaults are valid. The metadata is for audit/reproducibility, not a hard requirement.

### Profiles are surfaced but not deeply resolved

Adopted: if `--profile` is used, runtime metadata records `profile-controlled:<profile>` unless a direct `--model` is present.

Rejected alternative: fully emulate Codex's profile merge algorithm.

Reason: profile semantics are experimental and can change. The kit records that a profile controlled the run without pretending to know the full effective configuration.
