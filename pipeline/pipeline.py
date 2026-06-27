import json
from datetime import datetime
from typing import Optional
from sqlmodel import Session

from pipeline.fetcher import (
    is_git_repo,
    fetch_all_files,
    fetch_single_file,
    get_local_head_sha,
)
from pipeline.parser import parse_file
from pipeline.chunker import build_chunks
from pipeline.embedder import embed_texts
from pipeline.store import (
    upsert_chunks,
    get_existing_ids_for_repo,
    get_existing_ids_for_file,
    delete_ids,
    delete_file_nodes,
)
from local_models import LocalRepo
from local_database import engine

FILE_BATCH_SIZE = 50


def write_repo(repo_id: int, **kwargs):
    with Session(engine) as session:
        repo = session.get(LocalRepo, repo_id)
        if repo:
            for k, v in kwargs.items():
                setattr(repo, k, v)
            session.add(repo)
            session.commit()


# ── Full bulk index — used by setup_repo ──────────────────────────────────


async def run_full_index(repo_id: int, repo_path: str):
    """
    Walks the entire repo, parses every file, embeds and stores
    every node. Used once on first setup, or for an explicit full
    refresh. Processes in batches so memory stays flat and progress
    is visible incrementally rather than only at the very end.
    """
    if not is_git_repo(repo_path):
        write_repo(
            repo_id, index_status="failed", last_run_error="Not a git repository."
        )
        return

    started_at = datetime.now()
    error_log = []
    all_ids = set()
    nodes_embedded = 0
    files_processed = 0

    try:
        with Session(engine) as session:
            repo = session.get(LocalRepo, repo_id)
            provider, model_name = repo.embedding_provider, repo.embedding_model_name

        fetch_result = fetch_all_files(repo_path)
        write_repo(repo_id, last_run_files_total=fetch_result.total_fetched)

        success_files = [f for f in fetch_result.files if f.status == "success"]

        for b in range(0, len(success_files), FILE_BATCH_SIZE):
            batch_files = success_files[b : b + FILE_BATCH_SIZE]
            batch_chunks = []

            # ── Parse + chunk (cheap, no API calls) ──────────────────────
            for f in batch_files:
                parse_result = parse_file(f.path, f.content)

                if parse_result.errors:
                    error_log.append({"file": f.path, "errors": parse_result.errors})

                chunks = build_chunks(parse_result.nodes, repo_id)
                batch_chunks.extend(chunks)
                for c in chunks:
                    all_ids.add(c.id)

                files_processed += 1

            if not batch_chunks:
                write_repo(repo_id, last_run_files_processed=files_processed)
                continue

            # ── Embed must fully succeed before ChromaDB is touched ──────
            try:
                texts = [c.embed_text for c in batch_chunks]
                embeddings = await embed_texts(
                    texts, provider=provider, model_name=model_name
                )
            except Exception as e:
                error_log.append(
                    {"batch_start": b, "stage": "embedding", "error": str(e)}
                )
                write_repo(repo_id, last_run_files_processed=files_processed)
                continue  # batch skipped entirely, ChromaDB never written for it

            upsert_chunks(
                repo_id,
                provider,
                model_name,
                ids=[c.id for c in batch_chunks],
                embeddings=embeddings,
                documents=[c.document_text for c in batch_chunks],
                metadatas=[c.metadata for c in batch_chunks],
            )
            nodes_embedded += len(batch_chunks)

            write_repo(
                repo_id,
                last_run_files_processed=files_processed,
                last_run_nodes_embedded=nodes_embedded,
            )

        # ── Remove nodes for code that no longer exists ──────────────────
        existing_ids = get_existing_ids_for_repo(repo_id, provider, model_name)
        stale_ids = list(existing_ids - all_ids)
        if stale_ids:
            delete_ids(repo_id, provider, model_name, stale_ids)

        current_sha = get_local_head_sha(repo_path)
        completed_at = datetime.now()

        write_repo(
            repo_id,
            index_status="indexed",
            total_node_count=nodes_embedded,
            last_indexed_at=completed_at,
            last_run_completed_at=completed_at,
            last_commit_sha=current_sha,
            last_run_error=json.dumps(error_log) if error_log else None,
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        write_repo(repo_id, index_status="failed", last_run_error=str(e))


# ── Single-file incremental index — used by index_file ───────────────────


async def run_file_index(repo_id: int, repo_path: str, file_path: str) -> dict:
    """
    Re-parses one file and embeds only the nodes whose content-addressed
    ID is genuinely new — unchanged functions inside an otherwise-edited
    file are never re-embedded. Cheap, fast, called frequently during
    active development.
    """
    with Session(engine) as session:
        repo = session.get(LocalRepo, repo_id)
        provider, model_name = repo.embedding_provider, repo.embedding_model_name

    fetched = fetch_single_file(repo_path, file_path)

    if fetched.status == "failed" and fetched.error == "deleted":
        # File no longer exists — remove all its nodes from the index
        delete_file_nodes(repo_id, provider, model_name, file_path)
        return {
            "file": file_path,
            "deleted": True,
            "newly_embedded": 0,
            "skipped_unchanged": 0,
        }

    if fetched.status == "failed":
        return {"file": file_path, "error": fetched.error}

    parse_result = parse_file(fetched.path, fetched.content)
    chunks = build_chunks(parse_result.nodes, repo_id)

    new_ids = {c.id for c in chunks}
    existing_ids = get_existing_ids_for_file(repo_id, provider, model_name, file_path)

    # Only genuinely new/changed content-addressed IDs need embedding —
    # this is the function-level granularity that prevents re-embedding
    # an entire file over an unrelated single-line change.
    chunks_to_embed = [c for c in chunks if c.id not in existing_ids]

    newly_embedded = 0
    if chunks_to_embed:
        try:
            texts = [c.embed_text for c in chunks_to_embed]
            embeddings = await embed_texts(
                texts, provider=provider, model_name=model_name
            )
            upsert_chunks(
                repo_id,
                provider,
                model_name,
                ids=[c.id for c in chunks_to_embed],
                embeddings=embeddings,
                documents=[c.document_text for c in chunks_to_embed],
                metadatas=[c.metadata for c in chunks_to_embed],
            )
            newly_embedded = len(chunks_to_embed)
        except Exception as e:
            return {"file": file_path, "error": f"Embedding failed: {str(e)}"}

    # Remove nodes that existed before but are no longer produced
    stale_ids = list(existing_ids - new_ids)
    if stale_ids:
        delete_ids(repo_id, provider, model_name, stale_ids)

    return {
        "file": file_path,
        "total_nodes": len(chunks),
        "newly_embedded": newly_embedded,
        "skipped_unchanged": len(chunks) - newly_embedded,
        "removed_stale": len(stale_ids),
    }
