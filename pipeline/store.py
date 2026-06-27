import chromadb
from dataclasses import dataclass
from local_database import CHROMA_PATH

_client = chromadb.PersistentClient(path=str(CHROMA_PATH))


@dataclass
class SearchResult:
    node_name: str
    node_type: str
    file_path: str
    start_line: int
    end_line: int
    parent_class: str
    document_text: str
    similarity: float


def get_collection(repo_id: int, provider: str, model_name: str):
    """
    One collection per repo + provider + model combination.
    Switching embedding models for a repo creates a new, isolated
    collection rather than mixing incompatible vector spaces.
    """
    safe_model = model_name.replace("/", "_").replace(".", "-")
    collection_name = f"repo_{repo_id}_{provider}_{safe_model}"
    return _client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(
    repo_id: int,
    provider: str,
    model_name: str,
    ids: list[str],
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict],
) -> None:
    if not ids:
        return
    collection = get_collection(repo_id, provider, model_name)
    collection.upsert(
        ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas
    )


def get_existing_ids_for_file(
    repo_id: int,
    provider: str,
    model_name: str,
    file_path: str,
) -> set[str]:
    """
    Used by index_file to determine which nodes in a specific file
    already exist in the index, so only genuinely new or changed
    content-addressed IDs get sent for embedding.
    """
    collection = get_collection(repo_id, provider, model_name)
    results = collection.get(
        where={"$and": [{"repo_id": repo_id}, {"file_path": file_path}]},
        include=[],
    )
    return set(results["ids"])


def get_existing_ids_for_repo(repo_id: int, provider: str, model_name: str) -> set[str]:
    """
    Used by setup_repo to determine stale nodes after a full re-walk —
    any ID present before but not produced by the current parse
    belongs to deleted code and should be removed.
    """
    collection = get_collection(repo_id, provider, model_name)
    results = collection.get(
        where={"repo_id": repo_id},
        include=[],
    )
    return set(results["ids"])


def delete_ids(repo_id: int, provider: str, model_name: str, ids: list[str]) -> None:
    if not ids:
        return
    collection = get_collection(repo_id, provider, model_name)
    collection.delete(ids=ids)


def delete_file_nodes(
    repo_id: int, provider: str, model_name: str, file_path: str
) -> None:
    """
    Removes all nodes for a specific file. Called by index_file
    when fetch_single_file reports the file was deleted from disk.
    """
    collection = get_collection(repo_id, provider, model_name)
    collection.delete(where={"$and": [{"repo_id": repo_id}, {"file_path": file_path}]})


def delete_repo(repo_id: int, provider: str, model_name: str) -> None:
    collection = get_collection(repo_id, provider, model_name)
    collection.delete(where={"repo_id": repo_id})


def search(
    query_embedding: list[float],
    repo_id: int,
    provider: str,
    model_name: str,
    top_k: int = 5,
) -> list[SearchResult]:
    collection = get_collection(repo_id, provider, model_name)

    existing = collection.get(where={"repo_id": repo_id}, include=[])
    actual_count = len(existing["ids"])
    if actual_count == 0:
        return []

    safe_top_k = min(top_k, actual_count)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=safe_top_k,
        where={"repo_id": repo_id},
        include=["documents", "metadatas", "distances"],
    )

    search_results = []
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i]
        similarity = round(1 - (distance / 2), 4)

        search_results.append(
            SearchResult(
                node_name=meta.get("node_name", ""),
                node_type=meta.get("node_type", ""),
                file_path=meta.get("file_path", ""),
                start_line=meta.get("start_line", 0),
                end_line=meta.get("end_line", 0),
                parent_class=meta.get("parent_class", ""),
                document_text=results["documents"][0][i],
                similarity=similarity,
            )
        )

    return search_results


def get_by_name(
    repo_id: int,
    provider: str,
    model_name: str,
    node_name: str,
) -> dict:
    collection = get_collection(repo_id, provider, model_name)
    return collection.get(
        where={"$and": [{"repo_id": repo_id}, {"node_name": node_name}]},
        include=["documents", "metadatas"],
    )


def get_class_with_methods(
    repo_id: int,
    provider: str,
    model_name: str,
    class_name: str,
) -> tuple[dict, dict]:
    collection = get_collection(repo_id, provider, model_name)

    class_result = collection.get(
        where={
            "$and": [
                {"repo_id": repo_id},
                {"node_name": class_name},
                {"node_type": "class"},
            ]
        },
        include=["documents", "metadatas"],
    )
    method_results = collection.get(
        where={"$and": [{"repo_id": repo_id}, {"parent_class": class_name}]},
        include=["documents", "metadatas"],
    )
    return class_result, method_results


def list_symbols(
    repo_id: int,
    provider: str,
    model_name: str,
    symbol_type: str = "all",
) -> dict:
    collection = get_collection(repo_id, provider, model_name)
    conditions = [{"repo_id": repo_id}]
    if symbol_type != "all":
        conditions.append({"node_type": symbol_type})
    return collection.get(where={"$and": conditions}, include=["metadatas"])


def get_file_nodes(
    repo_id: int,
    provider: str,
    model_name: str,
    file_path: str,
) -> dict:
    collection = get_collection(repo_id, provider, model_name)
    return collection.get(
        where={"$and": [{"repo_id": repo_id}, {"file_path": file_path}]},
        include=["documents", "metadatas"],
    )
