import hashlib
from dataclasses import dataclass

from pipeline.parser import ParsedNode


@dataclass
class Chunk:
    name: str
    embed_text: str
    document_text: str
    metadata: dict


def _build_embed_text(node: ParsedNode) -> str:
    """
    Combines identity, location, signature, and docstring with the
    raw body so the embedding captures intent and structure, not
    just mechanical implementation text.
    """
    parts = []

    if node.node_type == "method" and node.parent_class:
        parts.append(f"[METHOD] {node.parent_class}.{node.name}")
    elif node.node_type == "class":
        parts.append(f"[CLASS] {node.name}")
    else:
        parts.append(f"[FUNCTION] {node.name}")

    parts.append(f"File: {node.file_path}")

    if node.parent_class:
        parts.append(f"Class: {node.parent_class}")

    if node.parameters:
        parts.append(f"Parameters: {node.parameters}")

    if node.docstring:
        parts.append(f"Description: {node.docstring}")

    parts.append(f"Code:\n{node.body}")

    return "\n".join(parts)


def build_chunk_id(repo_id: int, node: ParsedNode) -> str:
    """
    Deterministic hash of a node's identity and content.

    Same file_path + name + node_type + body → same ID every time.
    This is the mechanism that makes re-indexing surgical: a file-level
    git diff only tells you WHICH FILE to look at again. This hash
    tells you, at function granularity, whether anything inside that
    file actually changed. Unchanged functions keep their existing ID
    and are never re-embedded, no matter how many other lines in the
    same file were touched.
    """
    raw = f"{repo_id}:{node.file_path}:{node.name}:{node.node_type}:{node.body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_chunk(node: ParsedNode, repo_id: int) -> Chunk:
    return Chunk(
        name=build_chunk_id(repo_id, node),
        embed_text=_build_embed_text(node),
        document_text=node.body,
        metadata={
            "repo_id": repo_id,
            "file_path": node.file_path,
            "node_name": node.name,
            "node_type": node.node_type,
            "parent_class": node.parent_class or "",
            "start_line": node.start_line,
            "end_line": node.end_line,
        },
    )


def build_chunks(nodes: list[ParsedNode], repo_id: int) -> list[Chunk]:
    return [build_chunk(node, repo_id) for node in nodes]
