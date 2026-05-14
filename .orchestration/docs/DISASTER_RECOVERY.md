# Disaster recovery

`orch.py rebuild-ledger` rebuilds `.orchestration/ledger.json` from append-only `progress.jsonl`.

## Commands

Preview:

```bash
.orchestration/bin/rebuild-ledger --dry-run
```

Write a rebuilt copy:

```bash
.orchestration/bin/rebuild-ledger
```

Custom output:

```bash
.orchestration/bin/rebuild-ledger --output .orchestration/ledger.rebuilt.json
```

The command does not overwrite `ledger.json`. If `ledger.json` exists, it creates `ledger.json.backup.<timestamp>`.

## Event replay coverage

| Event | Recoverable fields |
|---|---|
| `task.created` | id, title, created_at, dependencies/touched_paths when present |
| `task.status` | status, last_reason, updated_at |
| `codex.dispatch.start` | attempts, branch, worktree, running status |
| `merge.queue.added` | review status, branch |
| `merge.success` | merged status, merged_at |
| `merge.conflict` | blocked status, reason |
| `validation.failed` | failed status |
| `diff.review.rejected` | failed status |
| `spec.approved` | spec_history entry when event data has version/path |

## Not fully recoverable

These may be empty or approximate if not present in progress events:

- objective
- acceptance
- touched_paths
- shared_resources
- timeout/budget settings
- detailed artifacts
- full dependency graph

Use the rebuilt ledger as a recovery aid, not as a perfect forensic record.
