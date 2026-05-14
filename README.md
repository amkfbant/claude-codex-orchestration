# Claude Code × Codex CLI Orchestration Kit

**English** | [日本語](README.ja.md)

A scaffolding kit that lets [Claude Code](https://docs.claude.com/en/docs/claude-code) orchestrate [Codex CLI](https://developers.openai.com/codex) as an implementation worker. Claude plans, writes specs, reviews diffs, and merges. Codex CLI implements in isolated git worktrees under sandboxed file access. All state lives in your repository, so you can resume any time and recover from crashes.

Designed for solo developers running structured, auditable work over long sessions on a single machine.

## Why use this

- **Separation of roles.** Claude is good at planning, reading code, and judgment calls. Codex CLI is good at focused, single-task implementation. This kit makes them collaborate without overlap.
- **Spec-driven.** Every implementation task starts with a `spec.md` you approve before any code is written. No "I'll just have it figure it out" surprises.
- **Two-stage review.** A second Codex process reviews each patch in read-only mode before it reaches the merge queue. You see both static and semantic findings.
- **State-driven, not session-driven.** All ledger, progress, audit, and lesson data lives in files. Stop and resume anytime. `rebuild-ledger` can replay everything from the event log.
- **Self-contained per project.** Everything lives under `.orchestration/` and `.claude/skills/` in your project. No global config. Your `docs/`, `scripts/`, and `templates/` directories are never touched.
- **Test-first option.** Use `--mode test-first` for tasks that benefit from writing failing tests before implementation. Two isolated phases enforce the discipline.
- **Lessons learned.** Approved traps and pitfalls accumulate in `LEARNED.md` and are automatically injected into Codex prompts, so the same mistakes don't repeat.

## Prerequisites

- Python 3.10+ (3.11+ recommended for native `tomllib`)
- Git
- Bash 3.2+
- [Claude Code](https://docs.claude.com/en/docs/claude-code) installed and authenticated
- [Codex CLI](https://developers.openai.com/codex) installed and authenticated (`codex login`)

## Installation

The kit is scaffolding — `init.sh` copies files into your project's git history. Updates happen via `upgrade.sh`, which only touches the engine layer and leaves your customized policy files alone.

```bash
# 1. Clone the kit (anywhere you like — the scripts resolve their own location)
git clone <kit-url>
cd claude-codex-orchestration

# 2. Install into your project (creates a backup of any existing AGENTS.md/CLAUDE.md)
./init.sh /path/to/your-project

# 3. Customize policy files for your project
cd /path/to/your-project
$EDITOR AGENTS.md            # fill in your stack, conventions, paths
$EDITOR .codex/config.toml   # optionally uncomment `model = "..."`

# 4. Tell orchestration about your project's commands
python3 .orchestration/scripts/orch.py init \
  --install   "pnpm install" \
  --lint      "pnpm lint" \
  --typecheck "pnpm typecheck" \
  --test      "pnpm test" \
  --build     "pnpm build"

# 5. Verify Codex CLI is ready
.orchestration/bin/codex-status --suggest

# 6. Commit
git add -A
git commit -m "Add claude-codex-orchestration"
```

Use `./init.sh /path/to/your-project --dry-run` to preview the install without making changes.

**About the kit's location.** The kit can live anywhere (`~/repos/`, `~/.local/share/`, `/opt/`, or even a temporary clone). `init.sh` and `upgrade.sh` resolve their own path, and the installed project does not reference the original kit directory afterward — you can delete the clone right after `init.sh` if you want. The only reason to keep the clone around is so you can `git pull` and run `upgrade.sh` later. For convenience, this README assumes you keep the clone somewhere you'll remember.

## What gets installed

After install, the kit-owned paths in your project look like:

```
your-project/
├── .orchestration/              # all kit assets live here
│   ├── scripts/                 # orch.py engine + install_orchestration.sh
│   ├── bin/                     # thin wrappers: codex-dispatch, codex-review, …
│   ├── schemas/                 # JSON schemas for specs, ledger, codex output
│   ├── docs/                    # kit documentation
│   ├── templates/               # spec.md and lesson templates
│   ├── tasks/                   # per-task working dirs (one per ID)
│   ├── ledger.json              # task state (created on first `init`)
│   ├── progress.jsonl           # full event log
│   ├── audit.jsonl              # security-sensitive events
│   ├── merge-queue.json         # serialized merge queue
│   └── LEARNED.md               # approved lessons, injected into Codex prompts
├── .claude/
│   ├── settings.json            # Claude Code permissions
│   └── skills/                  # orchestration skills for Claude Code
├── .codex/config.toml           # project-scoped Codex settings
├── AGENTS.md                    # YOU customize this for your project
├── CLAUDE.md                    # manager policy (Claude reads this)
└── .kit-version                 # installed version marker
```

Your project's existing `docs/`, `scripts/`, and `templates/` directories at the top level are not modified.

## Daily workflow

The basic loop for one task:

```bash
# 1. Create a task entry
.orchestration/bin/task-ledger new \
  --title "Add JWT refresh token endpoint" \
  --objective "Allow clients to exchange a refresh token for a new access token" \
  --paths "src/auth/refresh.ts,tests/auth/refresh.test.ts"
# → returns task-id like T20260514210816-1e38a2

# 2. Author the spec
.orchestration/bin/spec create <task-id> --kind feature
$EDITOR .orchestration/tasks/<task-id>/spec.md
.orchestration/bin/spec validate <task-id>
.orchestration/bin/spec approve <task-id>

# 3. Dispatch — Codex implements in an isolated worktree
.orchestration/bin/codex-dispatch <task-id>

# 4. Semantic review by a second Codex process
.orchestration/bin/codex-review <task-id>

# 5. Merge — serialized, with validation rollback on failure
.orchestration/bin/merge-arbiter --cleanup
```

For test-first tasks where you want failing tests written before any implementation:

```bash
.orchestration/bin/codex-dispatch <task-id> --mode test-first
```

Phase 1 writes the tests (which must fail), Phase 2 implements until they pass. Phase 2 is forbidden from modifying Phase 1 tests.

## Common operations

```bash
# Status overview
.orchestration/bin/task-ledger list
.orchestration/bin/stats --format text
.orchestration/bin/stats --format html --output stats.html   # standalone dashboard

# Codex environment check
.orchestration/bin/codex-status
.orchestration/bin/codex-status --suggest      # actionable suggestions

# Session resume
.orchestration/bin/manager-status
python3 .orchestration/scripts/orch.py summarize-session

# Stuck tasks
.orchestration/bin/stuck-detector

# Recovery
python3 .orchestration/scripts/orch.py rebuild-ledger --dry-run

# Lessons
.orchestration/bin/lesson list
.orchestration/bin/lesson add --task <task-id> \
  --context "pnpm workspace" --trap "peer-dep conflicts in parallel install" \
  --lesson "lockfile updates must be single-dispatch"
```

## Updating the kit

From wherever you keep the kit clone:

```bash
cd /path/to/claude-codex-orchestration
git pull
./upgrade.sh /path/to/your-project --dry-run    # preview changes
./upgrade.sh /path/to/your-project              # apply
```

`upgrade.sh` replaces only the engine layer (`.orchestration/{scripts,schemas,bin,docs,templates}` and `.claude/skills/`). Your `AGENTS.md`, `CLAUDE.md`, `.claude/settings.json`, `.codex/config.toml`, and all runtime data (`ledger.json`, `progress.jsonl`, `audit.jsonl`, `LEARNED.md`, `tasks/`) are never modified. Policy diffs are printed for your manual review.

After upgrade, inspect the engine diff in your project:

```bash
cd /path/to/your-project
git status
git diff -- .orchestration .claude/skills
```

## Documentation

After installing into a project, the full documentation lives under `.orchestration/docs/`:

- `ORCHESTRATION_OPERATIONS.md` — full operational reference
- `SPEC_WORKFLOW.md` — how to write good specs
- `STATE_MACHINE.md` — task state transitions
- `TEST_FIRST_WORKFLOW.md` — two-phase dispatch
- `MODEL_GUIDE.md` — choosing Codex models
- `PROMPT_STYLE.md` — XML prompt boundary conventions
- `FAILURE_MODES.md` — common failures and recovery
- `DISASTER_RECOVERY.md` — full crash recovery
- `DESIGN_DECISIONS.md` — why things are the way they are
- `CHANGELOG.md` — version history

## Compatibility notes

- The kit is designed for **single-machine, single-developer** workflows. Multi-developer or remote-coordination scenarios are out of scope.
- `manager.lock` is **advisory only** — it warns if a second Claude session may be running on the same project, but does not enforce mutual exclusion. The actual critical sections (per-task lock, merge queue lock) use atomic primitives.
- Codex CLI model availability changes over time. This kit deliberately does not hard-code model names. Use `codex-status` to inspect what model your environment resolves to, and configure it in `.codex/config.toml` per project.

## Troubleshooting

**`codex-status` reports `trust_state: unknown`.**
Codex CLI requires you to "trust" a project before reading its `.codex/config.toml`. Run `codex` once in the project directory and accept the trust prompt, then re-check.

**`init.sh` aborts with "orchestration kit appears to be already installed".**
Use `upgrade.sh` instead. `init.sh` is for first-time installs only.

**Engine upgrade left stale files behind.**
`upgrade.sh` does not delete files that exist in the target but not in the new kit version, to protect any custom skills you may have added under `.claude/skills/`. Use `git status` in the target project to find stale files and remove them manually.

**Codex commits "produce no changes".**
Check `.orchestration/tasks/<task-id>/exit.json` for `pre_head` and the actual diff. The dispatch records the pre-dispatch HEAD so commits made by Codex remain visible. If still empty, inspect `codex.stdout.jsonl` for tool calls.

For more failure scenarios, see `.orchestration/docs/FAILURE_MODES.md` and `.orchestration/docs/DISASTER_RECOVERY.md` after installing.

## License

[License to be specified by the kit author.]
