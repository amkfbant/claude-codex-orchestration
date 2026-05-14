#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

chmod +x .orchestration/scripts/orch.py .orchestration/scripts/install_orchestration.sh .orchestration/bin/*
python3 -m py_compile .orchestration/scripts/orch.py

append_gitignore_rules() {
  # The Python implementation updates the BEGIN/END block idempotently, including
  # .codex/config.local.toml, .codex/auth.json, .codex/sessions/, and .codex/log/.
  python3 .orchestration/scripts/orch.py init-detect --json --apply-gitignore >/dev/null
}

echo "== Codex status =="
python3 .orchestration/scripts/orch.py codex-status --suggest || true

echo
echo "== Detecting project stack =="
python3 .orchestration/scripts/orch.py init-detect --json --dry-run | tee .orchestration/init-detect.json

append_gitignore_rules

echo
echo "Review .orchestration/init-detect.json, then run:"
echo "python3 .orchestration/scripts/orch.py init --install '<install>' --lint '<lint>' --typecheck '<typecheck>' --test '<test>' --build '<build>'"
echo
echo "For v1 -> v2 migration, see .orchestration/docs/MIGRATION_v1_to_v2.md"
echo "For model selection, see .orchestration/docs/MODEL_GUIDE.md"
