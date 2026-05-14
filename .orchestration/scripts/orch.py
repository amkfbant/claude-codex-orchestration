#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import fnmatch
import difflib
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Tuple

STATUS = {
    "spec_draft",
    "spec_review",
    "spec_approved",
    "pending",
    "assigned",
    "running",
    "review",
    "codex_review",
    "phase1_running",
    "phase1_review",
    "phase1_done",
    "phase2_running",
    "phase2_review",
    "merged",
    "failed",
    "blocked",
}

# Shared resources are forced through serial execution because they create semantic
# conflicts even when git can merge them. CI workflow files are intentionally also
# listed in PROTECTED_PATTERNS below: normal Codex tasks must not touch them, and
# the shared-resource rule is a fail-safe for explicitly authorized CI tasks.
SHARED_PATTERNS = [
    r"(^|/)package\.json$",
    r"(^|/)(pnpm-lock\.yaml|package-lock\.json|yarn\.lock|bun\.lockb|npm-shrinkwrap\.json)$",
    r"(^|/)(Cargo\.lock|go\.sum|poetry\.lock|uv\.lock|Pipfile\.lock)$",
    r"(^|/)(requirements(-.*)?\.txt|constraints(-.*)?\.txt)$",
    r"(^|/)migrations?/",
    r"(^|/)db/migrate/",
    r"(^|/)(openapi|swagger)(\..*)?\.(json|ya?ml)$",
    r"(^|/)schema\.(graphql|json|prisma)$",
    r"(^|/)\.github/workflows/",
]

PROTECTED_PATTERNS = [
    r"(^|/)\.orchestration/",
    r"(^|/)\.env($|\.)",
    r"(^|/)secrets?/",
    r"(^|/)\.git/",
    r"(^|/)\.github/workflows/",
    r"(^|/)CODEOWNERS$",
]

TEST_PATH_RE = re.compile(
    r"(^|/)(test|tests|__tests__)/|\.(test|spec)\.(js|jsx|ts|tsx|py|go|rs|java|kt|rb)$"
)

PRODUCTION_PATH_RE = re.compile(
    r"^(src|app|apps|packages|services|libs|lib|cmd|internal|pkg)/"
    r"|\.(js|jsx|ts|tsx|py|go|rs|java|kt|rb|php|cs|swift|dart|ex|exs)$"
)

SPEC_BYPASS_KINDS = {"refactor", "docs", "config"}
SPEC_REQUIRED_KINDS = {"feature", "bugfix", "behavior", "api", "security", "performance"}

DANGEROUS_BASH_PATTERNS = [
    r"\bgit\s+push\b.*(\s-f\b|--force|--mirror|\+:)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+filter-(branch|repo)\b",
    r"\brm\s+-rf\s+(/|~|\$HOME|\.|\.\.|\*)",
    r"\b(chmod|chown)\s+-R\s+(777|root|/|~|\$HOME)",
    r"\bsudo\b",
    r"\b(dd|mkfs|mount|umount)\b",
    r"\bcodex\s+exec\b.*(--yolo|--dangerously-bypass-approvals-and-sandbox|danger-full-access)",
]

PATCH_REVIEW_MAX_BYTES = 200000
DIFF_REVIEW_MAX_LINES = 1000
DIFF_REVIEW_MAX_FILES = 30
LEARNED_MAX_BYTES = 50000

AUDIT_EVENTS = {
    "spec.bypass",
    "codex.semantic_review.bypassed",
    "merge.queue.blocked_by_codex_review",
    "diff.review.rejected",
    "merge.conflict",
    "merge.validation_failed.rollback",
    "validation.generated_changes",
    "protected_path.modified",
    "protected_path.detected",
    "task.escalated_to_user",
}

