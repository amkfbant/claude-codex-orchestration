# Changelog

## v3.3 — Multi-session safety and parallel dispatch hardening

- Added `fcntl.flock`-based state locks around every read-modify-write of `ledger.json`, `merge-queue.json`, `LEARNED.md`, and `progress.jsonl`. Re-entrant within a single process; exclusive across processes.
- `save_ledger()` now re-reads the on-disk ledger under the lock and merges so concurrent edits to other tasks made by parallel sessions are preserved.
- Strengthened task ID format: `T<YYYYMMDDhhmmssffffff>-<pid4>-<rand4>` for collision-free parallel task creation. Legacy IDs continue to parse and are kept as-is.
- Added session identity (`session` UUID, `pid`, `host`, `user`) to every event in `progress.jsonl`. Legacy string-form `actor` values are normalized at read time.
- New `session list` command (`.orchestration/bin/session-list`) lists sessions seen in `progress.jsonl`.
- Retired `manager-lock` / `manager-unlock` (subsumed by per-state-file locks). They remain as deprecated no-ops. `manager-status` is now an alias of `session list`. The advisory `manager.lock` file is cleaned up by `manager-unlock` for migration.
- Added `--warm-install-only` and `--skip-install` to `codex-dispatch` for install-cache pre-warming in parallel dispatch flows.
- Added `max_parallel_dispatches` ledger setting (default 5). Dispatch refuses to start when the running-task count reaches this cap.
- Added rate-limit detection in dispatch `exit.json` (`rate_limit_hit: true`); summed as `rate_limit_hits` in `stats`.
- Added `lock-status` (`.orchestration/bin/lock-status`) diagnostic.
- `stuck-check` now flags running tasks whose owning session has been silent for more than `--dead-session-minutes` (default 30).
- New documentation: `PARALLEL_SESSIONS.md`. Updated `STATE_MACHINE.md`, `ORCHESTRATION_OPERATIONS.md`, `EXTENDING.md`, both READMEs.

Backward compatible. Existing `ledger.json` and `progress.jsonl` formats are accepted as-is. Older events with string `actor` values are normalized at read time. Legacy task IDs (`T<14digits>-<6hex>`) continue to work.

## v3.2 — Polish

- Compressed `.gitignore` task-runtime patterns into `tasks/*/**` with `spec.md` / `spec.v*.md` whitelist.
- Added `EXTENDING.md` documenting project-specific skill naming conventions and extension points.
- Cross-linked from README and `ORCHESTRATION_OPERATIONS.md`.
