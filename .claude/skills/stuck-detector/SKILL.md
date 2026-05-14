---
name: stuck-detector
description: Detect repeated failure fingerprints across Codex attempts and trigger strategy change or user escalation based on retry thresholds.
allowed-tools: Read Grep Glob Bash(.orchestration/bin/stuck-detector *) Bash(python3 .orchestration/scripts/orch.py stuck-check *)
---

# stuck-detector

Use this skill after any failed Codex dispatch or failed merge validation.

## Procedure

```bash
.orchestration/bin/stuck-detector --strategy-after 2 --escalate-after 4
```

## Interpretation

- Same failure count >= 2: change strategy.
- Same failure count >= 4: mark blocked and ask user.
- Security findings: escalate immediately.