INSTALL_INPUT_GLOBS = [
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "bun.lockb",
    "npm-shrinkwrap.json",
    "pyproject.toml",
    "poetry.lock",
    "uv.lock",
    "requirements*.txt",
    "constraints*.txt",
    "Pipfile.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def run(
    cmd: List[str],
    cwd: Optional[pathlib.Path] = None,
    input_text: Optional[str] = None,
    timeout: Optional[int] = None,
    check: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"command failed ({cp.returncode}): {' '.join(cmd)}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    return cp


def shell(
    command: str,
    cwd: pathlib.Path,
    timeout: int,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
    )


def git_root() -> pathlib.Path:
    cp = run(["git", "rev-parse", "--show-toplevel"], check=False)
    if cp.returncode == 0:
        return pathlib.Path(cp.stdout.strip()).resolve()
    return pathlib.Path.cwd().resolve()


def paths(root: Optional[pathlib.Path] = None) -> Dict[str, pathlib.Path]:
    root = root or git_root()
    orch = root / ".orchestration"
    return {
        "root": root,
        "orch": orch,
        "tasks": orch / "tasks",
        "locks": orch / "locks",
        "schemas": orch / "schemas",
        "scripts": orch / "scripts",
        "bin": orch / "bin",
        "cache": orch / "cache",
        "progress": orch / "progress.jsonl",
        "audit": orch / "audit.jsonl",
        "manager_lock": orch / "manager.lock",
        "learned": orch / "LEARNED.md",
        "ledger": orch / "ledger.json",
        "queue": orch / "merge-queue.json",
        "worktrees": root.parent / f"{root.name}.codex-worktrees",
    }


def ensure_dirs(root: Optional[pathlib.Path] = None) -> Dict[str, pathlib.Path]:
    p = paths(root)
    for key in ("orch", "tasks", "locks", "schemas", "scripts", "bin", "cache", "worktrees"):
        p[key].mkdir(parents=True, exist_ok=True)
    if not p["progress"].exists():
        p["progress"].write_text("", encoding="utf-8")
    if not p["audit"].exists():
        p["audit"].write_text("", encoding="utf-8")
    if not p["queue"].exists():
        write_json(p["queue"], [])
    return p


def write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def read_json(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: pathlib.Path, line: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")


def should_audit_event(event: str, data: Optional[Dict[str, Any]] = None) -> bool:
    if event in AUDIT_EVENTS:
        return True
    if event == "task.status" and isinstance(data, dict) and data.get("to") in {"failed", "blocked"}:
        return True
    return False


def append_event(
    actor: str,
    event: str,
    task_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    p = ensure_dirs()
    payload = data or {}
    line = {
        "ts": now(),
        "actor": actor,
        "event": event,
        "task_id": task_id,
        "data": payload,
    }
    append_jsonl(p["progress"], line)
    if should_audit_event(event, payload):
        append_jsonl(p["audit"], line)


def init_ledger(root: pathlib.Path) -> Dict[str, Any]:
    p = ensure_dirs(root)
    if not p["ledger"].exists():
        ledger = {
            "version": 1,
            "project": root.name,
            "created_at": now(),
            "updated_at": now(),
            "commands": {
                "install": "",
                "lint": "",
                "typecheck": "",
                "test": "",
                "build": "",
            },
            "settings": {
                "max_parallel": 3,
                "failure_strategy_after": 2,
                "user_escalation_after": 4,
                "diff_review_max_lines": DIFF_REVIEW_MAX_LINES,
                "diff_review_max_files": DIFF_REVIEW_MAX_FILES,
            },
            "tasks": [],
        }
        write_json(p["ledger"], ledger)
    return read_json(p["ledger"], {})


def load_ledger() -> Dict[str, Any]:
    root = git_root()
    p = ensure_dirs(root)
    if not p["ledger"].exists():
        return init_ledger(root)
    return read_json(p["ledger"], {})


def save_ledger(ledger: Dict[str, Any]) -> None:
    ledger["updated_at"] = now()
    write_json(paths()["ledger"], ledger)


def slugify(text: str, max_len: int = 48) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return (text[:max_len].strip("-") or "task")


def new_task_id(title: str) -> str:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    salt = f"{title}:{time.time_ns()}"
    return f"T{ts}-{hashlib.sha1(salt.encode()).hexdigest()[:6]}"


def find_task(ledger: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    for task in ledger.get("tasks", []):
        if task.get("id") == task_id:
            return task
    raise KeyError(f"task not found: {task_id}")


def normalize_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [x.strip().lstrip("./") for x in re.split(r"[,\n]", raw) if x.strip()]


def path_parts(path: str) -> Tuple[str, ...]:
    clean = path.strip().lstrip("./")
    if not clean:
        return tuple()
    return pathlib.PurePosixPath(clean.replace(os.sep, "/")).parts


def paths_overlap(a: str, b: str) -> bool:
    ap, bp = path_parts(a), path_parts(b)
    # Empty touched_paths means unknown blast radius. Treat it as conflicting with
    # everything so such tasks never run in parallel by accident.
    if not ap or not bp:
        return True
    n = min(len(ap), len(bp))
    return ap[:n] == bp[:n]


def is_shared_path(path: str) -> bool:
    p = path.lstrip("./")
    return any(re.search(pattern, p) for pattern in SHARED_PATTERNS)


def is_protected_path(path: str) -> bool:
    p = path.lstrip("./")
    return any(re.search(pattern, p) for pattern in PROTECTED_PATTERNS)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


class AtomicLock:
    def __init__(self, name: str, timeout: int = 120, stale_after: int = 3600):
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
        self.path = ensure_dirs()["locks"] / f"{safe}.lock"
        self.timeout = timeout
        self.stale_after = stale_after

    def __enter__(self) -> "AtomicLock":
        deadline = time.time() + self.timeout
        while True:
            try:
                self.path.mkdir(parents=True)
                write_json(
                    self.path / "meta.json",
                    {
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "created_at": time.time(),
                        "created_at_iso": now(),
                    },
                )
                return self
            except FileExistsError:
                if self._is_stale():
                    shutil.rmtree(self.path, ignore_errors=True)
                    continue
                if time.time() > deadline:
                    raise TimeoutError(f"could not acquire lock: {self.path}")
                time.sleep(0.5)

    def _is_stale(self) -> bool:
        meta = read_json(self.path / "meta.json", {})
        created = float(meta.get("created_at", 0) or 0)
        pid = int(meta.get("pid", 0) or 0)
        same_host = meta.get("host") == socket.gethostname()
        if same_host and pid and not pid_alive(pid):
            return True
        if created and time.time() - created > self.stale_after:
            return True
        return False

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def cmd_init(args: argparse.Namespace) -> int:
    root = git_root()
    p = ensure_dirs(root)
    ledger = init_ledger(root)
    for k in ["install", "lint", "typecheck", "test", "build"]:
        val = getattr(args, k, None)
        if val is not None:
            ledger.setdefault("commands", {})[k] = val
    save_ledger(ledger)
    run(["git", "config", "rerere.enabled", "true"], cwd=root)
    run(["git", "config", "rerere.autoupdate", "true"], cwd=root)
    for sub in ["npm", "pnpm", "pip", "cargo", "go", "tmp", "installed"]:
        (p["cache"] / sub).mkdir(parents=True, exist_ok=True)
    gitignore_result = ensure_gitignore_block(root)
    append_event("claude", "orchestration.init", data={"root": str(root), "gitignore": gitignore_result})
    print(f"initialized orchestration at {p['orch']}")
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    ledger = load_ledger()

    if args.ledger_cmd == "new":
        touched_paths = normalize_list(args.paths)
        if not touched_paths and not args.allow_empty_paths:
            raise ValueError(
                "--paths is required. Empty touched_paths has unknown blast radius and disables parallel scheduling. "
                "Use --allow-empty-paths only for intentionally serialized exploratory tasks."
            )
        task = {
            "id": args.id or new_task_id(args.title),
            "title": args.title,
            "slug": slugify(args.title),
            "objective": args.objective,
            "acceptance": args.acceptance or "",
            "status": "pending",
            "dependencies": normalize_list(args.deps),
            "touched_paths": touched_paths,
            "shared_resources": [],
            "attempts": 0,
            "max_attempts": args.max_attempts,
            "timeout_seconds": args.timeout,
            "soft_budget_seconds": args.soft_budget,
            "hard_budget_seconds": args.hard_budget,
            "created_at": now(),
            "updated_at": now(),
            "owner": "claude",
            "branch": "",
            "worktree": "",
            "base_ref": "",
            "last_dispatch_head": "",
            "last_error_hash": "",
            "same_failure_count": 0,
            "artifacts": {},
            "spec_history": [],
            "phase_state": "",
        }
        task["shared_resources"] = [p for p in task["touched_paths"] if is_shared_path(p)]
        ledger.setdefault("tasks", []).append(task)
        save_ledger(ledger)
        append_event("claude", "task.created", task["id"], {"title": args.title})
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return 0

    if args.ledger_cmd == "list":
        if args.json:
            print(json.dumps(ledger.get("tasks", []), ensure_ascii=False, indent=2))
        else:
            for t in ledger.get("tasks", []):
                print(f"{t['id']}\t{t['status']}\tattempts={t.get('attempts', 0)}\t{t['title']}")
        return 0

    if args.ledger_cmd == "show":
        print(json.dumps(find_task(ledger, args.id), ensure_ascii=False, indent=2))
        return 0

    if args.ledger_cmd == "set-status":
        if args.status not in STATUS:
            raise ValueError(f"invalid status: {args.status}")
        task = find_task(ledger, args.id)
        old = task["status"]
        task["status"] = args.status
        task["updated_at"] = now()
        if args.reason:
            task["last_reason"] = args.reason
        save_ledger(ledger)
        append_event(
            "claude",
            "task.status",
            args.id,
            {"from": old, "to": args.status, "reason": args.reason},
        )
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return 0

    if args.ledger_cmd == "set-commands":
        for k in ["install", "lint", "typecheck", "test", "build"]:
            val = getattr(args, k, None)
            if val is not None:
                ledger.setdefault("commands", {})[k] = val
        save_ledger(ledger)
        print(json.dumps(ledger.get("commands", {}), ensure_ascii=False, indent=2))
        return 0

    raise ValueError(args.ledger_cmd)


def dependencies_satisfied(task: Dict[str, Any], tasks_by_id: Dict[str, Dict[str, Any]]) -> bool:
    for dep in task.get("dependencies", []):
        if tasks_by_id.get(dep, {}).get("status") != "merged":
            return False
    return True


def task_conflicts(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    apaths = a.get("touched_paths", []) or [""]
    bpaths = b.get("touched_paths", []) or [""]
    if any(is_shared_path(p) for p in apaths + bpaths):
        return True
    for x in apaths:
        for y in bpaths:
            if paths_overlap(x, y):
                return True
    return False


def selectable_tasks(ledger: Dict[str, Any], max_count: int) -> List[Dict[str, Any]]:
    tasks = ledger.get("tasks", [])
    by_id = {t["id"]: t for t in tasks}
    candidates = [
        t
        for t in tasks
        if t.get("status") == "pending" and dependencies_satisfied(t, by_id)
    ]
    selected: List[Dict[str, Any]] = []
    for task in candidates:
        if len(selected) >= max_count:
            break
        if all(not task_conflicts(task, chosen) for chosen in selected):
            selected.append(task)
    return selected


def cmd_select_parallel(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    chosen = selectable_tasks(ledger, args.max_workers)
    print(json.dumps(chosen, ensure_ascii=False, indent=2))
    return 0


def current_head(root: pathlib.Path) -> str:
    return run(["git", "rev-parse", "HEAD"], cwd=root, check=True).stdout.strip()


def branch_exists(root: pathlib.Path, branch: str) -> bool:
    return (
        run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=root).returncode
        == 0
    )


def ensure_worktree(root: pathlib.Path, task: Dict[str, Any], base_ref: Optional[str]) -> pathlib.Path:
    p = ensure_dirs(root)
    branch = task.get("branch") or f"codex/{task['id']}-{task.get('slug') or slugify(task['title'])}"
    wt = pathlib.Path(
        task.get("worktree")
        or (p["worktrees"] / f"{task['id']}-{task.get('slug') or slugify(task['title'])}")
    )
    base = base_ref or task.get("base_ref") or current_head(root)

    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        if branch_exists(root, branch):
            cp = run(["git", "worktree", "add", str(wt), branch], cwd=root)
        else:
            cp = run(["git", "worktree", "add", "-b", branch, str(wt), base], cwd=root)
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr or cp.stdout)

    task["branch"] = branch
    task["worktree"] = str(wt)
    task["base_ref"] = base
    return wt


def task_dir(task_id: str) -> pathlib.Path:
    d = ensure_dirs()["tasks"] / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def spec_path(task_id: str) -> pathlib.Path:
    return task_dir(task_id) / "spec.md"


def parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip('"\'') for x in inner.split(",")]
    if (value.startswith('\"') and value.endswith('\"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except Exception:
            pass
    return value



def cdata(text: str) -> str:
    """Return text safely wrapped for CDATA sections.

    XML cannot contain the literal sequence `]]>` inside CDATA. Splitting it
    keeps the prompt boundary unambiguous while preserving the visible text.
    """
    return str(text).replace("]]>", "]]]]><![CDATA[>")


def xml_escape(text: Any) -> str:
    value = str(text)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def xml_path_list(tag_name: str, paths: Iterable[str]) -> str:
    lines = [f"<{tag_name}>"]
    for path in paths:
        lines.append(f"  <path>{xml_escape(path)}</path>")
    lines.append(f"</{tag_name}>")
    return "\n".join(lines)


def normalize_xml_fragment(text: str) -> str:
    return text.strip()

def split_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    data: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(parse_scalar(line[4:]))
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val == "":
                data[key] = []
                current_key = key
            else:
                data[key] = parse_scalar(val)
                current_key = key
    return data, body


def render_frontmatter(data: Dict[str, Any]) -> str:
    lines = ["---"]
    for key in sorted(data.keys()):
        value = data[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, bool):
                    rendered = "true" if item else "false"
                elif isinstance(item, int):
                    rendered = str(item)
                else:
                    rendered = '"' + str(item).replace('"', '\\"') + '"'
                lines.append(f"  - {rendered}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key}: {value}")
        else:
            text = str(value).replace('"', '\\"')
            lines.append(f'{key}: "{text}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def read_spec(task_id: str) -> Tuple[Dict[str, Any], str, pathlib.Path]:
    path = spec_path(task_id)
    if not path.exists():
        return {}, "", path
    text = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    # v2 compatibility: specs approved by v2 could contain version: "1".
    if isinstance(fm.get("version"), str) and str(fm.get("version")).isdigit(): fm["version"] = int(fm["version"])
    return fm, body, path


def spec_kind(task_id: str) -> str:
    fm, _body, _path = read_spec(task_id)
    return str(fm.get("kind") or "feature").strip().lower()


def validate_spec_file(path: pathlib.Path, task_id: Optional[str] = None) -> Tuple[bool, List[str], Dict[str, Any]]:
    # Source of truth for this contract; keep spec.schema.json in sync.
    errors: List[str] = []
    if not path.exists():
        return False, [f"spec not found: {path}"], {}
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    if isinstance(fm.get("version"), str) and str(fm.get("version")).isdigit():
        fm["version"] = int(fm["version"])
    required_frontmatter = ["version", "id", "title", "kind", "status", "owner"]
    for key in required_frontmatter:
        value = fm.get(key)
        if key not in fm or value is None or value == "":
            errors.append(f"frontmatter missing required key: {key}")
    if "version" in fm and not isinstance(fm.get("version"), int):
        errors.append("frontmatter version must be integer")
    for key in ["id", "title", "kind", "status", "owner"]:
        if key in fm and not isinstance(fm.get(key), str):
            errors.append(f"frontmatter {key} must be string")
    if task_id and fm.get("id") and fm.get("id") != task_id:
        errors.append(f"frontmatter id {fm.get('id')} does not match task id {task_id}")
    kind = str(fm.get("kind") or "").lower()
    if kind and kind not in SPEC_BYPASS_KINDS | SPEC_REQUIRED_KINDS | {"test", "chore"}:
        errors.append(f"unsupported spec kind: {kind}")
    required_sections = [
        "## Purpose",
        "## User story",
        "## Input/output contract",
        "## Behavior specification",
        "## Non-functional requirements",
        "## Acceptance criteria",
        "## Out of scope",
        "## Expected test cases",
    ]
    for section in required_sections:
        if section.lower() not in body.lower():
            errors.append(f"missing required section: {section}")
    acceptance = extract_acceptance_items(body)
    if not acceptance:
        errors.append("acceptance criteria must contain at least one checklist item")
    return len(errors) == 0, errors, fm

def extract_acceptance_items(spec_body: str) -> List[str]:
    items: List[str] = []
    in_acceptance = False
    for line in spec_body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## "):
            in_acceptance = stripped.lower().startswith("## acceptance")
            continue
        if in_acceptance:
            m = re.match(r"[-*]\s+\[[ xX]\]\s+(.+)", stripped) or re.match(r"[-*]\s+(.+)", stripped)
            if m:
                item = m.group(1).strip()
                if item:
                    items.append(item)
    return items


def token_set(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9_]{4,}", text.lower())
    stop = {"should", "must", "when", "then", "with", "from", "that", "this", "into", "there", "their", "have"}
    return {w for w in words if w not in stop}


def acceptance_test_coverage(worktree: pathlib.Path, task_id: str, changed: List[str]) -> Dict[str, Any]:
    fm, body, _path = read_spec(task_id)
    items = extract_acceptance_items(body)
    test_files = [f for f in changed if TEST_PATH_RE.search(f)]
    test_text = ""
    for rel in test_files:
        p = worktree / rel
        if p.is_file() and p.stat().st_size < 1_000_000:
            try:
                test_text += "\n" + p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    missing: List[str] = []
    covered: List[str] = []
    hay = token_set(test_text)
    for item in items:
        toks = token_set(item)
        if not toks:
            missing.append(item)
            continue
        # Heuristic: at least two meaningful tokens, or half of tokens for short criteria.
        overlap = len(toks & hay)
        threshold = min(2, max(1, len(toks) // 2))
        if overlap >= threshold:
            covered.append(item)
        else:
            missing.append(item)
    return {"items": items, "covered": covered, "missing": missing, "test_files": test_files}


def spec_template(task: Dict[str, Any], kind: str = "feature") -> str:
    data = {
        "id": task["id"],
        "title": task.get("title", ""),
        "kind": kind,
        "status": "draft",
        "owner": "claude",
        "version": 1,
    }
    return render_frontmatter(data) + f"""# Spec: {task.get('title', task['id'])}

## Purpose

{task.get('objective', '<Describe the purpose of this task.>')}

## User story

As a <user or system actor>, I want <capability>, so that <outcome>.

## Input/output contract

- Function signature / API endpoint / CLI contract: `<fill in>`
- Input shape: `<fill in>`
- Output shape: `<fill in>`
- Error shape: `<fill in>`

## Behavior specification

### Success cases

- <success behavior>

### Failure cases

- <failure behavior>

### Edge cases

- <edge case>

## Non-functional requirements

- Performance: <requirement or N/A>
- Compatibility: <requirement or N/A>
- Security: <requirement or N/A>

## Acceptance criteria

- [ ] {task.get('acceptance') or '<criterion 1>'}

## Out of scope

- <explicitly out of scope>

## Expected test cases

- <test case linked to acceptance criterion>
"""


def ensure_gitignore_block(root: pathlib.Path) -> Dict[str, Any]:
    path = root / ".gitignore"
    begin = "# BEGIN claude-codex-orchestration"
    end = "# END claude-codex-orchestration"
    rules = [
        ".orchestration/cache/**",
        "!.orchestration/cache/.gitkeep",
        ".orchestration/locks/**",
        "!.orchestration/locks/.gitkeep",
        ".orchestration/tasks/*/codex.stdout.jsonl",
        ".orchestration/tasks/*/codex.stderr.log",
        ".orchestration/tasks/*/codex.review.stdout.jsonl",
        ".orchestration/tasks/*/codex.review.stderr.log",
        ".orchestration/tasks/*/validation.log",
        ".orchestration/tasks/*/merge-validation.log",
        ".orchestration/tasks/*/*.tmp",
        ".orchestration/session-*.json",
        ".orchestration/session-*.jsonl",
        ".orchestration/session-*.txt",
        ".orchestration/init-detect.json",
        ".orchestration/audit.jsonl",
        ".orchestration/manager.lock",
        ".orchestration/ledger.json.backup.*",
        ".orchestration/tasks/*/phase*/codex.stdout.jsonl",
        ".orchestration/tasks/*/phase*/codex.stderr.log",
        ".orchestration/tasks/*/phase*/codex.review.stdout.jsonl",
        ".orchestration/tasks/*/phase*/codex.review.stderr.log",
        ".orchestration/tasks/*/phase*/validation.log",
        ".codex/config.local.toml",
        ".codex/auth.json",
        ".codex/sessions/",
        ".codex/log/",
        "*.tmp",
    ]
    block = begin + "\n" + "\n".join(rules) + "\n" + end + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if begin in old and end in old:
        pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
        match = pattern.search(old)
        current = match.group(0) if match else ""
        existing_rules = [ln.strip() for ln in current.splitlines() if ln.strip() and not ln.startswith("#")]
        merged = []
        for rule in existing_rules + rules:
            if rule not in merged:
                merged.append(rule)
        new_block = begin + "\n" + "\n".join(merged) + "\n" + end + "\n"
        if current == new_block:
            return {"path": str(path), "changed": False, "reason": "existing block found"}
        path.write_text(pattern.sub(new_block, old), encoding="utf-8")
        return {"path": str(path), "changed": True, "reason": "existing block updated"}
    new = old.rstrip() + ("\n\n" if old.strip() else "") + block
    path.write_text(new, encoding="utf-8")
    return {"path": str(path), "changed": True, "reason": "block appended"}



def read_toml_like(path: pathlib.Path) -> Dict[str, Any]:
    """Read enough TOML for Codex config inspection without external deps."""
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        data: Dict[str, Any] = {}
        current: List[str] = []
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line.strip("[]")
                current = [part.strip().strip('"') for part in section.split(".") if part.strip()]
                cursor = data
                for part in current:
                    cursor = cursor.setdefault(part, {})
                continue
            m = re.match(r'([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"\s*$', line)
            if not m:
                continue
            cursor = data
            for part in current:
                cursor = cursor.setdefault(part, {})
            cursor[m.group(1)] = m.group(2)
        return data


def root_level_model(config: Dict[str, Any]) -> str:
    val = config.get("model", "") if isinstance(config, dict) else ""
    return val if isinstance(val, str) else ""


def profile_model(config: Dict[str, Any], profile: str) -> str:
    if not profile or not isinstance(config, dict):
        return ""
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        return ""
    prof = profiles.get(profile, {})
    if not isinstance(prof, dict):
        return ""
    val = prof.get("model", "")
    return val if isinstance(val, str) else ""


def config_default_profile(config: Dict[str, Any]) -> str:
    val = config.get("profile", "") if isinstance(config, dict) else ""
    return val if isinstance(val, str) else ""


def codex_login_status() -> bool:
    try:
        return run(["codex", "login", "status"], timeout=10).returncode == 0
    except Exception:
        return False


def codex_version_string() -> Tuple[bool, str]:
    try:
        cp = run(["codex", "--version"], timeout=10)
        return cp.returncode == 0, (cp.stdout or cp.stderr).strip()
    except Exception:
        return False, ""


def project_trust_state(root: pathlib.Path, user_config: Dict[str, Any]) -> str:
    """Best-effort local heuristic. There is no documented stable CLI trust query yet."""
    projects = user_config.get("projects", {}) if isinstance(user_config, dict) else {}
    if isinstance(projects, dict):
        candidates = {str(root.resolve()), str(root)}
        for key, val in projects.items():
            if str(key) in candidates and isinstance(val, dict):
                trust = str(val.get("trust_level") or val.get("trusted_level") or val.get("trust") or "").lower()
                if trust in {"trusted", "true", "full"}:
                    return "trusted"
                if trust in {"untrusted", "false", "read-only", "readonly"}:
                    return "untrusted"
    return "unknown"


def codex_preconditions(root: pathlib.Path) -> Dict[str, Any]:
    installed, version = codex_version_string()
    authenticated = codex_login_status() if installed else False
    user_path = pathlib.Path.home() / ".codex" / "config.toml"
    project_path = root / ".codex" / "config.toml"
    user_cfg = read_toml_like(user_path)
    project_cfg = read_toml_like(project_path)
    user_model = root_level_model(user_cfg)
    project_model = root_level_model(project_cfg)
    user_profile = config_default_profile(user_cfg)
    project_profile = config_default_profile(project_cfg)
    user_profile_model = profile_model(user_cfg, user_profile)
    project_profile_model = profile_model(project_cfg, project_profile)
    trust = project_trust_state(root, user_cfg)

    effective_source = "codex-builtin"
    effective = "codex-builtin-default"
    if project_path.exists() and (project_model or project_profile_model):
        if trust == "trusted":
            effective_source = "project"
            effective = project_profile_model or project_model
        else:
            effective_source = "unknown"
            effective = "unknown"
    elif user_model or user_profile_model:
        effective_source = "user"
        effective = user_profile_model or user_model

    return {
        "installed": bool(installed),
        "version": version,
        "authenticated": bool(authenticated),
        "user_config_exists": user_path.exists(),
        "user_config_model": user_model,
        "user_config_profile": user_profile,
        "user_config_profile_model": user_profile_model,
        "project_config_exists": project_path.exists(),
        "project_config_model": project_model,
        "project_config_profile": project_profile,
        "project_config_profile_model": project_profile_model,
        "effective_model_source": effective_source,
        "effective_model": effective or "unknown",
        "trust_state": trust,
        "trust_state_note": "Best-effort; no stable documented CLI query found. Run `codex` in this repo to establish trust if unknown.",
    }


def effective_model_for_run(root: pathlib.Path, explicit_model: str = "", profile: str = "") -> str:
    if explicit_model:
        return explicit_model
    if profile:
        return f"profile-controlled:{profile}"
    data = codex_preconditions(root)
    model = str(data.get("effective_model") or "")
    if not model or model == "unknown":
        return "codex-builtin-default"
    return model

def detect_project(root: pathlib.Path) -> Dict[str, Any]:
    files = {p.name for p in root.iterdir()} if root.exists() else set()
    stacks: List[Dict[str, Any]] = []
    monorepo: List[str] = []
    commands = {"install": "", "lint": "", "typecheck": "", "test": "", "build": ""}
    shared_candidates: List[str] = []

    package_json = root / "package.json"
    if package_json.exists():
        try:
            pkg = json.loads(package_json.read_text(encoding="utf-8"))
        except Exception:
            pkg = {}
        pm = str(pkg.get("packageManager") or "")
        lock = ""
        for candidate in ["pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lockb"]:
            if (root / candidate).exists():
                lock = candidate
                break
        if not pm:
            if lock == "pnpm-lock.yaml": pm = "pnpm"
            elif lock == "yarn.lock": pm = "yarn"
            elif lock == "bun.lockb": pm = "bun"
            else: pm = "npm"
        runner = pm.split("@", 1)[0]
        if runner not in {"pnpm", "yarn", "bun", "npm"}:
            runner = "npm"
        scripts = pkg.get("scripts", {}) if isinstance(pkg.get("scripts"), dict) else {}
        commands["install"] = {"pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --frozen-lockfile", "bun": "bun install --frozen-lockfile", "npm": "npm ci"}.get(runner, "npm ci")
        commands["lint"] = f"{runner} run lint" if "lint" in scripts else ""
        commands["typecheck"] = f"{runner} run typecheck" if "typecheck" in scripts else (f"{runner} run type-check" if "type-check" in scripts else "")
        commands["test"] = f"{runner} test" if "test" in scripts else ""
        commands["build"] = f"{runner} run build" if "build" in scripts else ""
        stacks.append({"name": "node", "package_manager": pm, "lockfile": lock, "workspaces": pkg.get("workspaces", [])})
        if pkg.get("workspaces"):
            monorepo.append("package.json workspaces")
        for f in ["pnpm-workspace.yaml", "nx.json", "turbo.json", "lerna.json", "rush.json"]:
            if (root / f).exists():
                monorepo.append(f)
                shared_candidates.append(f)

    if (root / "pyproject.toml").exists() or list(root.glob("requirements*.txt")) or (root / "setup.py").exists():
        py = root / "pyproject.toml"
        manager = "pip"
        if py.exists():
            txt = py.read_text(encoding="utf-8", errors="replace")
            if "[tool.poetry]" in txt: manager = "poetry"
            elif "[tool.uv]" in txt or "uv" in txt and (root / "uv.lock").exists(): manager = "uv"
            elif "[tool.hatch" in txt: manager = "hatch"
            elif "[tool.pdm]" in txt: manager = "pdm"
        stacks.append({"name": "python", "manager": manager})
        if not commands["install"]:
            commands["install"] = {"poetry": "poetry install", "uv": "uv sync --frozen", "pdm": "pdm install", "hatch": "hatch env create"}.get(manager, "python -m pip install -r requirements.txt")
        if not commands["lint"]:
            commands["lint"] = "uv run ruff check ." if manager == "uv" else ""
        if not commands["typecheck"]:
            commands["typecheck"] = "uv run mypy ." if manager == "uv" else ""
        if not commands["test"]:
            commands["test"] = "uv run pytest" if manager == "uv" else "pytest"

    if (root / "Cargo.toml").exists():
        cargo_text = (root / "Cargo.toml").read_text(encoding="utf-8", errors="replace")
        stacks.append({"name": "rust", "workspace": "[workspace]" in cargo_text})
        if "[workspace]" in cargo_text: monorepo.append("Cargo workspace")
        commands.update({k: commands[k] for k in commands})
        commands["install"] = commands["install"] or "cargo fetch"
        commands["lint"] = commands["lint"] or "cargo clippy --all-targets --all-features -- -D warnings"
        commands["test"] = commands["test"] or "cargo test --all"
        commands["build"] = commands["build"] or "cargo build --all"

    if (root / "go.mod").exists():
        stacks.append({"name": "go", "workspace": (root / "go.work").exists()})
        if (root / "go.work").exists(): monorepo.append("go.work")
        commands["install"] = commands["install"] or "go mod download"
        commands["lint"] = commands["lint"] or "go vet ./..."
        commands["test"] = commands["test"] or "go test ./..."
        commands["build"] = commands["build"] or "go build ./..."

    if (root / "pom.xml").exists() or list(root.glob("build.gradle*")):
        stacks.append({"name": "jvm", "tool": "maven" if (root / "pom.xml").exists() else "gradle"})
        if (root / "pom.xml").exists():
            commands["install"] = commands["install"] or "mvn -q -DskipTests dependency:go-offline"
            commands["test"] = commands["test"] or "mvn test"
            commands["build"] = commands["build"] or "mvn package"
        else:
            commands["test"] = commands["test"] or "./gradlew test"
            commands["build"] = commands["build"] or "./gradlew build"

    other_markers = [
        ("ruby", "Gemfile", {"install": "bundle install", "test": "bundle exec rspec"}),
        ("php", "composer.json", {"install": "composer install", "test": "composer test"}),
        ("dotnet", "*.sln", {"install": "dotnet restore", "test": "dotnet test", "build": "dotnet build"}),
        ("elixir", "mix.exs", {"install": "mix deps.get", "test": "mix test"}),
        ("dart", "pubspec.yaml", {"install": "dart pub get", "test": "dart test"}),
        ("swift", "Package.swift", {"install": "swift package resolve", "test": "swift test", "build": "swift build"}),
    ]
    for name, marker, defaults in other_markers:
        exists = bool(list(root.glob(marker))) if "*" in marker else (root / marker).exists()
        if exists:
            stacks.append({"name": name})
            for k, v in defaults.items():
                commands[k] = commands[k] or v

    for f in ["generated", "dist", "build", "target", ".next", "coverage"]:
        if (root / f).exists():
            shared_candidates.append(f + "/**")

    collisions = {
        "CLAUDE.md": (root / "CLAUDE.md").exists(),
        "AGENTS.md": (root / "AGENTS.md").exists(),
        ".claude/settings.json": (root / ".claude/settings.json").exists(),
        ".gitignore": (root / ".gitignore").exists(),
        ".orchestration/ledger.json": (root / ".orchestration/ledger.json").exists(),
    }
    return {
        "root": str(root),
        "stacks": stacks,
        "monorepo": sorted(set(monorepo)),
        "recommended_commands": commands,
        "shared_pattern_suggestions": sorted(set(shared_candidates)),
        "collisions": collisions,
        "idempotent": True,
        "brownfield_safe": True,
        "codex_preconditions": codex_preconditions(root),
    }



def learned_lessons_xml() -> str:
    lp = paths().get("learned")
    if not lp or not lp.exists():
        return "<learned_lessons><![CDATA[]]></learned_lessons>"
    text = lp.read_text(encoding="utf-8", errors="replace")
    raw = text.encode("utf-8", errors="replace")
    if len(raw) > LEARNED_MAX_BYTES:
        sections = re.split(r"(?=^## L-\d{3}\b)", text, flags=re.MULTILINE)
        header = sections[0] if sections and sections[0].lstrip().startswith("#") else "# Learned lessons\n\n"
        recent = sections[-50:]
        text = header + "".join(recent)
        if len(text.encode("utf-8", errors="replace")) > LEARNED_MAX_BYTES:
            text = text.encode("utf-8", errors="replace")[-LEARNED_MAX_BYTES:].decode("utf-8", errors="replace")
            text = "[learned lessons truncated to recent content]\n" + text
    return f"<learned_lessons><![CDATA[{cdata(text)}]]></learned_lessons>"


def parse_since(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    v = value.strip().lower()
    m = re.fullmatch(r"(\d+)\s*([smhdw])", v)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit] * n
        return dt.datetime.now(dt.timezone.utc).astimezone() - dt.timedelta(seconds=seconds)
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except Exception:
        return None


def parse_time(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except Exception:
        return None


def read_jsonl(path: pathlib.Path, since: str = "") -> List[Dict[str, Any]]:
    cutoff = parse_since(since) if since else None
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if cutoff:
            ts = parse_time(str(item.get("ts", "")))
            if ts and ts < cutoff:
                continue
        rows.append(item)
    return rows


def html_escape(text: Any) -> str:
    return xml_escape(text)


def count_changed_lines(diff: str) -> int:
    n = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            n += 1
    return n


def build_prompt(
    task: Dict[str, Any],
    ledger: Dict[str, Any],
    extra_prompt: str = "",
    spec_text: str = "",
    retry_prompt_xml: str = "",
    parallel_contract_xml: str = "",
) -> str:
    commands = ledger.get("commands", {})
    spec_block = spec_text.strip() or "LEGACY TASK: no spec.md was provided. Follow task objective and acceptance criteria only."
    allowed_paths_xml = xml_path_list("allowed_paths", task.get("touched_paths", []))
    manager_context_xml = f"<manager_context><![CDATA[{cdata(extra_prompt)}]]></manager_context>" if extra_prompt.strip() else "<manager_context><![CDATA[]]></manager_context>"
    raw_contexts = "\n".join(x for x in [normalize_xml_fragment(parallel_contract_xml), normalize_xml_fragment(retry_prompt_xml)] if x)
    return f"""You are Codex CLI running as an implementation worker for Claude Code.

Task ID: {task['id']}
Title: {task.get('title', '')}

Objective:
{task.get('objective', '')}

Acceptance criteria from ledger:
{task.get('acceptance', '')}

Authoritative task spec.md:
<spec><![CDATA[{cdata(spec_block)}]]></spec>

{allowed_paths_xml}

Commands to run when relevant:
- install: {commands.get('install', '')}
- lint: {commands.get('lint', '')}
- typecheck: {commands.get('typecheck', '')}
- test: {commands.get('test', '')}
- build: {commands.get('build', '')}

Hard constraints:
- Read and follow AGENTS.md before editing.
- Treat spec.md as read-only. Do not edit .orchestration/tasks/{task['id']}/spec.md.
- Implement exactly what the spec requires; do not expand scope.
- New behavior requires tests. Add or update test files for every acceptance criterion unless spec frontmatter kind is refactor, docs, or config.
- Do not edit .orchestration/, .env*, secrets/, .git/, or CI workflow files.
- Do not run git push, git push --force, git reset --hard, git filter-branch, git filter-repo, sudo, rm -rf on broad paths, or global package installs.
- Do not add or upgrade dependencies unless the task explicitly requires it; if required, document why.
- Preserve tests. Do not remove assertions, add skip/only, or weaken tests to make them pass.
- Work only in your current git worktree.
- Run the relevant checks and report their exact command and result.

{manager_context_xml}
{raw_contexts}
{learned_lessons_xml()}

Return only JSON matching the provided schema in your final message:
{{"summary":"...","changed_files":["..."],"commands_run":[{{"command":"...","exit_code":0}}],"risks":["..."],"ready_for_review":true}}
"""

def parse_jsonl(path: pathlib.Path) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "events": 0,
        "thread_id": "",
        "turn_failed": False,
        "errors": [],
        "command_failures": [],
    }
    if not path.exists():
        return stats

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            stats["events"] += 1
            typ = ev.get("type", "")

            if typ == "thread.started":
                stats["thread_id"] = ev.get("thread_id", "") or ev.get("thread", {}).get("id", "")
            if typ == "turn.failed":
                stats["turn_failed"] = True
                stats["errors"].append(ev)
            if typ == "error":
                stats["errors"].append(ev)

            item = ev.get("item") or {}
            if item.get("type") == "command_execution" and item.get("status") in {"failed", "error"}:
                stats["command_failures"].append(item)
            if item.get("type") == "command_execution" and int(item.get("exit_code", 0) or 0) != 0:
                stats["command_failures"].append(item)

    return stats


def porcelain_files(worktree: pathlib.Path) -> List[str]:
    cp = run(["git", "status", "--porcelain=v1"], cwd=worktree, check=True)
    files: List[str] = []
    for line in cp.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return sorted(set(files))


def diff_name_only(worktree: pathlib.Path, args: List[str]) -> List[str]:
    cp = run(["git", "diff", "--name-only", *args], cwd=worktree)
    if cp.returncode != 0:
        return []
    return [x.strip() for x in cp.stdout.splitlines() if x.strip()]


def changed_files(worktree: pathlib.Path, base_ref: Optional[str] = None) -> List[str]:
    files = set(porcelain_files(worktree))
    if base_ref:
        files.update(diff_name_only(worktree, [f"{base_ref}...HEAD"]))
        files.update(diff_name_only(worktree, [base_ref]))
    return sorted(files)


def untracked_files(worktree: pathlib.Path) -> List[str]:
    cp = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree)
    if cp.returncode != 0:
        return []
    return [x.strip() for x in cp.stdout.splitlines() if x.strip()]


def artificial_untracked_diff(worktree: pathlib.Path, files: Iterable[str], max_bytes: int = 1_000_000) -> str:
    chunks: List[str] = []
    for rel in files:
        path = worktree / rel
        if not path.is_file() or path.stat().st_size > max_bytes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        chunks.append(f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n")
        chunks.extend(f"+{line}\n" for line in text.splitlines())
    return "".join(chunks)


def diff_text(worktree: pathlib.Path, base_ref: Optional[str] = None) -> str:
    if base_ref:
        cp = run(["git", "diff", "--unified=0", base_ref, "--", ":!*.lock"], cwd=worktree)
    else:
        cp = run(["git", "diff", "--unified=0", "--", ":!*.lock"], cwd=worktree)
    diff = cp.stdout if cp.returncode == 0 else ""
    diff += artificial_untracked_diff(worktree, untracked_files(worktree))
    return diff


def validate_codex_final(final_path: pathlib.Path) -> Tuple[bool, Dict[str, Any], List[str]]:
    errors: List[str] = []
    if not final_path.exists():
        return False, {}, ["codex.final.json was not written"]
    try:
        data = json.loads(final_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, {}, [f"codex.final.json is not valid JSON: {e}"]

    if not isinstance(data, dict):
        return False, data if isinstance(data, dict) else {}, ["final output must be a JSON object"]

    required = {
        "summary": str,
        "changed_files": list,
        "commands_run": list,
        "risks": list,
        "ready_for_review": bool,
    }
    allowed_keys = set(required)
    for key in data.keys():
        if key not in allowed_keys:
            errors.append(f"unexpected key: {key}")

    for key, typ in required.items():
        if key not in data:
            errors.append(f"missing required key: {key}")
        elif not isinstance(data[key], typ):
            errors.append(f"{key} must be {typ.__name__}")

    if isinstance(data.get("summary"), str) and not data["summary"].strip():
        errors.append("summary must be non-empty")
    if isinstance(data.get("changed_files"), list) and not all(isinstance(x, str) for x in data["changed_files"]):
        errors.append("changed_files must contain only strings")
    if isinstance(data.get("risks"), list) and not all(isinstance(x, str) for x in data["risks"]):
        errors.append("risks must contain only strings")
    if isinstance(data.get("commands_run"), list):
        for i, item in enumerate(data["commands_run"]):
            if not isinstance(item, dict):
                errors.append(f"commands_run[{i}] must be an object")
                continue
            if not isinstance(item.get("command"), str):
                errors.append(f"commands_run[{i}].command must be a string")
            if not isinstance(item.get("exit_code"), int):
                errors.append(f"commands_run[{i}].exit_code must be an integer")
    if data.get("ready_for_review") is not True:
        errors.append("ready_for_review must be true for merge queue admission")

    return len(errors) == 0, data, errors


def is_docs_or_comment_only_file(path: str) -> bool:
    lower = path.lower()
    return lower.endswith((".md", ".mdx", ".txt", ".rst", ".adoc")) or lower.startswith("docs/")


def is_config_file(path: str) -> bool:
    base = pathlib.PurePosixPath(path).name.lower()
    if base in {"package.json", "tsconfig.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml", "gemfile", "composer.json"}:
        return True
    return path.startswith((".config/", "config/")) or base.endswith((".config.js", ".config.ts", ".yml", ".yaml", ".toml", ".ini"))


def is_production_file(path: str) -> bool:
    if TEST_PATH_RE.search(path):
        return False
    if is_docs_or_comment_only_file(path):
        return False
    if is_config_file(path):
        return False
    if is_protected_path(path):
        return False
    return bool(PRODUCTION_PATH_RE.search(path))


def review_diff_impl(
    task: Dict[str, Any],
    worktree: pathlib.Path,
    base_ref: Optional[str] = None,
) -> Dict[str, Any]:
    files = changed_files(worktree, base_ref=base_ref)
    touched = task.get("touched_paths", [])
    diff = diff_text(worktree, base_ref=base_ref)
    findings: List[Dict[str, str]] = []
    kind = spec_kind(task["id"])

    for f in files:
        if is_protected_path(f):
            findings.append(
                {
                    "severity": "high",
                    "file": f,
                    "rule": "protected-path",
                    "message": "protected path was modified",
                }
            )
        if touched and not any(paths_overlap(f, p) for p in touched):
            if not TEST_PATH_RE.search(f):
                findings.append(
                    {
                        "severity": "medium",
                        "file": f,
                        "rule": "outside-expected-paths",
                        "message": "file is outside declared touched_paths",
                    }
                )
        if is_shared_path(f):
            findings.append(
                {
                    "severity": "medium",
                    "file": f,
                    "rule": "shared-resource",
                    "message": "shared resource requires serialized merge and manager review",
                }
            )

    suspicious_test = [
        (r"^[-+].*\b(skip|xit|xdescribe)\s*\(", "test-skip-or-only"),
        (r"^\+.*\b(\.only)\s*\(", "test-only"),
        (r"^-.*\b(expect|assert|should)\b", "assertion-removed"),
    ]
    for pattern, rule in suspicious_test:
        if re.search(pattern, diff, re.MULTILINE):
            findings.append(
                {
                    "severity": "high",
                    "file": "<diff>",
                    "rule": rule,
                    "message": "test weakening pattern detected",
                }
            )

    dep_patterns = [
        r"^\+.*\"dependencies\"",
        r"^\+.*\"devDependencies\"",
        r"^\+.*version\s*=",
        r"^\+.*groupId",
    ]
    if any(re.search(p, diff, re.MULTILINE) for p in dep_patterns) or any(
        is_shared_path(f) for f in files
    ):
        findings.append(
            {
                "severity": "medium",
                "file": "<diff>",
                "rule": "dependency-or-lockfile-change",
                "message": "dependency/lockfile related change requires manager review",
            }
        )

    production_files = [f for f in files if is_production_file(f)]
    test_files = [f for f in files if TEST_PATH_RE.search(f)]
    if production_files and not test_files and kind not in SPEC_BYPASS_KINDS and not task.get("_test_first_phase2"):
        findings.append(
            {
                "severity": "high",
                "file": "<diff>",
                "rule": "missing-tests-for-production-change",
                "message": "production code changed but no test file changed; declare spec kind refactor/docs/config only for allowed exceptions",
            }
        )

    # Medium warning for diffs that are too large for reliable autonomous review.
    try:
        ledger_settings = load_ledger().get("settings", {})
    except Exception:
        ledger_settings = {}
    max_lines = int(ledger_settings.get("diff_review_max_lines", DIFF_REVIEW_MAX_LINES) or DIFF_REVIEW_MAX_LINES)
    max_files = int(ledger_settings.get("diff_review_max_files", DIFF_REVIEW_MAX_FILES) or DIFF_REVIEW_MAX_FILES)
    counted_files = [f for f in files if not is_docs_or_comment_only_file(f) and not is_config_file(f)]
    changed_line_count = sum(1 for line in diff.splitlines() if (line.startswith("+") or line.startswith("-")) and not line.startswith("+++") and not line.startswith("---"))
    if len(counted_files) > max_files or changed_line_count > max_lines:
        findings.append({
            "severity": "medium",
            "file": "<diff>",
            "rule": "diff-too-large",
            "message": f"diff is large ({len(counted_files)} files, {changed_line_count} changed lines); split into subtasks",
        })

    protected_findings = [x for x in findings if x.get("rule") == "protected-path"]
    if protected_findings:
        try:
            append_event("claude", "protected_path.detected", task["id"], {"findings": protected_findings})
        except Exception:
            pass

    coverage = acceptance_test_coverage(worktree, task["id"], files)
    if production_files and test_files and coverage.get("missing") and kind not in SPEC_BYPASS_KINDS and not task.get("_test_first_phase2"):
        findings.append(
            {
                "severity": "medium",
                "file": "<tests>",
                "rule": "acceptance-test-coverage-heuristic",
                "message": "some spec acceptance criteria were not heuristically matched in changed tests",
            }
        )

    settings = load_ledger().get("settings", {})
    max_files = int(settings.get("diff_review_max_files", DIFF_REVIEW_MAX_FILES) or DIFF_REVIEW_MAX_FILES)
    max_lines = int(settings.get("diff_review_max_lines", DIFF_REVIEW_MAX_LINES) or DIFF_REVIEW_MAX_LINES)
    counted_files = [f for f in files if not is_docs_or_comment_only_file(f) and not is_config_file(f)]
    changed_line_count = count_changed_lines(diff)
    if len(counted_files) > max_files or changed_line_count > max_lines:
        findings.append(
            {
                "severity": "medium",
                "file": "<diff>",
                "rule": "diff-too-large",
                "message": f"diff is large ({len(counted_files)} files, {changed_line_count} changed lines); split into subtasks",
            }
        )

    protected_findings = [x for x in findings if x.get("rule") == "protected-path"]
    if protected_findings:
        append_event("claude", "protected_path.modified", task["id"], {"findings": protected_findings})

    high = sum(1 for x in findings if x["severity"] == "high")
    medium = sum(1 for x in findings if x["severity"] == "medium")
    return {
        "task_id": task["id"],
        "base_ref": base_ref or "",
        "spec_kind": kind,
        "changed_files": files,
        "production_files": production_files,
        "test_files": test_files,
        "acceptance_test_coverage": coverage,
        "diff_metrics": {"counted_files": len(counted_files), "changed_lines": changed_line_count, "max_files": max_files, "max_lines": max_lines},
        "findings": findings,
        "high": high,
        "medium": medium,
        "approved": high == 0,
    }


def validation_env(root: pathlib.Path) -> Dict[str, str]:
    env = os.environ.copy()
    p = ensure_dirs(root)
    env.update(
        {
            "npm_config_cache": str(p["cache"] / "npm"),
            "PIP_CACHE_DIR": str(p["cache"] / "pip"),
            "CARGO_HOME": str(p["cache"] / "cargo"),
            "GOMODCACHE": str(p["cache"] / "go"),
            "TMPDIR": str(p["cache"] / "tmp"),
            "CI": env.get("CI", "1"),
            "TZ": env.get("TZ", "UTC"),
            "LC_ALL": env.get("LC_ALL", "C.UTF-8"),
        }
    )
    return env


def install_cache_key(worktree: pathlib.Path, install_cmd: str) -> str:
    h = hashlib.sha1()
    h.update(install_cmd.encode("utf-8"))
    matched: List[pathlib.Path] = []
    for pattern in INSTALL_INPUT_GLOBS:
        matched.extend(worktree.glob(pattern))
    for path in sorted(set(matched), key=lambda p: str(p)):
        if path.is_file():
            rel = path.relative_to(worktree).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def install_stamp_path(root: pathlib.Path, worktree: pathlib.Path, key: str) -> pathlib.Path:
    wt_hash = hashlib.sha1(str(worktree.resolve()).encode("utf-8")).hexdigest()[:12]
    return ensure_dirs(root)["cache"] / "installed" / f"{wt_hash}-{key}.json"


def run_install_if_needed(
    root: pathlib.Path,
    worktree: pathlib.Path,
    install_cmd: str,
    log,
    timeout_per_cmd: int,
    env: Dict[str, str],
) -> Dict[str, Any]:
    cmd = install_cmd.strip()
    if not cmd:
        return {"name": "install", "command": "", "exit_code": 0, "skipped": True, "reason": "no install command"}

    key = install_cache_key(worktree, cmd)
    stamp = install_stamp_path(root, worktree, key)
    if stamp.exists():
        log.write(f"\n===== {now()} install: skipped, cache key {key} already installed for this worktree =====\n")
        return {"name": "install", "command": cmd, "exit_code": 0, "skipped": True, "cache_key": key}

    log.write(f"\n===== {now()} install: {cmd} =====\n")
    try:
        cp = shell(cmd, worktree, timeout=timeout_per_cmd, env=env)
        log.write(cp.stdout)
        log.write(cp.stderr)
        result = {"name": "install", "command": cmd, "exit_code": cp.returncode, "cache_key": key}
    except subprocess.TimeoutExpired as e:
        result = {"name": "install", "command": cmd, "exit_code": 124, "timeout": timeout_per_cmd, "cache_key": key}
        log.write(f"TIMEOUT after {timeout_per_cmd}s\n{e}\n")

    if result["exit_code"] == 0:
        write_json(stamp, {"installed_at": now(), "worktree": str(worktree), "command": cmd, "cache_key": key})
    return result


def run_validation(
    root: pathlib.Path,
    worktree: pathlib.Path,
    ledger: Dict[str, Any],
    log_path: pathlib.Path,
    timeout_per_cmd: int = 1200,
) -> Tuple[bool, List[Dict[str, Any]]]:
    commands = ledger.get("commands", {})
    results: List[Dict[str, Any]] = []
    env = validation_env(root)

    with log_path.open("a", encoding="utf-8") as log:
        install_result = run_install_if_needed(
            root,
            worktree,
            commands.get("install", ""),
            log,
            timeout_per_cmd,
            env,
        )
        results.append(install_result)
        if install_result["exit_code"] != 0:
            return False, results

        for name in ["lint", "typecheck", "test", "build"]:
            cmd = (commands.get(name) or "").strip()
            if not cmd:
                if name == "test":
                    msg = "test command is empty; validation cannot prove required tests run"
                    log.write(f"\n===== {now()} test: WARNING: {msg} =====\n")
                    results.append({"name": "test", "command": "", "exit_code": 0, "skipped": True, "warning": msg})
                continue

            log.write(f"\n===== {now()} {name}: {cmd} =====\n")
            try:
                cp = shell(cmd, worktree, timeout=timeout_per_cmd, env=env)
                log.write(cp.stdout)
                log.write(cp.stderr)
                result = {"name": name, "command": cmd, "exit_code": cp.returncode}
            except subprocess.TimeoutExpired as e:
                result = {
                    "name": name,
                    "command": cmd,
                    "exit_code": 124,
                    "timeout": timeout_per_cmd,
                }
                log.write(f"TIMEOUT after {timeout_per_cmd}s\n{e}\n")

            results.append(result)
            if result["exit_code"] != 0:
                return False, results

    return True, results


def validate_codex_review(final_path: pathlib.Path) -> Tuple[bool, Dict[str, Any], List[str]]:
    # Source of truth for this contract; keep codex-review.schema.json in sync.
    errors: List[str] = []
    if not final_path.exists():
        return False, {}, ["codex.review.final.json was not written"]
    try:
        data = json.loads(final_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, {}, [f"codex.review.final.json is not valid JSON: {e}"]
    if not isinstance(data, dict):
        return False, {}, ["review final output must be a JSON object"]
    required = {
        "verdict": str,
        "summary": str,
        "findings": list,
        "spec_drift": list,
        "missing_tests": list,
        "ready_to_merge": bool,
    }
    allowed = set(required)
    for key in data:
        if key not in allowed:
            errors.append(f"unexpected key: {key}")
    for key, typ in required.items():
        if key not in data:
            errors.append(f"missing required key: {key}")
        elif not isinstance(data[key], typ):
            errors.append(f"{key} must be {typ.__name__}")
    if data.get("verdict") not in {"approve", "request_changes", "reject"}:
        errors.append("verdict must be approve, request_changes, or reject")
    if isinstance(data.get("summary"), str) and not data["summary"].strip():
        errors.append("summary must be non-empty")
    for key in ["spec_drift", "missing_tests"]:
        if isinstance(data.get(key), list) and not all(isinstance(x, str) for x in data[key]):
            errors.append(f"{key} must contain only strings")
    if isinstance(data.get("findings"), list):
        for i, item in enumerate(data["findings"]):
            if not isinstance(item, dict):
                errors.append(f"findings[{i}] must be object")
                continue
            for k in ["severity", "category", "file", "message"]:
                if k not in item:
                    errors.append(f"findings[{i}] missing {k}")
            if item.get("severity") not in {"high", "medium", "low"}:
                errors.append(f"findings[{i}].severity invalid")
            if item.get("category") not in {"correctness", "spec_drift", "test_coverage", "security", "style", "other"}:
                errors.append(f"findings[{i}].category invalid")
            if "line" in item and not isinstance(item.get("line"), int):
                errors.append(f"findings[{i}].line must be integer")
            extra = set(item) - {"severity", "category", "file", "line", "message"}
            if extra:
                errors.append(f"findings[{i}] unexpected keys: {sorted(extra)}")
    return len(errors) == 0, data, errors


def build_codex_review_prompt(
    task: Dict[str, Any],
    worktree: pathlib.Path,
    patch_max_bytes: int = PATCH_REVIEW_MAX_BYTES,
    artifact_dir: Optional[pathlib.Path] = None,
    phase: str = "single",
) -> Tuple[str, Dict[str, Any]]:
    td = artifact_dir or task_dir(task["id"])
    fm, spec_body, spec_file = read_spec(task["id"])
    spec_text = spec_file.read_text(encoding="utf-8") if spec_file.exists() else "LEGACY TASK: no spec.md found."
    patch_text = (td / "last.patch").read_text(encoding="utf-8", errors="replace") if (td / "last.patch").exists() else diff_text(worktree, task.get("last_dispatch_head") or task.get("base_ref") or None)
    patch_bytes = len(patch_text.encode("utf-8", errors="replace"))
    encoded = patch_text.encode("utf-8", errors="replace")
    truncated = len(encoded) > patch_max_bytes
    if truncated:
        shown_patch = encoded[:patch_max_bytes].decode("utf-8", errors="replace")
        truncation_notice = f"\n[patch truncated: shown {len(shown_patch.encode('utf-8', errors='replace'))} of {patch_bytes} bytes; reviewer must mark verdict=request_changes if full diff is needed]"
    else:
        shown_patch = patch_text
        truncation_notice = ""
    static_review = read_json(td / "review.json", {})
    validation = read_json(td / "validation.json", {})
    agents = (worktree / "AGENTS.md").read_text(encoding="utf-8", errors="replace") if (worktree / "AGENTS.md").exists() else ""
    metadata = {
        "patch_truncated": truncated,
        "patch_full_bytes": patch_bytes,
        "patch_shown_bytes": len(shown_patch.encode("utf-8", errors="replace")),
        "patch_max_bytes": patch_max_bytes,
    }
    prompt = f"""You are Codex CLI running as an independent semantic reviewer, not the implementation worker.

Review task {task['id']}: {task.get('title', '')}
Phase: {phase}

Your job:
- Compare the implementation patch against spec.md.
- Check correctness, spec drift, test coverage, security, and maintainability.
- Do not modify files. You are in a read-only sandbox.
- Do not approve if tests are missing for behavior changes.
- Do not approve if static review had high severity findings.
- If the patch is truncated and you need the missing part to decide, set verdict=request_changes and ready_to_merge=false.
- Return only JSON matching the provided schema.

<agents_md><![CDATA[{cdata(agents)}]]></agents_md>

<spec><![CDATA[{cdata(spec_text)}]]></spec>

<static_review_result><![CDATA[{cdata(json.dumps(static_review, ensure_ascii=False, indent=2))}]]></static_review_result>

<validation_result><![CDATA[{cdata(json.dumps(validation, ensure_ascii=False, indent=2))}]]></validation_result>

<patch truncated="{'true' if truncated else 'false'}" full_bytes="{patch_bytes}"><![CDATA[{cdata(shown_patch + truncation_notice)}]]></patch>

Required JSON shape:
{{
  "verdict": "approve | request_changes | reject",
  "summary": "non-empty summary",
  "findings": [{{"severity":"high|medium|low","category":"correctness|spec_drift|test_coverage|security|style|other","file":"path","line":1,"message":"..."}}],
  "spec_drift": ["..."],
  "missing_tests": ["..."],
  "ready_to_merge": true
}}
"""
    return prompt, metadata

def run_codex_review_impl(
    root: pathlib.Path,
    ledger: Dict[str, Any],
    task: Dict[str, Any],
    codex_bin: str = "codex",
    model: str = "",
    profile: str = "",
    timeout: int = 3600,
    allow_network: bool = False,
    patch_max_bytes: int = PATCH_REVIEW_MAX_BYTES,
    artifact_dir: Optional[pathlib.Path] = None,
    phase: str = "single",
) -> Tuple[bool, Dict[str, Any]]:
    wt = pathlib.Path(task.get("worktree") or root).resolve()
    td = artifact_dir or task_dir(task["id"])
    td.mkdir(parents=True, exist_ok=True)
    prompt, review_prompt_meta = build_codex_review_prompt(task, wt, patch_max_bytes=patch_max_bytes, artifact_dir=td, phase=phase)
    (td / "codex.review.prompt.md").write_text(prompt, encoding="utf-8")
    stdout_jsonl = td / "codex.review.stdout.jsonl"
    stderr_log = td / "codex.review.stderr.log"
    final_msg = td / "codex.review.final.json"
    schema = ensure_dirs(root)["schemas"] / "codex-review.schema.json"
    cmd = [
        codex_bin,
        "exec",
        "--cd",
        str(wt),
        "--ask-for-approval",
        "never",
        "--sandbox",
        "read-only",
        "--json",
        "--output-last-message",
        str(final_msg),
    ]
    if schema.exists():
        cmd += ["--output-schema", str(schema)]
    if model:
        cmd += ["--model", model]
    if profile:
        cmd += ["--profile", profile]
    if not allow_network:
        cmd += ["-c", "sandbox_workspace_write.network_access=false"]
    cmd.append("-")
    env = validation_env(root)
    env["ORCH_TASK_ID"] = task["id"]
    env["ORCH_REVIEW_ROLE"] = "semantic-reviewer"
    start = time.time()
    with stdout_jsonl.open("wb") as out, stderr_log.open("wb") as err:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            env=env,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
        try:
            proc.communicate(prompt.encode("utf-8"), timeout=timeout)
            rc = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_group(proc)
            rc = 124
    elapsed = round(time.time() - start, 3)
    stats = parse_jsonl(stdout_jsonl)
    ok, data, errors = validate_codex_review(final_msg)
    write_json(
        td / "review-exit.json",
        {
            "returncode": rc,
            "timed_out": timed_out,
            "elapsed_seconds": elapsed,
            "jsonl_stats": stats,
            "command": cmd,
            "schema_ok": ok,
            "schema_errors": errors,
            "patch_truncated": review_prompt_meta.get("patch_truncated", False),
            "patch_full_bytes": review_prompt_meta.get("patch_full_bytes", 0),
            "patch_shown_bytes": review_prompt_meta.get("patch_shown_bytes", 0),
            "patch_max_bytes": review_prompt_meta.get("patch_max_bytes", patch_max_bytes),
            "effective_model": effective_model_for_run(root, model, profile),
        },
    )
    write_json(td / "codex.review.validation.json", {"ok": ok, "errors": errors, "data": data})
    approved = rc == 0 and not timed_out and ok and data.get("verdict") == "approve" and data.get("ready_to_merge") is True
    task["codex_review"] = {
        "approved": approved,
        "verdict": data.get("verdict") if data else "invalid",
        "ready_to_merge": bool(data.get("ready_to_merge")) if data else False,
        "reviewed_at": now(),
        "final": str(final_msg),
        "validation": str(td / "codex.review.validation.json"),
        "exit": str(td / "review-exit.json"),
    }
    if not approved:
        task["last_reason"] = "codex semantic review did not approve merge"
    save_ledger(ledger)
    append_event("codex", "codex.semantic_review", task["id"], task["codex_review"])
    return approved, data


def add_merge_queue(item: Dict[str, Any]) -> None:
    p = ensure_dirs()
    with AtomicLock("merge-queue", timeout=120):
        queue = read_json(p["queue"], [])
        if not any(q.get("task_id") == item.get("task_id") for q in queue):
            queue.append(item)
            write_json(p["queue"], queue)


def kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        time.sleep(2)
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def cmd_init_detect(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root).resolve() if args.root else git_root()
    if not args.dry_run:
        ensure_dirs(root)
    data = detect_project(root)
    if args.apply_gitignore and not args.dry_run:
        data["gitignore"] = ensure_gitignore_block(root)
    elif args.apply_gitignore and args.dry_run:
        data["gitignore"] = {"dry_run": True, "changed": False, "note": "--dry-run does not modify .gitignore"}
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"root: {data['root']}")
        print("stacks:", ", ".join(x.get("name", "unknown") for x in data["stacks"]) or "none")
        print("monorepo:", ", ".join(data["monorepo"]) or "no")
        print("recommended commands:")
        for k, v in data["recommended_commands"].items():
            print(f"  {k}: {v}")
        print("collisions:")
        for k, v in data["collisions"].items():
            print(f"  {k}: {'exists' if v else 'absent'}")
    if not args.dry_run:
        append_event("claude", "init.detect", data=data)
    return 0



def cmd_codex_status(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root).resolve() if getattr(args, "root", "") else git_root()
    data = codex_preconditions(root)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"Codex CLI: installed ({data['version']})" if data.get("installed") else "Codex CLI: not installed")
        print(f"Authentication: {'ok' if data.get('authenticated') else 'not authenticated or unknown'}")
        print(f"User config:    ~/.codex/config.toml  model={data.get('user_config_model') or 'none'}")
        if data.get("user_config_profile"):
            print(f"                profile={data.get('user_config_profile')} profile_model={data.get('user_config_profile_model') or 'none'}")
        print(f"Project config: .codex/config.toml    model={data.get('project_config_model') or 'none'}")
        if data.get("project_config_profile"):
            print(f"                profile={data.get('project_config_profile')} profile_model={data.get('project_config_profile_model') or 'none'}")
        print(f"Effective model for codex-dispatch: {data.get('effective_model')}  (source: {data.get('effective_model_source')})")
        print(f"Project trust state: {data.get('trust_state')}")
        if args.suggest:
            suggestions: List[str] = []
            if not data.get("installed"):
                suggestions.append("Install Codex CLI: npm i -g @openai/codex or brew install --cask codex")
            if data.get("installed") and not data.get("authenticated"):
                suggestions.append("Authenticate: codex login")
            if data.get("trust_state") != "trusted":
                suggestions.append("Trust this project: run `codex` once from the repository root and accept the trust prompt if shown.")
            if data.get("effective_model_source") in {"codex-builtin", "unknown"}:
                suggestions.append("Set a project default by copying .codex/config.toml and uncommenting `model = \"<model-name>\"` after choosing a currently available model.")
            if suggestions:
                print("\nSuggestions:")
                for item in suggestions:
                    print(f"- {item}")
    return 0


def spec_version_file(task_id: str, version: int) -> pathlib.Path:
    return task_dir(task_id) / f"spec.v{version}.md"


def update_spec_history(ledger: Dict[str, Any], task: Dict[str, Any], version: int, path: pathlib.Path) -> None:
    hist = task.setdefault("spec_history", [])
    if not any(int(x.get("version", -1)) == version for x in hist if isinstance(x, dict)):
        hist.append({"version": version, "approved_at": now(), "path": str(path)})
    task["spec_history"] = sorted(hist, key=lambda x: int(x.get("version", 0)))


def cmd_spec(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    if args.spec_cmd in {"create", "update", "approve", "show", "validate", "history", "diff"}:
        task = find_task(ledger, args.task_id)
        sp = spec_path(args.task_id)

    if args.spec_cmd == "create":
        if sp.exists() and not args.force:
            raise RuntimeError(f"spec already exists: {sp}; use --force to overwrite")
        text = spec_template(task, args.kind)
        sp.write_text(text, encoding="utf-8")
        task["status"] = "spec_draft"
        task["spec"] = str(sp)
        task["updated_at"] = now()
        save_ledger(ledger)
        append_event("claude", "spec.created", args.task_id, {"path": str(sp), "kind": args.kind})
        print(str(sp))
        return 0

    if args.spec_cmd == "update":
        if args.file:
            text = pathlib.Path(args.file).read_text(encoding="utf-8")
        else:
            text = sys.stdin.read()
        if not text.strip():
            raise RuntimeError("empty spec update")
        sp.write_text(text, encoding="utf-8")
        task["status"] = "spec_draft"
        task["spec"] = str(sp)
        task["updated_at"] = now()
        save_ledger(ledger)
        append_event("claude", "spec.updated", args.task_id, {"path": str(sp)})
        print(str(sp))
        return 0

    if args.spec_cmd == "show":
        print(sp.read_text(encoding="utf-8"))
        return 0

    if args.spec_cmd == "validate":
        ok, errors, fm = validate_spec_file(sp, args.task_id)
        out = {"ok": ok, "errors": errors, "frontmatter": fm, "path": str(sp)}
        write_json(task_dir(args.task_id) / "spec.validation.json", out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if ok else 2

    if args.spec_cmd == "approve":
        ok, errors, fm = validate_spec_file(sp, args.task_id)
        if not ok:
            print(json.dumps({"ok": ok, "errors": errors}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        text = sp.read_text(encoding="utf-8")
        front, body = split_frontmatter(text)
        if isinstance(front.get("version"), str) and str(front.get("version")).isdigit():
            front["version"] = int(front["version"])
        old_version = int(front.get("version") or 1)
        history = task.setdefault("spec_history", [])
        if history:
            archived = task_dir(args.task_id) / f"spec.v{old_version}.md"
            shutil.copyfile(sp, archived)
            # Move the previous current-spec history entry to its archived file.
            for entry in history:
                if int(entry.get("version", -1)) == old_version and pathlib.Path(str(entry.get("path", ""))).name == "spec.md":
                    entry["path"] = str(archived)
            front["version"] = old_version + 1
        front["status"] = "approved"
        front["owner"] = "claude"
        sp.write_text(render_frontmatter(front) + body, encoding="utf-8")
        task["status"] = "spec_approved"
        task["spec"] = str(sp)
        task["updated_at"] = now()
        version = int(front.get("version") or old_version)
        history = [h for h in history if int(h.get("version", -1)) != version]
        history.append({"version": version, "approved_at": now(), "path": str(sp)})
        task["spec_history"] = sorted(history, key=lambda x: int(x.get("version", 0)))
        save_ledger(ledger)
        append_event("claude", "spec.approved", args.task_id, {"path": str(sp), "version": version})
        print(json.dumps({"ok": True, "path": str(sp), "frontmatter": front, "spec_history": task["spec_history"]}, ensure_ascii=False, indent=2))
        return 0

    if args.spec_cmd == "history":
        print(json.dumps(task.get("spec_history", []), ensure_ascii=False, indent=2))
        return 0

    if args.spec_cmd == "diff":
        from_v = int(str(args.from_version).lstrip("vV"))
        to_v = int(str(args.to_version).lstrip("vV"))
        history = task.get("spec_history", [])
        def path_for(version: int) -> pathlib.Path:
            for entry in history:
                if int(entry.get("version", -1)) == version:
                    pth = pathlib.Path(str(entry.get("path", "")))
                    if not pth.is_absolute():
                        pth = git_root() / pth
                    return pth
            candidate = task_dir(args.task_id) / f"spec.v{version}.md"
            if candidate.exists():
                return candidate
            fm_now, _body_now, sp_now = read_spec(args.task_id)
            if int(fm_now.get("version", -999)) == version:
                return sp_now
            raise RuntimeError(f"spec version not found: v{version}")
        a_path = path_for(from_v)
        b_path = path_for(to_v)
        a = a_path.read_text(encoding="utf-8").splitlines(keepends=True)
        b = b_path.read_text(encoding="utf-8").splitlines(keepends=True)
        sys.stdout.writelines(difflib.unified_diff(a, b, fromfile=f"spec.v{from_v}.md", tofile=f"spec.v{to_v}.md"))
        return 0

    raise ValueError(args.spec_cmd)


def cmd_codex_review(args: argparse.Namespace) -> int:
    root = git_root()
    ledger = load_ledger()
    task = find_task(ledger, args.task_id)
    if not args.allow_legacy and not spec_path(args.task_id).exists():
        raise RuntimeError("spec.md is required for codex-review; use --allow-legacy for old v1 tasks")
    if not pathlib.Path(task.get("worktree") or "").exists():
        raise RuntimeError("task worktree is missing; dispatch the task first")
    old_status = task.get("status")
    task["status"] = "codex_review"
    task["updated_at"] = now()
    save_ledger(ledger)
    ok, data = run_codex_review_impl(
        root,
        ledger,
        task,
        codex_bin=args.codex,
        model=args.review_model or args.model or "",
        profile=args.profile or "",
        timeout=args.timeout,
        allow_network=args.allow_network,
        patch_max_bytes=args.review_patch_max_bytes,
    )
    task = find_task(ledger, args.task_id)
    task["status"] = "review"
    task["updated_at"] = now()
    save_ledger(ledger)
    print(json.dumps({"approved": ok, "result": data, "previous_status": old_status}, ensure_ascii=False, indent=2))
    return 0 if ok else 6


def build_retry_prompt(
    task: Dict[str, Any],
    ledger: Dict[str, Any],
    previous_attempt_dir: pathlib.Path,
    retry_strategy_text: str,
    allowed_paths_override: Optional[List[str]] = None,
) -> str:
    validation = read_json(previous_attempt_dir / "validation.json", {})
    static_review = read_json(previous_attempt_dir / "review.json", {})
    codex_review = read_json(previous_attempt_dir / "codex.review.validation.json", {})
    stderr = ""
    stderr_file = previous_attempt_dir / "codex.stderr.log"
    if stderr_file.exists():
        stderr = stderr_file.read_text(encoding="utf-8", errors="replace")[-20000:]

    attempts = int(task.get("attempts", 0))
    allowed_paths = allowed_paths_override or task.get("touched_paths", [])
    lines = [
        '<retry_dispatch_context>',
        '  <task_context>',
        f'    <task_id>{xml_escape(task.get("id", ""))}</task_id>',
        f'    <attempt>{attempts + 1}</attempt>',
        f'    <previous_attempts>{attempts}</previous_attempts>',
        '  </task_context>',
        '  <previous_failure>',
        f'    <stderr><![CDATA[{cdata(stderr)}]]></stderr>',
        '    <validation_results>',
    ]
    for item in validation.get("results", []) if isinstance(validation, dict) else []:
        lines.append(
            f'      <command name="{xml_escape(item.get("name", ""))}" exit_code="{xml_escape(item.get("exit_code", ""))}"><![CDATA[{cdata(item.get("command", ""))}]]></command>'
        )
    lines.extend(['    </validation_results>', '    <static_review_findings>'])
    for finding in static_review.get("findings", []) if isinstance(static_review, dict) else []:
        attrs = {
            "severity": finding.get("severity", ""),
            "category": finding.get("category", finding.get("rule", "other")),
            "file": finding.get("file", ""),
            "rule": finding.get("rule", ""),
        }
        attr_text = " ".join(f'{k}="{xml_escape(v)}"' for k, v in attrs.items() if v != "")
        lines.append(f'      <finding {attr_text}><![CDATA[{cdata(finding.get("message", ""))}]]></finding>')
    lines.extend(['    </static_review_findings>', '    <codex_review_findings>'])
    data = codex_review.get("data", {}) if isinstance(codex_review, dict) else {}
    for finding in data.get("findings", []) if isinstance(data, dict) else []:
        attr_text = " ".join(
            f'{k}="{xml_escape(finding.get(k, ""))}"'
            for k in ["severity", "category", "file"]
            if finding.get(k, "") != ""
        )
        lines.append(f'      <finding {attr_text}><![CDATA[{cdata(finding.get("message", ""))}]]></finding>')
    lines.extend([
        '    </codex_review_findings>',
        '  </previous_failure>',
        f'  <retry_strategy><![CDATA[{cdata(retry_strategy_text)}]]></retry_strategy>',
        xml_path_list("allowed_paths", allowed_paths),
        '  <unchanged_constraints><![CDATA[Follow AGENTS.md, the task spec, protected-path rules, test-required policy, and JSON-only final output. Do not expand scope.]]></unchanged_constraints>',
        '</retry_dispatch_context>',
    ])
    return "\n".join(lines)


def build_parallel_worker_contract(task: Dict[str, Any], selected_tasks: List[Dict[str, Any]]) -> str:
    other_paths: List[str] = []
    for other in selected_tasks:
        if other.get("id") != task.get("id"):
            other_paths.extend(other.get("touched_paths", []))
    shared_locked = [
        "package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lockb",
        "Cargo.lock", "go.sum", "poetry.lock", "uv.lock", "migrations/**",
        "db/migrate/**", "openapi*.json", "openapi*.yaml", "schema.graphql",
        "schema.prisma", ".github/workflows/**",
    ]
    return "\n".join([
        "<parallel_worker_contract>",
        xml_path_list("assigned_paths", task.get("touched_paths", [])),
        xml_path_list("forbidden_paths", sorted(set(other_paths))),
        xml_path_list("shared_resources_locked", shared_locked),
        "</parallel_worker_contract>",
    ])

def cmd_dispatch_single(args: argparse.Namespace) -> int:
    root = git_root()
    ledger = load_ledger()
    task = find_task(ledger, args.task_id)
    fm_mode = ""
    if spec_path(args.task_id).exists():
        try:
            fm_mode = str(read_spec(args.task_id)[0].get("mode") or "")
        except Exception:
            fm_mode = ""
    dispatch_mode = args.mode or fm_mode or "single"
    if dispatch_mode == "test-first":
        return cmd_dispatch_test_first(args)

    if task.get("status") not in {"spec_approved", "pending", "assigned", "failed", "blocked"}:
        raise RuntimeError(f"task {args.task_id} is not dispatchable: {task.get('status')}")

    spec_file = spec_path(args.task_id)
    spec_text = ""
    if spec_file.exists():
        ok, spec_errors, _fm = validate_spec_file(spec_file, args.task_id)
        if not ok and not args.no_spec:
            raise RuntimeError("spec.md failed validation: " + "; ".join(spec_errors))
        spec_text = spec_file.read_text(encoding="utf-8")
    elif args.no_spec or args.allow_legacy:
        append_event("claude", "spec.bypass", args.task_id, {"no_spec": args.no_spec, "allow_legacy": args.allow_legacy})
    else:
        raise RuntimeError("spec.md is required before dispatch. Create it with `.orchestration/bin/spec create <task-id>` or use --allow-legacy for v1 tasks / --no-spec for audited bypass.")

    with AtomicLock(f"task-{args.task_id}", timeout=30, stale_after=max(args.timeout * 2, 3600)):
        wt = ensure_worktree(root, task, args.base_ref)
        pre_head = current_head(wt)
        task["last_dispatch_head"] = pre_head
        task["status"] = "running"
        task["attempts"] = int(task.get("attempts", 0)) + 1
        task["updated_at"] = now()
        save_ledger(ledger)

        append_event(
            "claude",
            "codex.dispatch.start",
            args.task_id,
            {"worktree": str(wt), "branch": task["branch"], "pre_head": pre_head},
        )

        td = task_dir(args.task_id)
        extra = pathlib.Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else ""
        retry_xml = ""
        parallel_xml = ""
        if extra.strip().startswith("<parallel_worker_contract>"):
            parallel_xml = extra
            extra = ""
        retry_strategy_text = extra
        if args.retry_context:
            prev_task = find_task(ledger, args.retry_context)
            retry_xml = build_retry_prompt(
                task,
                ledger,
                task_dir(args.retry_context),
                retry_strategy_text or "Retry with a narrower, evidence-based fix. Do not repeat the previous failing approach.",
                allowed_paths_override=task.get("touched_paths", []),
            )
            append_event("claude", "retry.context.attached", args.task_id, {"retry_context": args.retry_context})
            extra = ""
        prompt = build_prompt(
            task,
            ledger,
            extra,
            spec_text=spec_text,
            retry_prompt_xml=retry_xml,
            parallel_contract_xml=parallel_xml,
        )
        (td / "prompt.md").write_text(prompt, encoding="utf-8")

        stdout_jsonl = td / "codex.stdout.jsonl"
        stderr_log = td / "codex.stderr.log"
        final_msg = td / "codex.final.json"
        schema = ensure_dirs(root)["schemas"] / "task-output.schema.json"

        cmd = [
            args.codex,
            "exec",
            "--cd",
            str(wt),
            "--ask-for-approval",
            "never",
            "--sandbox",
            args.sandbox,
            "--json",
            "--output-last-message",
            str(final_msg),
        ]
        if schema.exists():
            cmd += ["--output-schema", str(schema)]
        if args.model:
            cmd += ["--model", args.model]
        if args.profile:
            cmd += ["--profile", args.profile]
        if not args.allow_network:
            cmd += ["-c", "sandbox_workspace_write.network_access=false"]
        cmd.append("-")

        env = validation_env(root)
        env["ORCH_TASK_ID"] = args.task_id

        start = time.time()
        with stdout_jsonl.open("wb") as out, stderr_log.open("wb") as err:
            proc = subprocess.Popen(
                cmd,
                cwd=str(root),
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                env=env,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
            try:
                proc.communicate(prompt.encode("utf-8"), timeout=args.timeout)
                rc = proc.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_process_group(proc)
                rc = 124

        elapsed = round(time.time() - start, 3)
        stats = parse_jsonl(stdout_jsonl)
        task["codex_session_id"] = stats.get("thread_id", "")

        write_json(
            td / "exit.json",
            {
                "returncode": rc,
                "timed_out": timed_out,
                "elapsed_seconds": elapsed,
                "jsonl_stats": stats,
                "command": cmd,
                "pre_head": pre_head,
                "effective_model": effective_model_for_run(root, args.model, args.profile),
            },
        )

        if rc != 0 or timed_out or stats.get("turn_failed"):
            task["status"] = "failed"
            task["last_reason"] = "codex process failed or timed out"
            task["updated_at"] = now()
            save_ledger(ledger)
            append_event(
                "codex",
                "codex.dispatch.failed",
                args.task_id,
                {"returncode": rc, "timed_out": timed_out},
            )
            return rc or 1

        final_ok, final_data, final_errors = validate_codex_final(final_msg)
        write_json(td / "codex.final.validation.json", {"ok": final_ok, "errors": final_errors, "data": final_data})
        if not final_ok:
            task["status"] = "failed"
            task["last_reason"] = "codex final output did not match required schema"
            task["updated_at"] = now()
            save_ledger(ledger)
            append_event("codex", "codex.final.invalid", args.task_id, {"errors": final_errors})
            return 4

        files = changed_files(wt, base_ref=pre_head)
        if not files:
            task["status"] = "failed"
            task["last_reason"] = "codex produced no file changes"
            task["updated_at"] = now()
            save_ledger(ledger)
            append_event("codex", "codex.dispatch.no_changes", args.task_id, {"pre_head": pre_head})
            return 1

        report = review_diff_impl(task, wt, base_ref=pre_head)
        write_json(td / "review.json", report)

        if not report["approved"] and not args.allow_review_warnings:
            task["status"] = "failed"
            task["last_reason"] = "diff-reviewer high severity findings"
            task["updated_at"] = now()
            save_ledger(ledger)
            append_event("claude", "diff.review.rejected", args.task_id, report)
            return 2

        # Commit Codex-produced uncommitted changes before validation. Validation and install
        # commands must not create new repository changes; otherwise generated artifacts
        # could be accidentally committed without review.
        run(["git", "add", "-A"], cwd=wt, check=True)
        if run(["git", "diff", "--cached", "--quiet"], cwd=wt).returncode != 0:
            msg = f"chore(codex): complete {task['id']} {task.get('slug', 'task')}"
            run(["git", "commit", "-m", msg], cwd=wt, check=True)

        files = changed_files(wt, base_ref=pre_head)

        if args.validate:
            ok, results = run_validation(root, wt, ledger, td / "validation.log", args.validation_timeout)
            write_json(td / "validation.json", {"ok": ok, "results": results})
            if not ok:
                task["status"] = "failed"
                task["last_reason"] = "validation command failed"
                task["updated_at"] = now()
                save_ledger(ledger)
                append_event("claude", "validation.failed", args.task_id, {"results": results})
                return 3

            dirty_after_validation = porcelain_files(wt)
            if dirty_after_validation:
                write_json(td / "validation-dirty.json", {"dirty_files": dirty_after_validation})
                task["status"] = "failed"
                task["last_reason"] = "validation generated uncommitted repository changes"
                task["updated_at"] = now()
                save_ledger(ledger)
                append_event("claude", "validation.generated_changes", args.task_id, {"dirty_files": dirty_after_validation})
                return 5

        rev_count_cp = run(["git", "rev-list", "--count", f"{pre_head}..HEAD"], cwd=wt, check=True)
        if int((rev_count_cp.stdout or "0").strip() or "0") > 0:
            patch_cp = run(["git", "format-patch", "--stdout", f"{pre_head}..HEAD"], cwd=wt)
        else:
            patch_cp = run(["git", "diff", pre_head], cwd=wt)
        (td / "last.patch").write_text(patch_cp.stdout, encoding="utf-8")

        task["status"] = "review"
        task["updated_at"] = now()
        task["artifacts"] = {
            "stdout_jsonl": str(stdout_jsonl),
            "stderr": str(stderr_log),
            "final": str(final_msg),
            "final_validation": str(td / "codex.final.validation.json"),
            "review": str(td / "review.json"),
            "patch": str(td / "last.patch"),
        }
        save_ledger(ledger)

        codex_review_bypassed = False
        if args.skip_codex_review:
            codex_review_bypassed = True
            task["codex_review"] = {"approved": False, "verdict": "skipped", "ready_to_merge": False, "reviewed_at": now()}
            save_ledger(ledger)
            append_event("claude", "codex.semantic_review.bypassed", args.task_id, {"reason": "--skip-codex-review"})
        else:
            approved_by_review, review_data = run_codex_review_impl(
                root,
                ledger,
                task,
                codex_bin=args.codex,
                model=args.review_model or "",
                profile=args.profile or "",
                timeout=args.review_timeout,
                allow_network=args.allow_network,
                patch_max_bytes=args.review_patch_max_bytes,
            )
            if not approved_by_review:
                task["status"] = "review"
                task["updated_at"] = now()
                save_ledger(ledger)
                append_event("claude", "merge.queue.blocked_by_codex_review", args.task_id, {"review": review_data})
                print(json.dumps({"task_id": task["id"], "status": "review", "queued": False, "reason": "codex semantic review did not approve"}, ensure_ascii=False, indent=2))
                return 6

        add_merge_queue(
            {
                "task_id": task["id"],
                "branch": task["branch"],
                "worktree": task["worktree"],
                "queued_at": now(),
                "attempt": task["attempts"],
                "pre_head": pre_head,
                "codex_review": "skipped" if codex_review_bypassed else "approved",
                "codex_review_bypassed": codex_review_bypassed,
            }
        )

        append_event(
            "claude",
            "merge.queue.added",
            args.task_id,
            {"branch": task["branch"], "files": files, "pre_head": pre_head, "codex_review_bypassed": codex_review_bypassed},
        )

        print(
            json.dumps(
                {
                    "task_id": task["id"],
                    "status": "review",
                    "branch": task["branch"],
                    "worktree": task["worktree"],
                    "changed_files": files,
                    "pre_head": pre_head,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0


def cmd_parallel(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    selected = selectable_tasks(ledger, args.max_workers)

    if not selected:
        print("no parallelizable pending tasks")
        return 0

    for task in selected:
        task["status"] = "assigned"
        task["updated_at"] = now()
    save_ledger(ledger)

    append_event("claude", "parallel.start", data={"tasks": [t["id"] for t in selected]})
    script = pathlib.Path(__file__).resolve()
    results: Dict[str, int] = {}

    def one(t: Dict[str, Any]) -> Tuple[str, int]:
        contract_path = task_dir(t["id"]) / "parallel-worker-context.xml"
        contract_path.write_text(build_parallel_worker_contract(t, selected), encoding="utf-8")
        cmd = [
            sys.executable,
            str(script),
            "dispatch",
            t["id"],
            "--timeout",
            str(args.timeout),
            "--sandbox",
            args.sandbox,
            "--prompt-file",
            str(contract_path),
            "--review-patch-max-bytes",
            str(args.review_patch_max_bytes),
        ]
        if args.model:
            cmd += ["--model", args.model]
        if args.review_model:
            cmd += ["--review-model", args.review_model]
        if args.profile:
            cmd += ["--profile", args.profile]
        if args.allow_network:
            cmd += ["--allow-network"]
        if args.allow_legacy:
            cmd += ["--allow-legacy"]
        if not args.no_validate:
            cmd += ["--validate"]
        cp = subprocess.run(cmd, cwd=str(git_root()))
        return t["id"], cp.returncode

    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futs = [ex.submit(one, t) for t in selected]
        for fut in as_completed(futs):
            tid, rc = fut.result()
            results[tid] = rc
            append_event("claude", "parallel.task.done", tid, {"returncode": rc})

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(rc == 0 for rc in results.values()) else 1


def cmd_review_diff(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    task = find_task(ledger, args.task_id)
    wt = pathlib.Path(args.worktree or task.get("worktree") or ".").resolve()
    base_ref = args.base_ref or task.get("last_dispatch_head") or task.get("base_ref") or ""
    report = review_diff_impl(task, wt, base_ref=base_ref or None)
    write_json(task_dir(args.task_id) / "review.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["approved"] else 2


def cmd_merge_next(args: argparse.Namespace) -> int:
    root = git_root()
    p = ensure_dirs(root)
    ledger = load_ledger()

    with AtomicLock("merge-queue", timeout=120, stale_after=3600):
        queue = read_json(p["queue"], [])
        if not queue:
            print("merge queue is empty")
            return 0

        item = queue[0]
        task = find_task(ledger, item["task_id"])
        branch = item["branch"]

        review_validation = read_json(task_dir(task["id"]) / "codex.review.validation.json", {})
        review_data = review_validation.get("data", {}) if isinstance(review_validation, dict) else {}
        review_ok = review_data.get("verdict") == "approve" and review_data.get("ready_to_merge") is True
        if not review_ok and not item.get("codex_review_bypassed") and not args.allow_legacy:
            raise RuntimeError("merge blocked: Codex semantic review approval is required before merge queue processing; use --allow-legacy for v1 queue entries")

        dirty_worktree = run(["git", "diff", "--quiet"], cwd=root).returncode != 0
        dirty_index = run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode != 0
        if dirty_worktree or dirty_index:
            raise RuntimeError("main worktree is dirty; commit/stash before merging")

        run(["git", "config", "rerere.enabled", "true"], cwd=root)
        run(["git", "config", "rerere.autoupdate", "true"], cwd=root)

        pre = current_head(root)
        cp = run(
            ["git", "merge", "--no-ff", branch, "-m", f"merge(codex): {task['id']} {task.get('slug', 'task')}"],
            cwd=root,
        )

        if cp.returncode != 0:
            run(["git", "merge", "--abort"], cwd=root)
            task["status"] = "blocked"
            task["last_reason"] = "merge conflict"
            task["updated_at"] = now()
            queue.pop(0)
            write_json(p["queue"], queue)
            save_ledger(ledger)
            append_event("claude", "merge.conflict", task["id"], {"stderr": cp.stderr, "stdout": cp.stdout})
            print(cp.stderr or cp.stdout, file=sys.stderr)
            return 2

        ok, results = run_validation(root, root, ledger, task_dir(task["id"]) / "merge-validation.log", args.validation_timeout)
        write_json(task_dir(task["id"]) / "merge-validation.json", {"ok": ok, "results": results})

        if not ok:
            run(["git", "reset", "--hard", pre], cwd=root)
            task["status"] = "failed"
            task["last_reason"] = "post-merge validation failed; merge rolled back"
            task["updated_at"] = now()
            queue.pop(0)
            write_json(p["queue"], queue)
            save_ledger(ledger)
            append_event("claude", "merge.validation_failed.rollback", task["id"], {"results": results})
            return 3

        task["status"] = "merged"
        task["merged_at"] = now()
        task["updated_at"] = now()
        queue.pop(0)
        write_json(p["queue"], queue)

        tag = f"orch/{task['id']}"
        run(["git", "tag", "-a", tag, "-m", f"Checkpoint for {task['id']}: {task.get('title', '')}"], cwd=root)

        save_ledger(ledger)
        append_event("claude", "merge.success", task["id"], {"tag": tag})

        if args.cleanup:
            wt = task.get("worktree")
            if wt:
                run(["git", "worktree", "remove", "--force", wt], cwd=root)

        print(json.dumps({"merged": task["id"], "tag": tag}, ensure_ascii=False, indent=2))
        return 0


def error_fingerprint(task: Dict[str, Any]) -> str:
    td = task_dir(task["id"])
    chunks: List[str] = []
    for name in ["codex.stderr.log", "validation.log", "review.json", "exit.json", "codex.final.validation.json"]:
        fp = td / name
        if fp.exists():
            text = fp.read_text(encoding="utf-8", errors="replace")[-8000:]
            text = re.sub(r"0x[0-9a-fA-F]+|\d{4,}", "#", text)
            chunks.append(text)
    return hashlib.sha256("\n".join(chunks).encode("utf-8")).hexdigest()


def cmd_stuck_check(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    changed = False
    alerts = []

    for task in ledger.get("tasks", []):
        if task.get("status") != "failed":
            continue

        fp = error_fingerprint(task)
        if fp == task.get("last_error_hash"):
            task["same_failure_count"] = int(task.get("same_failure_count", 0)) + 1
        else:
            task["last_error_hash"] = fp
            task["same_failure_count"] = 1

        count = task["same_failure_count"]

        if count >= args.escalate_after:
            task["status"] = "blocked"
            task["last_reason"] = f"same failure repeated {count} times; user escalation required"
            alerts.append({"task_id": task["id"], "action": "user-escalation", "count": count})
            append_event("claude", "task.escalated_to_user", task["id"], {"same_failure_count": count, "reason": task["last_reason"]})
        elif count >= args.strategy_after:
            alerts.append({"task_id": task["id"], "action": "change-strategy", "count": count})

        task["updated_at"] = now()
        changed = True

    if changed:
        save_ledger(ledger)

    append_event("claude", "stuck.check", data={"alerts": alerts})
    print(json.dumps(alerts, ensure_ascii=False, indent=2))
    return 0 if not alerts else 1


def cmd_resume_check(args: argparse.Namespace) -> int:
    root = git_root()
    p = ensure_dirs(root)
    ledger = load_ledger()
    issues: List[Dict[str, Any]] = []

    for lock in p["locks"].glob("*.lock"):
        meta = read_json(lock / "meta.json", {})
        stale = False
        if lock.exists():
            created = float(meta.get("created_at", 0) or 0)
            pid = int(meta.get("pid", 0) or 0)
            same_host = meta.get("host") == socket.gethostname()
            stale = (same_host and pid and not pid_alive(pid)) or (created and time.time() - created > 3600)
        if stale:
            issues.append({"type": "stale-lock", "path": str(lock), "meta": meta})
            if args.repair:
                shutil.rmtree(lock, ignore_errors=True)

    for task in ledger.get("tasks", []):
        wt = task.get("worktree")
        if task.get("status") in {"running", "assigned", "review"} and wt and not pathlib.Path(wt).exists():
            issues.append({"type": "missing-worktree", "task_id": task["id"], "worktree": wt})
            if args.repair:
                task["status"] = "failed"
                task["last_reason"] = "worktree missing during resume repair"
                task["updated_at"] = now()

    if args.repair:
        save_ledger(ledger)

    summary = {
        "root": str(root),
        "ledger": str(p["ledger"]),
        "queue": read_json(p["queue"], []),
        "issues": issues,
        "read_order": [
            "CLAUDE.md",
            "AGENTS.md",
            ".orchestration/ledger.json",
            ".orchestration/progress.jsonl",
            ".orchestration/merge-queue.json",
            ".orchestration/tasks/*/exit.json",
            ".orchestration/tasks/*/review.json",
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


def cmd_poll(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    counts = {s: 0 for s in sorted(STATUS)}
    for t in ledger.get("tasks", []):
        counts[t.get("status", "pending")] = counts.get(t.get("status", "pending"), 0) + 1

    out = {
        "counts": counts,
        "merge_queue": read_json(ensure_dirs()["queue"], []),
        "tasks": ledger.get("tasks", []) if args.verbose else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    root = git_root()
    ledger = load_ledger()
    removed: List[str] = []

    for task in ledger.get("tasks", []):
        if task.get("status") == "merged" or (args.failed and task.get("status") == "failed"):
            wt = task.get("worktree")
            if wt and pathlib.Path(wt).exists():
                cp = run(["git", "worktree", "remove", "--force", wt], cwd=root)
                if cp.returncode == 0:
                    removed.append(wt)

    append_event("claude", "cleanup", data={"removed": removed})
    print(json.dumps({"removed": removed}, indent=2))
    return 0



def task_spec_meta(task_id: str) -> Dict[str, Any]:
    fm, _body, _path = read_spec(task_id)
    return fm if isinstance(fm, dict) else {}


def seconds_between(a: str, b: str) -> Optional[float]:
    da, db = parse_time(a), parse_time(b)
    if da and db:
        return max(0.0, (db - da).total_seconds())
    return None


def collect_stats(since: str = "") -> Dict[str, Any]:
    root = git_root()
    p = ensure_dirs(root)
    ledger = load_ledger()
    tasks = ledger.get("tasks", [])
    events = read_jsonl(p["progress"], since=since)
    status_counts: Dict[str, int] = {}
    for task in tasks:
        status_counts[task.get("status", "pending")] = status_counts.get(task.get("status", "pending"), 0) + 1
    kind: Dict[str, Dict[str, int]] = {}
    ttm: List[float] = []
    retries: List[int] = []
    for task in tasks:
        k = str(task_spec_meta(task.get("id", "")).get("kind") or "unknown")
        bucket = kind.setdefault(k, {"merged": 0, "dispatched": 0, "total": 0})
        bucket["total"] += 1
        if int(task.get("attempts", 0) or 0) > 0:
            bucket["dispatched"] += 1
        if task.get("status") == "merged":
            bucket["merged"] += 1
        if task.get("created_at") and task.get("merged_at"):
            sec = seconds_between(task.get("created_at", ""), task.get("merged_at", ""))
            if sec is not None:
                ttm.append(sec)
        retries.append(max(0, int(task.get("attempts", 0) or 0) - 1))
    review_dist: Dict[str, int] = {"approve": 0, "request_changes": 0, "reject": 0, "invalid": 0, "skipped": 0}
    total_codex_seconds = 0.0
    for task in tasks:
        td = task_dir(task.get("id", ""))
        for candidate in [td / "review-exit.json", td / "phase1" / "review-exit.json", td / "phase2" / "review-exit.json"]:
            data = read_json(candidate, {})
            total_codex_seconds += float(data.get("elapsed_seconds", 0) or 0)
        for candidate in [td / "exit.json", td / "phase1" / "exit.json", td / "phase2" / "exit.json"]:
            data = read_json(candidate, {})
            total_codex_seconds += float(data.get("elapsed_seconds", 0) or 0)
        for candidate in [td / "codex.review.validation.json", td / "phase1" / "codex.review.validation.json", td / "phase2" / "codex.review.validation.json"]:
            data = read_json(candidate, {})
            verdict = ((data.get("data") or {}) if isinstance(data, dict) else {}).get("verdict")
            if verdict in review_dist:
                review_dist[verdict] += 1
        if (task.get("codex_review") or {}).get("verdict") == "skipped":
            review_dist["skipped"] += 1
    bypass = {"spec.bypass": 0, "allow_legacy": 0, "no_spec": 0, "codex.semantic_review.bypassed": 0}
    timeline_events = []
    for ev in events:
        if ev.get("event") == "spec.bypass":
            bypass["spec.bypass"] += 1
            data = ev.get("data") or {}
            if data.get("allow_legacy"):
                bypass["allow_legacy"] += 1
            if data.get("no_spec"):
                bypass["no_spec"] += 1
        if ev.get("event") == "codex.semantic_review.bypassed":
            bypass["codex.semantic_review.bypassed"] += 1
        if ev.get("event") in {"merge.success", "task.status", "spec.approved", "merge.queue.blocked_by_codex_review"}:
            timeline_events.append(ev)
    queue = read_json(p["queue"], [])
    wt_cp = run(["git", "worktree", "list", "--porcelain"], cwd=root)
    active_worktrees = max(0, len([x for x in wt_cp.stdout.splitlines() if x.startswith("worktree ")]) - 1) if wt_cp.returncode == 0 else 0
    return {
        "generated_at": now(),
        "since": since,
        "status_counts": status_counts,
        "spec_kind": {k: {**v, "success_rate": (v["merged"] / v["dispatched"] if v["dispatched"] else 0)} for k, v in kind.items()},
        "average_time_to_merge_seconds": (sum(ttm) / len(ttm) if ttm else None),
        "average_retry_count": (sum(retries) / len(retries) if retries else 0),
        "max_retry_count": max(retries) if retries else 0,
        "semantic_review_verdicts": review_dist,
        "bypass_counts": bypass,
        "codex_elapsed_seconds_total": total_codex_seconds,
        "active_worktree_count": active_worktrees,
        "merge_queue_length": len(queue),
        "recent_timeline": timeline_events[-20:],
    }


def stats_text(stats: Dict[str, Any]) -> str:
    lines = ["# Orchestration Stats", "", f"generated_at: {stats['generated_at']}", f"since: {stats.get('since') or 'all'}", ""]
    lines.append("## Status counts")
    for k, v in sorted(stats["status_counts"].items()):
        lines.append(f"- {k}: {v}")
    lines.append("\n## Spec kind success rates")
    for k, v in sorted(stats["spec_kind"].items()):
        lines.append(f"- {k}: merged={v['merged']} dispatched={v['dispatched']} success_rate={v['success_rate']:.2%}")
    lines.extend([
        "\n## Retry and time",
        f"- average_time_to_merge_seconds: {stats['average_time_to_merge_seconds']}",
        f"- average_retry_count: {stats['average_retry_count']:.2f}",
        f"- max_retry_count: {stats['max_retry_count']}",
        f"- codex_elapsed_seconds_total: {stats['codex_elapsed_seconds_total']:.1f}",
        "\n## Semantic review verdicts",
    ])
    for k, v in stats["semantic_review_verdicts"].items():
        lines.append(f"- {k}: {v}")
    lines.extend(["\n## Bypasses"])
    for k, v in stats["bypass_counts"].items():
        lines.append(f"- {k}: {v}")
    lines.extend(["\n## Current", f"- active_worktree_count: {stats['active_worktree_count']}", f"- merge_queue_length: {stats['merge_queue_length']}"])
    lines.append("\n## Recent timeline")
    for ev in stats["recent_timeline"][-10:]:
        lines.append(f"- {ev.get('ts')} {ev.get('event')} {ev.get('task_id') or ''}")
    return "\n".join(lines) + "\n"


def stats_html(stats: Dict[str, Any]) -> str:
    body = stats_text(stats)
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>Orchestration Stats</title><style>body{{font-family:system-ui,-apple-system,sans-serif;margin:2rem;line-height:1.45}} pre{{background:#f6f8fa;padding:1rem;border-radius:8px;white-space:pre-wrap}}</style></head><body><h1>Orchestration Stats</h1><pre>{html_escape(body)}</pre></body></html>"""


def cmd_stats(args: argparse.Namespace) -> int:
    def emit() -> None:
        stats = collect_stats(args.since)
        if args.format == "json":
            out = json.dumps(stats, ensure_ascii=False, indent=2) + "\n"
        elif args.format == "html":
            out = stats_html(stats)
        else:
            out = stats_text(stats)
        if args.output:
            pathlib.Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out, end="")
    if args.watch:
        while True:
            emit()
            time.sleep(30)
    emit()
    return 0


def cmd_rebuild_ledger(args: argparse.Namespace) -> int:
    root = git_root(); p = ensure_dirs(root)
    events = read_jsonl(p["progress"])
    tasks: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        tid = ev.get("task_id")
        data = ev.get("data") or {}
        if ev.get("event") == "task.created" and tid:
            tasks[tid] = {
                "id": tid,
                "title": data.get("title", ""),
                "slug": slugify(data.get("title", tid)),
                "objective": "",
                "acceptance": "",
                "status": "pending",
                "dependencies": data.get("dependencies", []),
                "touched_paths": data.get("touched_paths", []),
                "shared_resources": [],
                "attempts": 0,
                "max_attempts": 4,
                "created_at": ev.get("ts", now()),
                "updated_at": ev.get("ts", now()),
                "owner": "claude",
                "branch": "",
                "worktree": "",
                "base_ref": "",
                "last_reason": "",
                "artifacts": {},
                "spec_history": [],
            }
        if tid and tid not in tasks:
            tasks[tid] = {"id": tid, "title": "", "slug": slugify(tid), "objective": "", "acceptance": "", "status": "pending", "dependencies": [], "touched_paths": [], "shared_resources": [], "attempts": 0, "max_attempts": 4, "created_at": ev.get("ts", now()), "updated_at": ev.get("ts", now()), "owner": "claude", "branch": "", "worktree": "", "base_ref": "", "last_reason": "", "artifacts": {}, "spec_history": []}
        if not tid:
            continue
        t = tasks[tid]
        t["updated_at"] = ev.get("ts", t.get("updated_at"))
        if ev.get("event") == "task.status":
            t["status"] = data.get("to", t.get("status"))
            t["last_reason"] = data.get("reason", t.get("last_reason", ""))
        elif ev.get("event") == "codex.dispatch.start":
            t["status"] = "running"; t["attempts"] = int(t.get("attempts", 0)) + 1; t["branch"] = data.get("branch", t.get("branch", "")); t["worktree"] = data.get("worktree", t.get("worktree", ""))
        elif ev.get("event") == "merge.queue.added":
            t["status"] = "review"; t["branch"] = data.get("branch", t.get("branch", ""))
        elif ev.get("event") == "merge.success":
            t["status"] = "merged"; t["merged_at"] = ev.get("ts")
        elif ev.get("event") in {"merge.conflict", "task.escalated_to_user"}:
            t["status"] = "blocked"; t["last_reason"] = data.get("reason", ev.get("event"))
        elif ev.get("event") in {"validation.failed", "diff.review.rejected"}:
            t["status"] = "failed"; t["last_reason"] = ev.get("event")
        elif ev.get("event") == "spec.approved":
            t.setdefault("spec_history", []).append({"version": data.get("version", 1), "approved_at": ev.get("ts"), "path": data.get("path", "")})
    current = load_ledger()
    rebuilt = {"version": current.get("version", 1), "project": current.get("project", root.name), "created_at": current.get("created_at", now()), "updated_at": now(), "commands": current.get("commands", {"install":"","lint":"","typecheck":"","test":"","build":""}), "settings": current.get("settings", {}), "tasks": list(tasks.values()), "rebuild_warnings": ["objective/acceptance/touched_paths may be empty if not present in progress events"]}
    out_path = pathlib.Path(args.output or (p["orch"] / "ledger.rebuilt.json"))
    if args.dry_run:
        print(json.dumps({"output": str(out_path), "current_task_count": len(current.get("tasks", [])), "rebuilt_task_count": len(rebuilt["tasks"]), "warnings": rebuilt["rebuild_warnings"]}, ensure_ascii=False, indent=2))
    else:
        if p["ledger"].exists():
            backup = p["orch"] / f"ledger.json.backup.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(p["ledger"], backup)
        write_json(out_path, rebuilt)
        print(str(out_path))
    return 0


def cmd_summarize_session(args: argparse.Namespace) -> int:
    ledger = load_ledger(); events = read_jsonl(ensure_dirs()["progress"], args.since)
    tasks = ledger.get("tasks", [])
    by_status: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        by_status.setdefault(t.get("status", "pending"), []).append(t)
    lines = ["# Session Summary", "", "## TL;DR"]
    lines.append(f"{len(by_status.get('merged', []))} merged, {len(by_status.get('blocked', []))} blocked, {len(by_status.get('review', []))} in review, merge queue length {len(read_json(ensure_dirs()['queue'], []))}.")
    lines.append("Use resume-session for mechanical repair; this summary is narrative context for Claude/user handoff.")
    lines.append("\n## Merged tasks")
    for t in by_status.get("merged", []):
        kind = task_spec_meta(t.get("id", "")).get("kind", "unknown")
        lines.append(f"- {t.get('id')} — {t.get('title','')} (kind: {kind}) tag: orch/{t.get('id')}")
    lines.append("\n## Blocked tasks")
    for t in by_status.get("blocked", []):
        lines.append(f"- {t.get('id')} — {t.get('title','')}: {t.get('last_reason','')}")
    lines.append("\n## In-progress tasks")
    for status in ["assigned", "running", "review", "codex_review", "phase1_running", "phase1_review", "phase1_done", "phase2_running", "phase2_review", "failed", "pending", "spec_approved"]:
        for t in by_status.get(status, []):
            lines.append(f"- {t.get('id')} — {t.get('title','')} status={status} attempts={t.get('attempts',0)}")
    lines.append("\n## Recent failures")
    for ev in events[-args.max_events:]:
        if ev.get("event") in {"validation.failed", "diff.review.rejected", "merge.queue.blocked_by_codex_review", "merge.conflict", "task.escalated_to_user"}:
            lines.append(f"- {ev.get('ts')} {ev.get('event')} {ev.get('task_id')}")
    lines.append("\n## Recommended next actions")
    queue = read_json(ensure_dirs()["queue"], [])
    if queue:
        lines.append("- Run `.orchestration/bin/merge-arbiter --cleanup` after reviewing queued items.")
    elif by_status.get("blocked"):
        lines.append("- Resolve blocked tasks before dispatching more work; consider adding a learned lesson after user escalation.")
    else:
        lines.append("- Select pending/spec_approved tasks and dispatch the next safe task.")
    print("\n".join(lines[: max(20, args.max_events + 80)]))
    return 0


def manager_lock_state() -> Dict[str, Any]:
    lock = ensure_dirs()["manager_lock"]
    data = read_json(lock, {})
    if not data:
        return {"locked": False, "path": str(lock)}
    expires = parse_time(data.get("expires_at", ""))
    stale = bool(expires and expires < dt.datetime.now(dt.timezone.utc).astimezone())
    return {"locked": not stale, "stale": stale, "path": str(lock), "data": data}


def cmd_manager_status(args: argparse.Namespace) -> int:
    print(json.dumps(manager_lock_state(), ensure_ascii=False, indent=2))
    return 0


def cmd_manager_lock(args: argparse.Namespace) -> int:
    p = ensure_dirs(); state = manager_lock_state()
    if state.get("locked") and not args.force:
        print(json.dumps(state, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    started = dt.datetime.now(dt.timezone.utc).astimezone()
    expires = started + dt.timedelta(hours=args.ttl_hours)
    data = {"pid": os.getpid(), "host": socket.gethostname(), "started_at": started.isoformat(timespec="seconds"), "expires_at": expires.isoformat(timespec="seconds"), "claude_session_id": os.environ.get("CLAUDE_SESSION_ID", "")}
    write_json(p["manager_lock"], data)
    append_event("claude", "manager.lock", data=data)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_manager_unlock(args: argparse.Namespace) -> int:
    lock = ensure_dirs()["manager_lock"]
    if lock.exists():
        lock.unlink()
    append_event("claude", "manager.unlock", data={})
    print("unlocked")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    if args.audit_cmd == "show":
        rows = read_jsonl(ensure_dirs()["audit"], args.since)
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        return 0
    raise ValueError(args.audit_cmd)


def next_lesson_id(text: str) -> str:
    nums = [int(x) for x in re.findall(r"^## L-(\d{3})\b", text, flags=re.MULTILINE)]
    return f"L-{(max(nums) + 1 if nums else 1):03d}"


def cmd_lesson(args: argparse.Namespace) -> int:
    p = ensure_dirs(); path = p["learned"]
    if not path.exists():
        path.write_text("# Learned lessons\n\n", encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    if args.lesson_cmd == "add":
        context, trap, lesson = args.context, args.trap, args.lesson
        if args.interactive:
            context = context or input("Context: ")
            trap = trap or input("Trap: ")
            lesson = lesson or input("Lesson: ")
        if not (context and trap and lesson):
            raise RuntimeError("context, trap, and lesson are required")
        lid = next_lesson_id(text)
        kind = task_spec_meta(args.task).get("kind", "unknown") if args.task else "unknown"
        block = f"## {lid} — {now().split('T')[0]} — from {args.task or 'manual'} (kind: {kind})\n\nContext: {context}\n\nTrap: {trap}\n\nLesson: {lesson}\n\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
        append_event("claude", "lesson.added", args.task or None, {"lesson_id": lid})
        print(lid)
        return 0
    if args.lesson_cmd == "list":
        cutoff = parse_since(args.since) if args.since else None
        for m in re.finditer(r"^## (L-\d{3}) — ([^—]+) — (.*)$", text, flags=re.MULTILINE):
            when = parse_time(m.group(2).strip())
            if cutoff and when and when < cutoff:
                continue
            print(f"{m.group(1)}\t{m.group(2).strip()}\t{m.group(3).strip()}")
        return 0
    if args.lesson_cmd == "show":
        m = re.search(rf"^## {re.escape(args.id)}\b.*?(?=^## L-\d{{3}}\b|\Z)", text, flags=re.MULTILINE | re.DOTALL)
        if not m:
            return 2
        print(m.group(0).rstrip())
        return 0
    raise ValueError(args.lesson_cmd)


def phase_dir(task_id: str, phase: str) -> pathlib.Path:
    d = task_dir(task_id) / phase
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_test_command(root: pathlib.Path, worktree: pathlib.Path, ledger: Dict[str, Any], log_path: pathlib.Path, timeout: int) -> Dict[str, Any]:
    env = validation_env(root); commands = ledger.get("commands", {})
    with log_path.open("a", encoding="utf-8") as log:
        install_result = run_install_if_needed(root, worktree, commands.get("install", ""), log, timeout, env)
        test_cmd = (commands.get("test") or "").strip()
        if install_result.get("exit_code") != 0:
            return {"ok": False, "results": [install_result]}
        if not test_cmd:
            msg = "test command is empty; test-first mode cannot prove failing tests"
            return {"ok": False, "results": [install_result, {"name": "test", "command": "", "exit_code": 0, "skipped": True, "warning": msg}]}
        log.write(f"\n===== {now()} test: {test_cmd} =====\n")
        try:
            cp = shell(test_cmd, worktree, timeout=timeout, env=env)
            log.write(cp.stdout); log.write(cp.stderr)
            test_result = {"name": "test", "command": test_cmd, "exit_code": cp.returncode}
        except subprocess.TimeoutExpired as e:
            log.write(f"TIMEOUT after {timeout}s\n{e}\n")
            test_result = {"name": "test", "command": test_cmd, "exit_code": 124, "timeout": timeout}
        return {"ok": test_result["exit_code"] == 0, "results": [install_result, test_result]}


def run_codex_phase(args: argparse.Namespace, root: pathlib.Path, ledger: Dict[str, Any], task: Dict[str, Any], wt: pathlib.Path, phase: str, pre_head: str, prompt: str, allowed_review_warnings: bool, expect_test_failure: bool = False) -> Tuple[bool, Dict[str, Any]]:
    td = phase_dir(task["id"], phase)
    (td / "prompt.md").write_text(prompt, encoding="utf-8")
    stdout_jsonl = td / "codex.stdout.jsonl"; stderr_log = td / "codex.stderr.log"; final_msg = td / "codex.final.json"
    schema = ensure_dirs(root)["schemas"] / "task-output.schema.json"
    cmd = [args.codex, "exec", "--cd", str(wt), "--ask-for-approval", "never", "--sandbox", args.sandbox, "--json", "--output-last-message", str(final_msg)]
    if schema.exists(): cmd += ["--output-schema", str(schema)]
    if args.model: cmd += ["--model", args.model]
    if args.profile: cmd += ["--profile", args.profile]
    if not args.allow_network: cmd += ["-c", "sandbox_workspace_write.network_access=false"]
    cmd.append("-")
    env = validation_env(root); env["ORCH_TASK_ID"] = task["id"]; env["ORCH_PHASE"] = phase
    start = time.time()
    with stdout_jsonl.open("wb") as out, stderr_log.open("wb") as err:
        proc = subprocess.Popen(cmd, cwd=str(root), stdin=subprocess.PIPE, stdout=out, stderr=err, env=env, preexec_fn=os.setsid if hasattr(os, "setsid") else None)
        try:
            proc.communicate(prompt.encode("utf-8"), timeout=args.timeout); rc = proc.returncode; timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True; kill_process_group(proc); rc = 124
    stats = parse_jsonl(stdout_jsonl)
    write_json(td / "exit.json", {"returncode": rc, "timed_out": timed_out, "elapsed_seconds": round(time.time() - start, 3), "jsonl_stats": stats, "command": cmd, "pre_head": pre_head, "phase": phase, "effective_model": effective_model_for_run(root, args.model, args.profile)})
    if rc != 0 or timed_out or stats.get("turn_failed"):
        return False, {"reason": "codex process failed", "returncode": rc}
    final_ok, final_data, final_errors = validate_codex_final(final_msg)
    write_json(td / "codex.final.validation.json", {"ok": final_ok, "errors": final_errors, "data": final_data})
    if not final_ok:
        return False, {"reason": "codex final invalid", "errors": final_errors}
    files = changed_files(wt, base_ref=pre_head)
    if not files:
        return False, {"reason": "no changes"}
    review_task = dict(task)
    if phase == "phase2":
        review_task["_test_first_phase2"] = True
    report = review_diff_impl(review_task, wt, base_ref=pre_head)
    write_json(td / "review.json", report)
    if not report["approved"] and not allowed_review_warnings:
        append_event("claude", "diff.review.rejected", task["id"], {"phase": phase, "report": report})
        return False, {"reason": "static review rejected", "review": report}
    run(["git", "add", "-A"], cwd=wt, check=True)
    if run(["git", "diff", "--cached", "--quiet"], cwd=wt).returncode != 0:
        msg = f"chore(codex): {task['id']} {'phase1 tests' if phase == 'phase1' else 'phase2 implementation'}"
        run(["git", "commit", "-m", msg], cwd=wt, check=True)
    if expect_test_failure:
        validation = run_test_command(root, wt, ledger, td / "validation.log", args.validation_timeout)
        write_json(td / "validation.json", validation)
        test_items = [x for x in validation.get("results", []) if x.get("name") == "test"]
        if not test_items or test_items[-1].get("exit_code") == 0:
            return False, {"reason": "phase1 expected test command to fail", "validation": validation}
    else:
        ok, results = run_validation(root, wt, ledger, td / "validation.log", args.validation_timeout)
        write_json(td / "validation.json", {"ok": ok, "results": results})
        if not ok:
            return False, {"reason": "validation failed", "results": results}
    if porcelain_files(wt):
        write_json(td / "validation-dirty.json", {"dirty_files": porcelain_files(wt)})
        append_event("claude", "validation.generated_changes", task["id"], {"phase": phase, "dirty_files": porcelain_files(wt)})
        return False, {"reason": "validation generated changes"}
    rev_count_cp = run(["git", "rev-list", "--count", f"{pre_head}..HEAD"], cwd=wt, check=True)
    if int((rev_count_cp.stdout or "0").strip() or "0") > 0:
        patch_cp = run(["git", "format-patch", "--stdout", f"{pre_head}..HEAD"], cwd=wt)
    else:
        patch_cp = run(["git", "diff", pre_head], cwd=wt)
    (td / "last.patch").write_text(patch_cp.stdout, encoding="utf-8")
    return True, {"changed_files": files, "phase_dir": str(td)}


def cmd_dispatch_test_first(args: argparse.Namespace) -> int:
    root = git_root(); ledger = load_ledger(); task = find_task(ledger, args.task_id)
    fm, spec_body, sp = read_spec(args.task_id)
    kind = str(fm.get("kind") or "feature")
    if kind not in {"feature", "bugfix", "behavior", "api"}:
        raise RuntimeError("test-first mode is intended for spec kind feature/bugfix/behavior/api")
    if not sp.exists() and not (args.allow_legacy or args.no_spec):
        raise RuntimeError("test-first mode requires spec.md")
    wt = ensure_worktree(root, task, args.base_ref)
    spec_text = sp.read_text(encoding="utf-8") if sp.exists() else "LEGACY TASK"
    test_paths = [p for p in task.get("touched_paths", []) if TEST_PATH_RE.search(p)]
    if not test_paths:
        raise RuntimeError("test-first mode requires at least one test path in touched_paths")
    with AtomicLock(f"task-{args.task_id}", timeout=30, stale_after=max(args.timeout * 2, 3600)):
        # Phase 1
        pre1 = current_head(wt); task["status"] = "phase1_running"; task["phase_state"] = "phase1"; task["attempts"] = int(task.get("attempts", 0)) + 1; task["updated_at"] = now(); save_ledger(ledger)
        append_event("claude", "test_first.phase1.start", args.task_id, {"pre_head": pre1, "test_paths": test_paths})
        phase1_task = dict(task); phase1_task["touched_paths"] = test_paths
        phase1_prompt = build_prompt(phase1_task, ledger, """Test-first Phase 1. Write tests only. Do not implement production code. The test command is expected to fail because implementation is absent. Touch only paths listed in <allowed_paths>.""", spec_text)
        ok1, data1 = run_codex_phase(args, root, ledger, phase1_task, wt, "phase1", pre1, phase1_prompt, args.allow_review_warnings, expect_test_failure=True)
        if not ok1:
            task["status"] = "failed"; task["last_reason"] = "test-first phase1 failed: " + str(data1.get("reason")); task["updated_at"] = now(); save_ledger(ledger); return 11
        task["status"] = "phase1_review"; task["updated_at"] = now(); save_ledger(ledger)
        # Phase 1 semantic review, tolerate request_changes when missing_tests empty. Publish
        # phase1 artifacts temporarily to root-level names for reviewer compatibility.
        td = task_dir(task["id"]); p1 = phase_dir(task["id"], "phase1")
        for name in ["last.patch", "review.json", "validation.json", "validation.log", "codex.final.json", "codex.final.validation.json", "exit.json"]:
            src = p1 / name
            if src.exists():
                shutil.copy2(src, td / name)
        task["worktree"] = str(wt); save_ledger(ledger)
        approved1, review1 = run_codex_review_impl(root, ledger, task, codex_bin=args.codex, model=args.review_model or "", profile=args.profile or "", timeout=args.review_timeout, allow_network=args.allow_network, patch_max_bytes=args.review_patch_max_bytes)
        v1 = read_json(task_dir(task["id"]) / "codex.review.validation.json", {})
        verdict1 = ((v1.get("data") or {}) if isinstance(v1, dict) else {}).get("verdict")
        missing1 = ((v1.get("data") or {}) if isinstance(v1, dict) else {}).get("missing_tests") or []
        if not (approved1 or (verdict1 == "request_changes" and not missing1)):
            task["status"] = "failed"; task["last_reason"] = "phase1 semantic review did not accept test coverage"; task["updated_at"] = now(); save_ledger(ledger); return 12
        task["status"] = "phase1_done"; task["updated_at"] = now(); save_ledger(ledger); append_event("claude", "test_first.phase1.done", args.task_id, data1)
        if args.pause:
            print(json.dumps({"task_id": task["id"], "status": "phase1_done", "paused": True}, ensure_ascii=False, indent=2)); return 0
        # Phase 2
        pre2 = current_head(wt); task["status"] = "phase2_running"; task["phase_state"] = "phase2"; task["updated_at"] = now(); save_ledger(ledger)
        test_texts = []
        for rel in test_paths:
            fp = wt / rel
            if fp.is_file() and fp.stat().st_size < 1_000_000:
                test_texts.append(f"## {rel}\n\n```\n{fp.read_text(encoding='utf-8', errors='replace')}\n```")
        forbidden_xml = xml_path_list("forbidden_paths", test_paths)
        manager = "Test-first Phase 2. Implement production code to make Phase 1 tests pass. Do not rewrite Phase 1 tests except minimal non-behavioral adjustments.\n" + forbidden_xml + "\n<phase1_tests><![CDATA[" + cdata("\n\n".join(test_texts)) + "]]></phase1_tests>"
        phase2_prompt = build_prompt(task, ledger, manager, spec_text)
        ok2, data2 = run_codex_phase(args, root, ledger, task, wt, "phase2", pre2, phase2_prompt, args.allow_review_warnings, expect_test_failure=False)
        if not ok2:
            task["status"] = "failed"; task["last_reason"] = "test-first phase2 failed: " + str(data2.get("reason")); task["updated_at"] = now(); save_ledger(ledger); return 13
        phase2_changed = changed_files(wt, base_ref=pre2)
        rewritten_tests = [f for f in phase2_changed if any(paths_overlap(f, tp) for tp in test_paths)]
        if rewritten_tests:
            task["status"] = "failed"; task["last_reason"] = "phase2 modified phase1 test files"; task["updated_at"] = now(); save_ledger(ledger); append_event("claude", "diff.review.rejected", args.task_id, {"phase": "phase2", "rule": "phase2-test-rewrite", "files": rewritten_tests}); return 15
        task["status"] = "phase2_review"; task["updated_at"] = now(); save_ledger(ledger)
        # publish phase2 artifacts to root-level names for merge-arbiter compatibility.
        td = task_dir(task["id"]); p2 = phase_dir(task["id"], "phase2")
        for name in ["last.patch", "review.json", "validation.json", "validation.log", "codex.final.json", "codex.final.validation.json", "exit.json"]:
            src = p2 / name
            if src.exists():
                shutil.copy2(src, td / name)
        approved2, review2 = run_codex_review_impl(root, ledger, task, codex_bin=args.codex, model=args.review_model or "", profile=args.profile or "", timeout=args.review_timeout, allow_network=args.allow_network, patch_max_bytes=args.review_patch_max_bytes)
        if not approved2:
            task["status"] = "review"; task["last_reason"] = "phase2 semantic review did not approve"; task["updated_at"] = now(); save_ledger(ledger); append_event("claude", "merge.queue.blocked_by_codex_review", args.task_id, {"phase": "phase2", "review": review2}); return 14
        add_merge_queue({"task_id": task["id"], "branch": task["branch"], "worktree": task["worktree"], "queued_at": now(), "attempt": task["attempts"], "pre_head": pre1, "codex_review": "approved", "mode": "test-first"})
        task["status"] = "review"; task["updated_at"] = now(); save_ledger(ledger); append_event("claude", "merge.queue.added", args.task_id, {"branch": task["branch"], "mode": "test-first", "phase1_head": pre1, "phase2_head": pre2})
        print(json.dumps({"task_id": task["id"], "status": "review", "mode": "test-first", "queued": True}, ensure_ascii=False, indent=2)); return 0




def cmd_dispatch(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    _task = find_task(ledger, args.task_id)
    fm, _body, _sp = read_spec(args.task_id)
    mode = args.mode or str(fm.get("mode") or "single")
    if mode == "test-first":
        return cmd_dispatch_test_first(args)
    return cmd_dispatch_single(args)


def cmd_hook_pretool(args: argparse.Namespace) -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    tool = data.get("tool_name")
    ti = data.get("tool_input") or {}
    command = ti.get("command", "")

    if tool == "Bash":
        for pattern in DANGEROUS_BASH_PATTERNS:
            if re.search(pattern, command):
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": f"Dangerous command blocked by orchestration hook: {pattern}",
                            }
                        }
                    )
                )
                return 0

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orch.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    for k in ["install", "lint", "typecheck", "test", "build"]:
        s.add_argument(f"--{k}")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("init-detect")
    s.add_argument("--root", default="")
    s.add_argument("--json", action="store_true")
    s.add_argument("--apply-gitignore", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_init_detect)

    s = sub.add_parser("codex-status")
    s.add_argument("--root", default="")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--suggest", action="store_true")
    s.set_defaults(func=cmd_codex_status)

    s = sub.add_parser("spec")
    spec_sub = s.add_subparsers(dest="spec_cmd", required=True)
    sc = spec_sub.add_parser("create")
    sc.add_argument("task_id")
    sc.add_argument("--kind", default="feature", choices=sorted(SPEC_BYPASS_KINDS | SPEC_REQUIRED_KINDS | {"test", "chore"}))
    sc.add_argument("--force", action="store_true")
    su = spec_sub.add_parser("update")
    su.add_argument("task_id")
    su.add_argument("--file", default="")
    ss = spec_sub.add_parser("show")
    ss.add_argument("task_id")
    sv = spec_sub.add_parser("validate")
    sv.add_argument("task_id")
    sa = spec_sub.add_parser("approve")
    sa.add_argument("task_id")
    sh = spec_sub.add_parser("history")
    sh.add_argument("task_id")
    sd = spec_sub.add_parser("diff")
    sd.add_argument("task_id")
    sd.add_argument("--from", dest="from_version", required=True)
    sd.add_argument("--to", dest="to_version", required=True)
    s.set_defaults(func=cmd_spec)

    s = sub.add_parser("ledger")
    lsub = s.add_subparsers(dest="ledger_cmd", required=True)

    n = lsub.add_parser("new")
    n.add_argument("--id")
    n.add_argument("--title", required=True)
    n.add_argument("--objective", required=True)
    n.add_argument("--acceptance", default="")
    n.add_argument("--paths", default="")
    n.add_argument("--allow-empty-paths", action="store_true")
    n.add_argument("--deps", default="")
    n.add_argument("--timeout", type=int, default=7200)
    n.add_argument("--soft-budget", type=int, default=3600)
    n.add_argument("--hard-budget", type=int, default=14400)
    n.add_argument("--max-attempts", type=int, default=4)

    l = lsub.add_parser("list")
    l.add_argument("--json", action="store_true")

    shw = lsub.add_parser("show")
    shw.add_argument("id")

    st = lsub.add_parser("set-status")
    st.add_argument("id")
    st.add_argument("status")
    st.add_argument("--reason", default="")

    c = lsub.add_parser("set-commands")
    for k in ["install", "lint", "typecheck", "test", "build"]:
        c.add_argument(f"--{k}")

    s.set_defaults(func=cmd_ledger)

    s = sub.add_parser("select-parallel")
    s.add_argument("--max-workers", type=int, default=3)
    s.set_defaults(func=cmd_select_parallel)

    s = sub.add_parser("dispatch")
    s.add_argument("task_id")
    s.add_argument("--mode", choices=["single", "test-first"], default="")
    s.add_argument("--pause", action="store_true", help="in test-first mode, stop after phase1")
    s.add_argument("--prompt-file")
    s.add_argument("--retry-context", default="")
    s.add_argument("--base-ref")
    s.add_argument("--codex", default="codex")
    s.add_argument("--model", default="")
    s.add_argument("--profile", default="")
    s.add_argument(
        "--sandbox",
        choices=["read-only", "workspace-write", "danger-full-access"],
        default="workspace-write",
    )
    s.add_argument("--allow-network", action="store_true")
    s.add_argument("--timeout", type=int, default=7200)
    s.add_argument("--validate", action=argparse.BooleanOptionalAction, default=True)
    s.add_argument("--validation-timeout", type=int, default=1200)
    s.add_argument("--allow-review-warnings", action="store_true")
    s.add_argument("--no-spec", action="store_true", help="audited bypass for tasks without spec.md")
    s.add_argument("--allow-legacy", action="store_true", help="allow v1 tasks without spec.md")
    s.add_argument("--skip-codex-review", action="store_true", help="audited bypass of semantic Codex review")
    s.add_argument("--review-model", default="")
    s.add_argument("--review-timeout", type=int, default=3600)
    s.add_argument("--review-patch-max-bytes", type=int, default=PATCH_REVIEW_MAX_BYTES)
    s.set_defaults(func=cmd_dispatch)

    s = sub.add_parser("parallel")
    s.add_argument("--max-workers", type=int, default=3)
    s.add_argument("--timeout", type=int, default=7200)
    s.add_argument(
        "--sandbox",
        choices=["workspace-write", "read-only", "danger-full-access"],
        default="workspace-write",
    )
    s.add_argument("--model", default="")
    s.add_argument("--review-model", default="")
    s.add_argument("--profile", default="")
    s.add_argument("--allow-network", action="store_true")
    s.add_argument("--no-validate", action="store_true")
    s.add_argument("--allow-legacy", action="store_true")
    s.add_argument("--review-patch-max-bytes", type=int, default=PATCH_REVIEW_MAX_BYTES)
    s.set_defaults(func=cmd_parallel)

    s = sub.add_parser("codex-review")
    s.add_argument("task_id")
    s.add_argument("--codex", default="codex")
    s.add_argument("--model", default="")
    s.add_argument("--review-model", default="")
    s.add_argument("--profile", default="")
    s.add_argument("--timeout", type=int, default=3600)
    s.add_argument("--allow-network", action="store_true")
    s.add_argument("--allow-legacy", action="store_true")
    s.add_argument("--review-patch-max-bytes", type=int, default=PATCH_REVIEW_MAX_BYTES)
    s.set_defaults(func=cmd_codex_review)

    s = sub.add_parser("review-diff")
    s.add_argument("task_id")
    s.add_argument("--worktree", default="")
    s.add_argument("--base-ref", default="")
    s.set_defaults(func=cmd_review_diff)

    s = sub.add_parser("merge-next")
    s.add_argument("--validation-timeout", type=int, default=1200)
    s.add_argument("--cleanup", action="store_true")
    s.add_argument("--allow-legacy", action="store_true")
    s.set_defaults(func=cmd_merge_next)

    s = sub.add_parser("stuck-check")
    s.add_argument("--strategy-after", type=int, default=2)
    s.add_argument("--escalate-after", type=int, default=4)
    s.set_defaults(func=cmd_stuck_check)

    s = sub.add_parser("resume-check")
    s.add_argument("--repair", action="store_true")
    s.set_defaults(func=cmd_resume_check)

    s = sub.add_parser("poll")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(func=cmd_poll)

    s = sub.add_parser("cleanup")
    s.add_argument("--failed", action="store_true")
    s.set_defaults(func=cmd_cleanup)

    s = sub.add_parser("stats")
    s.add_argument("--format", choices=["text", "json", "html"], default="text")
    s.add_argument("--output", default="")
    s.add_argument("--since", default="")
    s.add_argument("--watch", action="store_true")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("rebuild-ledger")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--output", default="")
    s.set_defaults(func=cmd_rebuild_ledger)

    s = sub.add_parser("summarize-session")
    s.add_argument("--since", default="")
    s.add_argument("--max-events", type=int, default=80)
    s.set_defaults(func=cmd_summarize_session)

    s = sub.add_parser("manager-lock")
    s.add_argument("--ttl-hours", type=float, default=12.0)
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_manager_lock)

    s = sub.add_parser("manager-unlock")
    s.set_defaults(func=cmd_manager_unlock)

    s = sub.add_parser("manager-status")
    s.set_defaults(func=cmd_manager_status)

    s = sub.add_parser("audit")
    audit_sub = s.add_subparsers(dest="audit_cmd", required=True)
    ash = audit_sub.add_parser("show")
    ash.add_argument("--since", default="")
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("lesson")
    lesson_sub = s.add_subparsers(dest="lesson_cmd", required=True)
    la = lesson_sub.add_parser("add")
    la.add_argument("--task", default="")
    la.add_argument("--context", default="")
    la.add_argument("--trap", default="")
    la.add_argument("--lesson", default="")
    la.add_argument("--interactive", action="store_true")
    ll = lesson_sub.add_parser("list")
    ll.add_argument("--since", default="")
    ls = lesson_sub.add_parser("show")
    ls.add_argument("id")
    s.set_defaults(func=cmd_lesson)

    s = sub.add_parser("hook-pretool")
    s.set_defaults(func=cmd_hook_pretool)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        try:
            append_event("orchestrator", "error", data={"message": str(e), "argv": sys.argv[1:]})
        except Exception:
            pass
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
