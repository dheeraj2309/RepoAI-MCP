import os
from typing import Optional
from dataclasses import dataclass
import subprocess
from pathlib import Path

SUPPORTED_EXTENSIONS = {".py"}
EXCLUDED_FOLDERS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".next",
    "dist",
    "build",
    "out",
    "coverage",
    ".cache",
    "vendor",
    "venv",
    ".venv",
    "env",
    "target",
    "bin",
    "obj",
    ".idea",
    ".vscode",
}
MAX_FILE_SIZE_BYTES = 500_000


@dataclass
class FetchedFile:
    path: str
    content: str
    status: str
    error: Optional[str] = None


@dataclass
class FetchResult:
    files: list[FetchedFile]
    total_found: int
    total_filtered: int
    total_fetched: int
    total_failed: int


def is_git_repo(repo_path: str) -> bool:
    """
    Hard prerequisite check. No fallback — if this returns False,
    the caller must refuse to proceed entirely.
    """
    git_dir = os.path.join(repo_path, ".git")
    return os.path.isdir(git_dir)

def has_any_commits(repo_path: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path, capture_output=True, text=True,
    )
    return result.returncode == 0

def ensure_git_baseline(repo_path: str) -> None:
    """
    Solves the cold-start problem without touching any of the
    developer's actual code. If git isn't initialized, initializes it.
    If there are zero commits yet, commits only a .gitignore file —
    never the developer's existing or in-progress files. This gives
    HEAD a real commit to point to, so last_commit_sha and git diff
    based staleness detection work correctly from the very first index.
    """
    if not is_git_repo(repo_path):
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)

    if not has_any_commits(repo_path):
        gitignore_path = os.path.join(repo_path, ".gitignore")
        if not os.path.exists(gitignore_path):
            with open(gitignore_path, "w") as f:
                f.write("__pycache__/\n*.pyc\n.env\nvenv/\n.venv/\n")

        subprocess.run(["git", "add", ".gitignore"], cwd=repo_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initialize repository for code intelligence indexing"],
            cwd=repo_path, capture_output=True,
        )

def get_local_head_sha(repo_path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return None
    except Exception:
        return None


def get_uncommitted_changes(repo_path: str) -> list[dict]:
    """
    Returns files with uncommitted changes (modified, added, deleted)
    using git status --porcelain. Covers changes git hasn't recorded
    in a commit yet — including untracked new files.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        if result.returncode != 0:
            return []
        changes = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status_code = line[:2].strip()
            file_path = line[3:].strip()
            if not file_path.endswith(".py"):
                continue
            if status_code in ("A", "??"):
                changes.append({"file_path": file_path, "change_type ": "added"})
            elif status_code in ("M", "AM"):
                changes.append({"file_path": file_path, "change_type ": "modified"})
            elif status_code == "D":
                changes.append({"file_path": file_path, "change_type ": "deleted"})
        return changes
    except Exception:
        return []


def get_committed_changes_since(repo_path: str, since_sha: str) -> list[dict]:
    """
    Returns files changed between since_sha and current HEAD,
    using git diff --name-status. Covers changes already committed
    since the last time this repo was indexed.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", since_sha, "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        changes = []

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status_code, file_path = parts[0], parts[-1]

            if status_code == "A":
                changes.append({"file": file_path, "change_type": "added"})
            elif status_code == "M":
                changes.append({"file": file_path, "change_type": "modified"})
            elif status_code == "D":
                changes.append({"file": file_path, "change_type": "deleted"})
        return changes
    except Exception:
        return []


def check_repo_changes(repo_path: str, last_indexed_sha: Optional[str]) -> dict:
    """
    Combines uncommitted and committed changes into one picture
    of everything that differs from the last indexed state.
    This is the function backing the check_repo_changes MCP tool.
    """
    uncommitted = get_uncommitted_changes(repo_path)
    committed = (
        get_committed_changes_since(repo_path, last_indexed_sha)
        if last_indexed_sha
        else []
    )

    seen = set()
    combined = []
    for change in uncommitted + committed:
        if change["file"] not in seen:
            seen.add(change["file"])
            combined.append(change)

    return {
        "has_changes": len(combined) > 0,
        "changed_files": [
            c["file"] for c in combined if c["change_type"] in ("added", "modified")
        ],
        "deleted_files": [c["file"] for c in combined if c["change_type"] == "deleted"],
    }


def _should_include(file_path: str, size: int) -> bool:
    parts = file_path.split(os.sep)
    for part in parts[:-1]:
        if part in EXCLUDED_FOLDERS:
            return False
    ext = os.path.splitext(file_path)[1]
    if ext not in SUPPORTED_EXTENSIONS:
        return False
    if size > MAX_FILE_SIZE_BYTES:
        return False
    return True


def fetch_all_files(repo_path: str) -> FetchResult:
    """
    Walks the entire repo directory. Used by setup_repo for the
    initial bulk index. No API calls — pure filesystem reads.
    """
    repo_path = str(Path(repo_path).expanduser().resolve())

    targets = []
    for root, dirs, filenames in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_FOLDERS]
        for filename in filenames:
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, repo_path)
            try:
                size = os.path.getsize(full_path)
            except OSError:
                continue
            if _should_include(rel_path, size):
                targets.append((full_path, rel_path))

    total_found = len(targets)
    files = []
    failed = 0

    for full_path, rel_path in targets:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            files.append(FetchedFile(path=rel_path, content=content, status="success"))
        except Exception as e:
            failed += 1
            files.append(
                FetchedFile(path=rel_path, content="", status="failed", error=str(e))
            )

    return FetchResult(
        files=files,
        total_found=total_found,
        total_filtered=total_found,
        total_fetched=total_found - failed,
        total_failed=failed,
    )


def fetch_single_file(repo_path: str, file_path: str) -> FetchedFile:
    """
    Reads exactly one file. Used by index_file for the fast,
    single-file incremental path. Returns status="failed" with
    error="deleted" if the file no longer exists on disk —
    callers use this to know to remove the file's nodes from ChromaDB.
    """
    full_path = os.path.join(repo_path, file_path)

    if not os.path.exists(full_path):
        return FetchedFile(path=file_path, content="", status="failed", error="deleted")

    try:
        size = os.path.getsize(full_path)
        if size > MAX_FILE_SIZE_BYTES:
            return FetchedFile(
                path=file_path, content="", status="failed", error="file_too_large"
            )

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        return FetchedFile(path=file_path, content=content, status="success")

    except Exception as e:
        return FetchedFile(path=file_path, content="", status="failed", error=str(e))
