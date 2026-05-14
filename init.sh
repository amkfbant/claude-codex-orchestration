#!/usr/bin/env bash
# init.sh — install claude-codex-orchestration into a target project (first-time)
#
# Usage:
#   ./init.sh <target-project-dir>
#   ./init.sh <target-project-dir> --yes      # skip confirmation
#   ./init.sh <target-project-dir> --dry-run  # plan only, no writes
#
# Must be run from the cloned claude-codex-orchestration repository.
#
# This script copies the kit into a target project so that:
#   - All kit assets live under .orchestration/ (engine + docs + templates + scripts)
#     and .claude/skills/ (Claude Code requires this path).
#   - Policy templates (CLAUDE.md, AGENTS.md, .claude/settings.json,
#     .codex/config.toml, .gitignore) are placed at the top level for editing.
#   - The target project's top-level docs/, templates/, scripts/ directories
#     are NEVER touched.

set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration: which paths are engine vs policy
# ----------------------------------------------------------------------------
KIT_ROOT="$(cd "$(dirname "$0")" && pwd)"
KIT_NAME="claude-codex-orchestration"

# Engine layer (kit-owned, fully overwritable on upgrade)
ENGINE_PATHS=(
  ".orchestration/scripts"
  ".orchestration/schemas"
  ".orchestration/bin"
  ".orchestration/docs"
  ".orchestration/templates"
  ".claude/skills"
)

# Policy templates (project-owned after install, edited per-project)
POLICY_PATHS=(
  "CLAUDE.md"
  "AGENTS.md"
  ".claude/settings.json"
  ".codex/config.toml"
  ".gitignore"
)

# Runtime artifacts (created empty; project-owned forever)
RUNTIME_DIRS=(
  ".orchestration/tasks"
  ".orchestration/cache"
  ".orchestration/locks"
)

# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------
TARGET=""
ASSUME_YES=false
DRY_RUN=false

usage() {
  cat <<EOF
Usage: $0 <target-project-dir> [options]

Options:
  --yes        Skip the confirmation prompt
  --dry-run    Show the install plan without making any changes
  -h, --help   Show this message

Run this script from inside the ${KIT_NAME} repository.
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
  echo "Error: this script must be run from inside the ${KIT_NAME} repo" >&2
  echo "       Missing: ${KIT_ROOT}/.orchestration/scripts/orch.py" >&2
  exit 1
fi

if [[ ! -d "$TARGET" ]]; then
  echo "Error: target directory does not exist: $TARGET" >&2
  exit 1
fi

TARGET="$(cd "$TARGET" && pwd)"

if [[ ! -d "$TARGET/.git" ]]; then
  echo "Error: target is not a git repository: $TARGET" >&2
  echo "       Run 'git init' in the target first." >&2
  exit 1
fi

if [[ -f "$TARGET/.orchestration/scripts/orch.py" ]]; then
  echo "Error: orchestration kit appears to be already installed at $TARGET" >&2
  echo "       Use upgrade.sh to update the engine layer." >&2
  exit 1
fi

# ----------------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------------
KIT_VERSION="$(git -C "$KIT_ROOT" describe --tags --always --dirty 2>/dev/null || echo unknown)"

cat <<EOF
===================================================================
  ${KIT_NAME} install
===================================================================
Kit version : ${KIT_VERSION}
Source repo : ${KIT_ROOT}
Target proj : ${TARGET}

Engine paths to be installed (all kit assets stay under .orchestration/
and .claude/skills/; top-level docs/templates/scripts in your project
are NOT touched):
EOF
for p in "${ENGINE_PATHS[@]}"; do echo "  - $p/"; done
echo ""
echo "Policy templates to be installed (you will customize after):"
for p in "${POLICY_PATHS[@]}"; do echo "  - $p"; done
echo ""
echo "Runtime artifacts to be created (empty):"
for d in "${RUNTIME_DIRS[@]}"; do echo "  - $d/"; done
echo "  - .orchestration/progress.jsonl"
echo "  - .orchestration/audit.jsonl"
echo "  - .orchestration/merge-queue.json"
echo "  - .orchestration/LEARNED.md"
echo ""

if $DRY_RUN; then
  echo "[dry-run] no changes will be made."
  exit 0
fi

if ! $ASSUME_YES; then
  read -r -p "Proceed with install? [y/N] " ans
  case "$ans" in y|Y|yes|YES) ;; *) echo "Aborted."; exit 0 ;; esac
