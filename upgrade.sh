#!/usr/bin/env bash
# upgrade.sh — refresh the engine layer of claude-codex-orchestration in a target project.
#
# Engine paths under .orchestration/ (scripts, schemas, bin, docs, templates) and
# .claude/skills/ are replaced. Policy files (CLAUDE.md, AGENTS.md, settings.json,
# .codex/config.toml) are NEVER touched. Differences against the new templates are
# shown for manual review. Runtime artifacts (ledger.json, progress.jsonl,
# audit.jsonl, LEARNED.md, tasks/, cache/, locks/) are never touched.
#
# The target project's top-level docs/, templates/, and scripts/ directories
# are NEVER touched. The kit only owns paths under .orchestration/ and .claude/.
#
# Usage:
#   ./upgrade.sh <target-project-dir>
#   ./upgrade.sh <target-project-dir> --yes      # skip confirmation
#   ./upgrade.sh <target-project-dir> --dry-run  # plan only, no writes
#
# Must be run from the cloned claude-codex-orchestration repository.

set -euo pipefail

KIT_ROOT="$(cd "$(dirname "$0")" && pwd)"
KIT_NAME="claude-codex-orchestration"

ENGINE_PATHS=(
  ".orchestration/scripts"
  ".orchestration/schemas"
  ".orchestration/bin"
  ".orchestration/docs"
  ".orchestration/templates"
  ".claude/skills"
)

POLICY_PATHS=(
  "CLAUDE.md"
  "AGENTS.md"
  ".claude/settings.json"
  ".codex/config.toml"
)

NEVER_TOUCH_RUNTIME=(
  ".orchestration/ledger.json"
  ".orchestration/progress.jsonl"
  ".orchestration/audit.jsonl"
  ".orchestration/merge-queue.json"
  ".orchestration/manager.lock"
  ".orchestration/LEARNED.md"
  ".orchestration/tasks"
  ".orchestration/cache"
  ".orchestration/locks"
)

TARGET=""
ASSUME_YES=false
DRY_RUN=false

usage() {
  cat <<EOF
Usage: $0 <target-project-dir> [options]

Options:
  --yes        Skip the confirmation prompt
  --dry-run    Show what would change without modifying anything
  -h, --help   Show this message

Run from the cloned ${KIT_NAME} repository. Policy files and runtime
data are never modified.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)     ASSUME_YES=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    --*)       echo "Unknown option: $1" >&2; usage; exit 2 ;;
    *)
      if [[ -z "$TARGET" ]]; then TARGET="$1"; shift
      else echo "Unexpected argument: $1" >&2; usage; exit 2
      fi ;;
  esac
done

[[ -n "$TARGET" ]] || { usage; exit 2; }

# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------
if [[ ! -f "${KIT_ROOT}/.orchestration/scripts/orch.py" ]]; then
  echo "Error: must be run from inside the ${KIT_NAME} repository" >&2
  exit 1
fi

if [[ ! -d "$TARGET" ]]; then
  echo "Error: target directory does not exist: $TARGET" >&2
  exit 1
fi
TARGET="$(cd "$TARGET" && pwd)"

if [[ ! -f "$TARGET/.orchestration/scripts/orch.py" ]]; then
  echo "Error: no orchestration kit detected in target." >&2
  echo "       Use init.sh for first-time install." >&2
  exit 1
fi

CURRENT_VERSION="$(cat "$TARGET/.kit-version" 2>/dev/null || echo unknown)"
NEW_VERSION="$(git -C "$KIT_ROOT" describe --tags --always --dirty 2>/dev/null || echo unknown)"

# ----------------------------------------------------------------------------
# Plan and policy diff
# ----------------------------------------------------------------------------
cat <<EOF
===================================================================
  ${KIT_NAME} upgrade
===================================================================
Source repo  : ${KIT_ROOT}
Target proj  : ${TARGET}
Current ver  : ${CURRENT_VERSION}
Incoming ver : ${NEW_VERSION}

Engine layer to be REPLACED (project-owned files in these paths
will be overwritten; runtime files outside these paths are untouched):
EOF
for p in "${ENGINE_PATHS[@]}"; do echo "  - $p/"; done
echo ""
echo "Policy files to be DIFFED ONLY (NOT modified):"
for p in "${POLICY_PATHS[@]}"; do echo "  - $p"; done
echo ""
echo "Runtime artifacts that are guaranteed UNTOUCHED:"
for p in "${NEVER_TOUCH_RUNTIME[@]}"; do echo "  - $p"; done
echo ""

