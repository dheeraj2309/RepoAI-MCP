from sqlmodel import Session, select

from local_models import LocalRepo
from local_database import engine
from pipeline.embedder import embed_query
from pipeline.store import (
    search as _search,
    get_by_name,
    get_class_with_methods,
    list_symbols as _list_symbols,
    get_file_nodes,
)


def _get_indexed_repo(repo_id: int) -> LocalRepo | None:
    with Session(engine) as session:
        repo = session.get(LocalRepo, repo_id)
        if not repo or repo.index_status != "indexed":
            return None
        return repo


def _format_results(items: list[dict]) -> str:
    if not items:
        return "No relevant code found."
    output = []
    for m in items:
        header = f"[{m['node_type'].upper()}] {m['node_name']}"
        if m.get("parent_class"):
            header += f" (in class {m['parent_class']})"
        if "similarity" in m:
            header += f" — similarity: {m['similarity']}"
        location = f"File: {m['file_path']} | Lines: {m['start_line']}-{m['end_line']}"
        output.append(f"{header}\n{location}\n\n{m['document']}")
        output.append("─" * 60)
    return "\n".join(output)


async def search_codebase(query: str, repo_id: int, top_k: int = 5) -> str:
    """Semantically searches the indexed codebase for functions, methods,
    and classes matching a concept. Use this as your primary navigation
    tool instead of relying on memory of code you wrote earlier in this
    session. Results include full function body, file path, and line
    numbers. Import statements visible in returned code indicate
    dependencies — use get_file_summary on those files to explore further.
    Use technical programming terms, not natural language — describe
    what the code DOES, not what you want to know."""
    top_k = min(top_k, 10)

    repo = _get_indexed_repo(repo_id)
    if not repo:
        return f"Repo {repo_id} not found or not indexed."

    query_vector = await embed_query(
        query,
        provider=repo.embedding_provider,
        model_name=repo.embedding_model_name,
    )
    results = _search(
        query_vector, repo_id, repo.embedding_provider, repo.embedding_model_name, top_k
    )

    items = [
        {
            "node_name": r.node_name,
            "node_type": r.node_type,
            "file_path": r.file_path,
            "start_line": r.start_line,
            "end_line": r.end_line,
            "parent_class": r.parent_class,
            "document": r.document_text,
            "similarity": r.similarity,
        }
        for r in results
    ]

    header = f"Search: '{query}' in {repo.repo_name} — {len(items)} results\n\n"
    return header + _format_results(items)


async def get_function(function_name: str, repo_id: int) -> str:
    """Retrieves a specific function or method by exact name. Use this
    when you already know the name. More precise than search_codebase
    when the exact name is known. For class methods, provide just the
    method name, not ClassName.method."""
    repo = _get_indexed_repo(repo_id)
    if not repo:
        return f"Repo {repo_id} not found or not indexed."

    results = get_by_name(
        repo_id, repo.embedding_provider, repo.embedding_model_name, function_name
    )

    if not results["ids"]:
        return f"Function '{function_name}' not found."

    output = []
    for i in range(len(results["ids"])):
        meta = results["metadatas"][i]
        header = f"[{meta['node_type'].upper()}] {function_name}"
        if meta.get("parent_class"):
            header += f" (method of {meta['parent_class']})"
        location = f"File: {meta['file_path']} | Lines: {meta['start_line']}-{meta['end_line']}"
        output.append(f"{header}\n{location}\n\n{results['documents'][i]}")

    return "\n\n".join(output)


async def get_class(class_name: str, repo_id: int) -> str:
    """Retrieves a class definition and ALL of its methods in one call.
    Use this when you need to understand a complete class — more
    efficient than calling get_function repeatedly for each method."""
    repo = _get_indexed_repo(repo_id)
    if not repo:
        return f"Repo {repo_id} not found or not indexed."

    class_result, method_results = get_class_with_methods(
        repo_id,
        repo.embedding_provider,
        repo.embedding_model_name,
        class_name,
    )

    if not class_result["ids"] and not method_results["ids"]:
        return f"Class '{class_name}' not found."

    output = [f"[CLASS] {class_name}\n"]
    if class_result["ids"]:
        meta = class_result["metadatas"][0]
        output.append(
            f"File: {meta['file_path']} | Lines: {meta['start_line']}-{meta['end_line']}\n"
        )

    if method_results["ids"]:
        output.append(f"\nMethods ({len(method_results['ids'])}):\n" + "─" * 60)
        for i in range(len(method_results["ids"])):
            meta = method_results["metadatas"][i]
            output.append(
                f"\n[METHOD] {meta['node_name']} — Lines: {meta['start_line']}-{meta['end_line']}\n{method_results['documents'][i]}"
            )
            output.append("─" * 60)

    return "\n".join(output)


async def list_symbols(repo_id: int = None, symbol_type: str = "all") -> str:
    """Lists all indexed symbols in a repo — functions, methods, classes.
    Use this first to orient yourself in an unfamiliar codebase. If
    repo_id is omitted, lists all available indexed repos instead."""
    if repo_id is None:
        with Session(engine) as session:
            repos = session.exec(select(LocalRepo)).all()
        if not repos:
            return "No indexed repos found. Use setup_repo to add one."
        lines = ["Available repositories:\n"]
        for r in repos:
            lines.append(
                f"  repo_id={r.id}  {r.repo_name}  ({r.total_node_count} nodes, status={r.index_status})"
            )
        return "\n".join(lines)

    repo = _get_indexed_repo(repo_id)
    if not repo:
        return f"Repo {repo_id} not found or not indexed."

    results = _list_symbols(
        repo_id, repo.embedding_provider, repo.embedding_model_name, symbol_type
    )

    if not results["ids"]:
        return f"No symbols found in repo {repo_id}."

    by_file: dict[str, list] = {}
    for meta in results["metadatas"]:
        by_file.setdefault(meta["file_path"], []).append(meta)

    lines = [f"Symbols in {repo.repo_name} ({len(results['ids'])} total):\n"]
    for file_path, symbols in sorted(by_file.items()):
        lines.append(f"\n{file_path}")
        for s in symbols:
            name = (
                f"{s['parent_class']}.{s['node_name']}"
                if s.get("parent_class")
                else s["node_name"]
            )
            lines.append(
                f"  [{s['node_type'].upper()}] {name} (lines {s['start_line']}-{s['end_line']})"
            )

    return "\n".join(lines)


async def get_file_summary(file_path: str, repo_id: int) -> str:
    """Returns all indexed functions and classes from a specific file.
    Use this to understand a complete module, or when following import
    threads found in other search results."""
    repo = _get_indexed_repo(repo_id)
    if not repo:
        return f"Repo {repo_id} not found or not indexed."

    results = get_file_nodes(
        repo_id, repo.embedding_provider, repo.embedding_model_name, file_path
    )

    if not results["ids"]:
        return f"No indexed nodes for '{file_path}'."

    output = [f"File: {file_path} — {len(results['ids'])} nodes\n" + "─" * 60]
    for i in range(len(results["ids"])):
        meta = results["metadatas"][i]
        header = f"[{meta['node_type'].upper()}] {meta['node_name']}"
        if meta.get("parent_class"):
            header += f" (in {meta['parent_class']})"
        output.append(
            f"\n{header} — Lines: {meta['start_line']}-{meta['end_line']}\n{results['documents'][i]}"
        )
        output.append("─" * 60)

    return "\n".join(output)
