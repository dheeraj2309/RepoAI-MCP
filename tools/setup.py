import asyncio
import os
from pathlib import Path
from datetime import datetime
from sqlmodel import Session, select

from local_models import LocalRepo
from local_database import engine
from pipeline.fetcher import is_git_repo,ensure_git_baseline, check_repo_changes as _check_repo_changes
from pipeline.pipeline import run_full_index, run_file_index, write_repo


def _resolve_path(repo_path: str) -> str:
    return str(Path(repo_path).expanduser().resolve())


def _try_acquire_lock(repo_path: str) -> tuple[bool, LocalRepo]:
    """
    Atomic check-and-set on index_status, scoped to one SQLite
    Session/commit. Prevents two agents (separate Claude sessions,
    VS Code, Claude Code, all hitting the same repo_path) from
    starting concurrent indexing jobs on the same repo.
    """
    with Session(engine) as session:
        repo = session.exec(
            select(LocalRepo).where(LocalRepo.repo_path == repo_path)
        ).first()

        if repo and repo.index_status == "indexing":
            return False, repo

        if not repo:
            repo = LocalRepo(repo_path=repo_path, repo_name=os.path.basename(repo_path))
            session.add(repo)

        repo.index_status = "indexing"
        repo.last_run_started_at = datetime.now()
        repo.last_run_files_processed = 0
        repo.last_run_nodes_embedded = 0
        repo.last_run_error = None
        session.add(repo)
        session.commit()
        session.refresh(repo)
        return True, repo


async def start_new_project(repo_path: str) -> str:
    """Initializes a brand new project at the given path, including git
    setup, ready for indexing as code gets written. Use this when the
    developer is starting something from scratch — an empty or new
    folder, no existing code yet. Creates the folder if it doesn't
    exist and sets up git automatically. After this, use index_file
    as new files are created during development."""
    resolved_path = _resolve_path(repo_path)
    os.makedirs(resolved_path, exist_ok=True)

    ensure_git_baseline(resolved_path)

    acquired, repo = _try_acquire_lock(resolved_path)
    if not acquired:
        return f"{repo.repo_name} is already being set up."

    await run_full_index(repo.id, resolved_path)

    return (
        f"New project initialized at {resolved_path}, git ready. repo_id={repo.id}. "
        f"As you create files, call index_file after each one so they become searchable immediately."
    )


async def setup_existing_repo(repo_path: str) -> str:
    """Indexes an existing codebase at the given path. Use this when the
    developer already has code in this folder. If the folder isn't a
    git repository yet, this initializes git automatically without
    touching any existing files. Runs in the background — use
    check_indexing_status to monitor progress."""
    resolved_path = _resolve_path(repo_path)

    if not os.path.exists(resolved_path):
        return f"Path does not exist: {resolved_path}"

    ensure_git_baseline(resolved_path)

    acquired, repo = _try_acquire_lock(resolved_path)
    if not acquired:
        return f"{repo.repo_name} is already being indexed by another session."

    await run_full_index(repo.id, resolved_path)

    return (
        f"Indexing started for {repo.repo_name}. repo_id={repo.id}. "
        f"Use check_indexing_status(repo_id={repo.id}) to monitor progress."
    )

async def check_indexing_status(repo_id: int) -> str:
    """Checks the current indexing progress for a repo. Use this after
    calling setup_repo to monitor completion. Returns files processed,
    nodes embedded, and any errors."""
    with Session(engine) as session:
        session.expire_all()
        repo = session.get(LocalRepo, repo_id)

        if not repo:
            return f"Repo {repo_id} not found."

        progress = 0
        if repo.last_run_files_total:
            progress = round(
                (repo.last_run_files_processed / repo.last_run_files_total) * 100, 1
            )

        return (
            f"{repo.repo_name}\n"
            f"Status: {repo.index_status}\n"
            f"Progress: {repo.last_run_files_processed}/{repo.last_run_files_total} files ({progress}%)\n"
            f"Nodes embedded: {repo.last_run_nodes_embedded}\n"
            f"Total indexed nodes: {repo.total_node_count}\n"
            f"Error: {repo.last_run_error or 'none'}"
        )


async def check_repo_changes(repo_id: int) -> str:
    """Checks which files changed on disk since the last index, using
    git status and git diff. Call this before searching if you're unsure
    whether your indexed knowledge is current — for example, at the start
    of a new task. Returns changed, added, and deleted files. Follow up
    with index_file on each changed file, or setup_repo for a full
    refresh if many files changed."""
    with Session(engine) as session:
        repo = session.get(LocalRepo, repo_id)
        if not repo:
            return f"Repo {repo_id} not found."
        repo_path, last_sha = repo.repo_path, repo.last_commit_sha

    if not is_git_repo(repo_path):
        return f"'{repo_path}' is not a git repository."

    changes = _check_repo_changes(repo_path, last_sha)

    if not changes["has_changes"]:
        return "No changes detected since last index. Index is current."

    lines = ["Changes detected since last index:\n"]
    if changes["changed_files"]:
        lines.append(f"Changed/added ({len(changes['changed_files'])}):")
        lines.extend(f"  {f}" for f in changes["changed_files"])
    if changes["deleted_files"]:
        lines.append(f"\nDeleted ({len(changes['deleted_files'])}):")
        lines.extend(f"  {f}" for f in changes["deleted_files"])

    lines.append(
        "\nCall index_file for each changed file to update the index, "
        "or setup_repo for a full refresh if many files changed."
    )

    return "\n".join(lines)


async def index_file(repo_id: int, file_path: str) -> str:
    """Indexes or re-indexes a single file. Call this whenever you become
    aware that a file was created or modified — whether you wrote it
    directly, the developer told you they added it, or check_repo_changes
    flagged it. Reads whatever currently exists on disk at the given
    path — the filesystem is always the ground truth. Only genuinely
    changed functions get re-embedded; unchanged code in the same file
    is skipped automatically."""
    with Session(engine) as session:
        repo = session.get(LocalRepo, repo_id)
        if not repo:
            return f"Repo {repo_id} not found."
        repo_path = repo.repo_path

    result = await run_file_index(repo_id, repo_path, file_path)

    if result.get("deleted"):
        write_repo(repo_id, last_trigger_type="incremental")
        return f"{file_path} no longer exists — removed from index."

    if "error" in result:
        return f"Failed to index {file_path}: {result['error']}"

    write_repo(repo_id, last_trigger_type="incremental")

    return (
        f"{file_path}: {result['total_nodes']} nodes total, "
        f"{result['newly_embedded']} newly embedded, "
        f"{result['skipped_unchanged']} unchanged (skipped), "
        f"{result['removed_stale']} removed."
    )