echo "============= POLICY DIFFS (informational, not applied) ============="
policy_has_changes=false
for p in "${POLICY_PATHS[@]}"; do
  if [[ -f "$KIT_ROOT/$p" && -f "$TARGET/$p" ]]; then
    if diff -q "$KIT_ROOT/$p" "$TARGET/$p" >/dev/null 2>&1; then
      echo "  [same] $p"
    else
      echo "  [diff] $p"
      policy_has_changes=true
    fi
  elif [[ -f "$KIT_ROOT/$p" && ! -f "$TARGET/$p" ]]; then
    echo "  [new]  $p — exists in kit, missing in target"
    policy_has_changes=true
  fi
done
echo "====================================================================="
echo ""

if $DRY_RUN; then
  echo "[dry-run] no changes will be made."
  exit 0
fi

if ! $ASSUME_YES; then
  read -r -p "Proceed with engine upgrade? [y/N] " ans
  case "$ans" in y|Y|yes|YES) ;; *) echo "Aborted."; exit 0 ;; esac
fi

# ----------------------------------------------------------------------------
# Engine replace
#
# rsync runs without --delete: stale files from older versions are left in
# place rather than silently nuked. This protects any custom files a user
# may have added under .claude/skills/. Inspect via `git status` after upgrade
# to find and remove stale kit files manually if needed.
# ----------------------------------------------------------------------------
echo ""
echo "Replacing engine paths..."
for p in "${ENGINE_PATHS[@]}"; do
  src="${KIT_ROOT}/${p}"
  dst="${TARGET}/${p}"
  if [[ ! -e "$src" ]]; then
    echo "  [skip]     $p (missing in kit)"
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "${src}/" "${dst}/" 2>/dev/null || rsync -a "$src" "$(dirname "$dst")/"
  else
    cp -R "${src}/." "$dst"
  fi
  echo "  [upgraded] $p"
done

chmod +x "$TARGET/.orchestration/scripts/orch.py" 2>/dev/null || true
chmod +x "$TARGET/.orchestration/scripts/install_orchestration.sh" 2>/dev/null || true
chmod +x "$TARGET/.orchestration/bin/"* 2>/dev/null || true

echo "$NEW_VERSION" > "$TARGET/.kit-version"

# ----------------------------------------------------------------------------
# Quick sanity: orch.py compiles
# ----------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
  if python3 -m py_compile "$TARGET/.orchestration/scripts/orch.py" 2>/dev/null; then
    echo "  [ok]       python3 -m py_compile passed"
  else
    echo "  [WARN]     python3 -m py_compile failed; inspect immediately" >&2
  fi
fi

# ----------------------------------------------------------------------------
# Post-upgrade guidance
# ----------------------------------------------------------------------------
cat <<EOF

===================================================================
Engine upgraded: ${CURRENT_VERSION} -> ${NEW_VERSION}

Recommended next steps:

1. Inspect the engine diff in the target project:
     cd $TARGET
     git status
     git diff -- .orchestration .claude/skills

2. Consider adopting policy improvements (NOT applied automatically):
EOF

if $policy_has_changes; then
  for p in "${POLICY_PATHS[@]}"; do
    if [[ -f "$KIT_ROOT/$p" && -f "$TARGET/$p" ]] && ! diff -q "$KIT_ROOT/$p" "$TARGET/$p" >/dev/null 2>&1; then
      echo "     diff -u $TARGET/$p $KIT_ROOT/$p"
    elif [[ -f "$KIT_ROOT/$p" && ! -f "$TARGET/$p" ]]; then
      echo "     # consider adopting: cp $KIT_ROOT/$p $TARGET/$p"
    fi
  done
else
  echo "     (no policy template changes detected)"
fi

cat <<EOF

3. Refresh .gitignore patterns if the kit added new ones (idempotent):
     cd $TARGET && bash .orchestration/scripts/install_orchestration.sh 2>/dev/null \\
       || echo "(if install_orchestration.sh refuses to run, check its arguments)"

4. Commit the upgrade:
     cd $TARGET
     git add -A
     git commit -m "Upgrade ${KIT_NAME} ${CURRENT_VERSION} -> ${NEW_VERSION}"
===================================================================
EOF
