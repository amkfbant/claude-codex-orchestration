---
name: resume-session
description: Restore orchestration state after Claude session interruption by reading ledger, progress log, merge queue, task artifacts, locks, and worktrees in order.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/resume-session *) Bash(.orchestration/bin/summarize-session *) Bash(python3 .orchestration/scripts/orch.py resume-check *) Bash(git status *) Bash(git worktree list *)
---

# resume-session

Use this skill at the beginning of a resumed Claude session.

## Procedure

1. Read:
   - `CLAUDE.md`
   - `AGENTS.md`
   - `.orchestration/ledger.json`
   - `.orchestration/progress.jsonl`
   - `.orchestration/merge-queue.json`
2. Run:
   ```bash
   .orchestration/bin/resume-session --repair
   ```
3. Run:
   ```bash
   .orchestration/bin/stuck-detector
   ```
4. For conversational resume, also read a narrative summary:
   ```bash
   .orchestration/bin/summarize-session --since 7d
   ```
5. Summarize:
   - merged
   - review
   - failed
   - blocked
   - pending
   - merge queue
   - next safest action

## v3.0 narrative resume

After mechanical repair, also read a human-oriented session summary:

```bash
.orchestration/bin/summarize-session --since 7d --max-events 200
```

`resume-session` restores state; `summarize-session` explains what happened and what to do next.
