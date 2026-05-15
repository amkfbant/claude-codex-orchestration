# Parallel sessions and parallel dispatch

This kit supports two layers of parallelism on a single machine:

1. **Multiple Claude Code manager sessions** running on the same project simultaneously.
2. **Parallel Codex worker dispatches** within a single Claude session.

Both are coordinated via process-level `fcntl.flock` locks on state files. Locks are advisory, single-machine only, and released automatically when a process exits.

## How concurrent state mutations are coordinated

The orch.py engine acquires a state lock around every read-modify-write sequence on:

- `.orchestration/ledger.json`
- `.orchestration/merge-queue.json`
- `.orchestration/LEARNED.md`
- `.orchestration/progress.jsonl` (atomic appends, lock used for serialization)

Lock files live in `.orchestration/locks/<name>.flock`. They are empty marker files; the lock state is held in the kernel, not the file contents.

Acquisition is re-entrant within a single process — nested `state_lock(same name)` returns immediately without touching the kernel lock. Across processes the lock is exclusive.

The save path for `ledger.json` re-reads the on-disk ledger under the lock and merges so tasks created by other sessions are preserved when this session writes its own tasks. Top-level fields (`commands`, `settings`) follow last-write-wins.

## Recommended pattern for a monorepo with multiple workspaces

- Open one Claude session per workspace (e.g., one terminal for `apps/web`, one for `apps/api`).
- Designate one session as the "merge driver" — only that session runs `merge-arbiter --cleanup`. Other sessions do dispatch and review only.
- Keep parallel dispatch counts within `ledger.settings.max_parallel_dispatches` (default 5).
- Use `.orchestration/bin/session-list` to see who is doing what.

## Parallel dispatch flow

To dispatch multiple tasks concurrently from one session, use the `parallel-codex` skill or run manually:

```
# 1. Warm install cache in main worktree (no Codex dispatched).
.orchestration/bin/codex-dispatch --warm-install-only

# 2. Dispatch parallel workers, each in its own worktree.
for tid in T-abc T-def T-ghi; do
  .orchestration/bin/codex-dispatch "$tid" --skip-install &
done
wait

# 3. Review each result independently.
for tid in T-abc T-def T-ghi; do
  .orchestration/bin/codex-review "$tid" &
done
wait

# 4. Single-threaded merge from one session only.
.orchestration/bin/merge-arbiter --cleanup
```

`--warm-install-only` runs the configured install command in the main worktree and writes a cache stamp. Subsequent `--skip-install` workers detect the stamp via `install_cache_key` and skip install entirely.

## Diagnostics

- `.orchestration/bin/session-list` — recent sessions and their event counts.
- `.orchestration/bin/lock-status` — which state locks are currently held.
- `.orchestration/bin/stuck-detector` (or `orch.py stuck-check`) — flags running tasks whose owning session has been silent for more than `--dead-session-minutes` (default 30).
- `.orchestration/bin/stats` — includes `rate_limit_hits` summed across all task `exit.json` files.

## Limits

- Single machine only. Cross-machine concurrency is out of scope; use git-based collaboration (PRs) for multi-developer scenarios.
- Codex CLI auth (`~/.codex/auth.json`) is shared across sessions on the same user. Parallel calls may trigger concurrent token refresh; Codex handles this internally.
- Codex API rate limits are a real ceiling. Watch `rate_limit_hits` in `stats`. When you see rate-limit failures, lower `max_parallel_dispatches` or stagger dispatches.
- Windows is not supported (`fcntl.flock` does not exist there).

## Failure modes

- **Stuck task after session crash**: a Claude session that crashes mid-dispatch leaves its task in `running` status with `dispatch_session` set to that session. `stuck-check --dead-session-minutes 30` will flag these. Recover with `task-ledger set-status <id> failed`.
- **Lock acquisition timeout**: if `state_lock` waits more than `timeout` seconds (default 30s), it raises `TimeoutError`. This means another process is holding the lock for an unreasonably long time. Diagnose with `lock-status` and inspect process state via `lsof` / `fuser` on the `.flock` path.
- **Concurrent merge**: prevented by the merge-queue lock, but if two sessions try simultaneously, one waits up to the lock timeout. Convention: only one session runs merge-arbiter.

## Migration from single-session

No action required. Existing `ledger.json` and `progress.jsonl` formats are fully compatible; new fields (`dispatch_session`, structured `actor` dicts, `max_parallel_dispatches`) are optional and additive. Legacy `actor` string values are normalized at read time by `normalize_actor()`.

The `manager-lock` / `manager-unlock` / `manager-status` CLI commands are retained as deprecated no-ops; `manager-status` is an alias of `session list`. The advisory `manager.lock` file is no longer authoritative.
