# Migration: v1 to v2

This migration is backward-compatible. Existing task IDs, ledger shape, bin names, and merge queue format are preserved.

## 1. Backup current orchestration state

```bash
cp -a .orchestration .orchestration.v1.backup.$(date -u +%Y%m%d%H%M%S)
cp CLAUDE.md CLAUDE.md.v1.backup 2>/dev/null || true
cp AGENTS.md AGENTS.md.v1.backup 2>/dev/null || true
```

## 2. Overlay v2 files

Copy the v2 kit files into the repository root.

```bash
rsync -a ./claude-codex-orchestration/ ./
chmod +x .orchestration/scripts/orch.py .orchestration/bin/*
```

## 3. Check syntax and detection

```bash
python3 -m py_compile .orchestration/scripts/orch.py
python3 .orchestration/scripts/orch.py init-detect --json
```

## 4. Re-run idempotent init

Use the commands recommended by `init-detect`, or keep your existing commands.

```bash
python3 .orchestration/scripts/orch.py init \
  --install "<existing install command>" \
  --lint "<existing lint command>" \
  --typecheck "<existing typecheck command>" \
  --test "<existing test command>" \
  --build "<existing build command>"
```

This appends the `.gitignore` marker block if missing and preserves existing ledger data.

## 5. Finish in-flight v1 tasks

For existing tasks without `spec.md`, use explicit legacy flags:

```bash
.orchestration/bin/codex-dispatch <task-id> --allow-legacy
.orchestration/bin/merge-arbiter --allow-legacy --cleanup
```

Prefer finishing in-flight tasks before creating new v2 tasks.

## 6. Start all new tasks with specs

```bash
.orchestration/bin/task-ledger new \
  --title "<title>" \
  --objective "<objective>" \
  --acceptance "<acceptance>" \
  --paths "<paths>"

.orchestration/bin/spec create <task-id> --kind feature
.orchestration/bin/spec validate <task-id>
.orchestration/bin/spec approve <task-id>
.orchestration/bin/codex-dispatch <task-id>
```

## 7. Verify v2 artifacts

```bash
ls .orchestration/schemas/spec.schema.json
ls .orchestration/schemas/codex-review.schema.json
ls .claude/skills/orchestration-init/SKILL.md
ls .claude/skills/spec-author/SKILL.md
ls .claude/skills/codex-review/SKILL.md
```

## Rollback

```bash
rm -rf .orchestration
mv .orchestration.v1.backup.<timestamp> .orchestration
mv CLAUDE.md.v1.backup CLAUDE.md 2>/dev/null || true
mv AGENTS.md.v1.backup AGENTS.md 2>/dev/null || true
```