fi

# ----------------------------------------------------------------------------
# Copy helper
# ----------------------------------------------------------------------------
copy_path() {
  local rel="$1"
  local src="${KIT_ROOT}/${rel}"
  local dst="${TARGET}/${rel}"
  if [[ ! -e "$src" ]]; then
    echo "  [skip] $rel (not present in kit)"
    return
  fi
  mkdir -p "$(dirname "$dst")"
  if [[ -d "$src" ]]; then
    if command -v rsync >/dev/null 2>&1; then
      rsync -a "${src}/" "${dst}/"
    else
      cp -R "${src}/." "$dst"
    fi
  else
    cp "$src" "$dst"
  fi
  echo "  [ok]   $rel"
}

# ----------------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------------
echo ""
echo "Copying engine paths..."
for p in "${ENGINE_PATHS[@]}"; do copy_path "$p"; done

echo "Copying policy templates..."
for p in "${POLICY_PATHS[@]}"; do
  if [[ -e "$TARGET/$p" ]]; then
    backup="$TARGET/${p}.pre-${KIT_NAME}.$(date -u +%Y%m%d%H%M%S)"
    mv "$TARGET/$p" "$backup"
    echo "  [back] $p moved to $(basename "$backup")"
  fi
  copy_path "$p"
done

echo "Creating runtime artifacts..."
mkdir -p "$TARGET/.orchestration"
for d in "${RUNTIME_DIRS[@]}"; do
  mkdir -p "$TARGET/$d"
  touch "$TARGET/$d/.gitkeep"
done
[[ -f "$TARGET/.orchestration/progress.jsonl"   ]] || : > "$TARGET/.orchestration/progress.jsonl"
[[ -f "$TARGET/.orchestration/audit.jsonl"      ]] || : > "$TARGET/.orchestration/audit.jsonl"
[[ -f "$TARGET/.orchestration/merge-queue.json" ]] || echo "[]" > "$TARGET/.orchestration/merge-queue.json"
[[ -f "$TARGET/.orchestration/LEARNED.md"       ]] || printf '# Learned lessons\n\n' > "$TARGET/.orchestration/LEARNED.md"

echo "Setting executable bits..."
chmod +x "$TARGET/.orchestration/scripts/orch.py" 2>/dev/null || true
chmod +x "$TARGET/.orchestration/scripts/install_orchestration.sh" 2>/dev/null || true
chmod +x "$TARGET/.orchestration/bin/"* 2>/dev/null || true

echo "Writing .kit-version..."
echo "$KIT_VERSION" > "$TARGET/.kit-version"

# ----------------------------------------------------------------------------
# Post-install guidance
# ----------------------------------------------------------------------------
cat <<EOF

===================================================================
Install complete. Next steps:

1. Customize policy files for this project:
   - $TARGET/AGENTS.md             (project stack, conventions, paths)
   - $TARGET/.codex/config.toml    (uncomment 'model' if you want a project default)
   - $TARGET/.claude/settings.json (adjust permissions if your stack needs more)

2. Run orchestration init with your project commands:
   cd $TARGET
   python3 .orchestration/scripts/orch.py init \\
     --install   "<install command>" \\
     --lint      "<lint command>" \\
     --typecheck "<typecheck command>" \\
     --test      "<test command>" \\
     --build     "<build command>"

3. Verify Codex CLI preconditions:
   .orchestration/bin/codex-status

4. Commit the orchestration kit into your project's history:
   cd $TARGET
   git add -A
   git commit -m "Add ${KIT_NAME} ${KIT_VERSION}"

Kit documentation lives at $TARGET/.orchestration/docs/.
===================================================================
EOF
