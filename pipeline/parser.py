import tree_sitter_python
from tree_sitter import Language, Parser
from typing import Optional
from dataclasses import dataclass, field

PY_LANGUAGE = Language(tree_sitter_python.language())
_parser = Parser(PY_LANGUAGE)


@dataclass
class ParsedNode:
    name: str
    node_type: str
    body: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str | None = None
    parameters: str | None = None
    parent_class: str | None = None


@dataclass
class ParseResult:
    file_path: str
    status: str
    nodes: list[ParsedNode] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fallback_used: bool = False


def _node_text(source: bytes, node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _extract_docstring(source: bytes, node) -> Optional[str]:
    for child in node.children:
        if child.type == "block":
            for bc in child.children:
                if bc.type == "expression_statement":
                    for ec in bc.children:
                        if ec.type == "string":
                            raw = _node_text(source, ec)
                            return (
                                raw.strip()
                                .strip('"""')
                                .strip("'''")
                                .strip('"')
                                .strip("'")
                                .strip()
                            )
    return None


def _extract_parameters(source: bytes, node) -> Optional[str]:
    for child in node.children:
        if child.type == "parameters":
            return _node_text(source, child)
    return None


def _walk(
    source: bytes, node, file_path: str, parent_class: Optional[str] = None
) -> list[ParsedNode]:
    results = []
    for child in node.children:
        if child.type == "function_definition":
            name_node = child.child_by_field_name("name")
            name = _node_text(source, name_node) if name_node else "unknown"
            node_type = "method" if parent_class else "function"
            results.append(
                ParsedNode(
                    name=name,
                    node_type=node_type,
                    body=_node_text(source, child),
                    file_path=file_path,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    docstring=_extract_docstring(source, child),
                    parameters=_extract_parameters(source, child),
                    parent_class=parent_class,
                )
            )
        elif child.type == "class_defintion":
            name_node = child.child_by_field_name("name")
            class_name = _node_text(source, name_node) if name_node else "unknown"
            results.append(
                ParsedNode(
                    name=class_name,
                    node_type="class",
                    body=_node_text(source, child),
                    file_path=file_path,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    docstring=_extract_docstring(source, child),
                    parameters=None,
                    parent_class=None,
                )
            )
            results.extend(_walk(source, child, file_path, parent_class=class_name))
        else:
            results.extend(_walk(source, child, file_path, parent_class=parent_class))
    return results


def _text_chunk_fallback(content: str, file_path: str) -> list[ParsedNode]:
    """
    Deterministic Tree-sitter failures (syntax errors, malformed code)
    are never fixed by retrying — the content doesn't change between
    attempts. This fallback runs immediately on first failure, no retry.
    """
    lines = content.split("\n")
    chunk_size = 50
    overlap = 10
    chunks = []
    i = 0
    idx = 0

    while i < len(lines):
        chunk_lines = lines[i : i + chunk_size]
        chunks.append(
            ParsedNode(
                name=f"chunk_{idx}",
                node_type="function",
                body="\n".join(chunk_lines),
                file_path=file_path,
                start_line=i + 1,
                end_line=min(i + chunk_size, len(lines)),
                docstring=None,
                parameters=None,
                parent_class=None,
            )
        )
        i += chunk_size - overlap
        idx += 1

    return chunks


def parse_file(file_path: str, content: str) -> ParseResult:
    """Parses file content into structured nodes via Tree-sitter.
    On deterministic parse failure, falls back to fixed-size text
    chunks immediately"""
    source = content.encode("utf-8")

    try:
        tree = _parser.parse(source)
        had_syntax_errors = tree.root_node.has_error

        nodes = _walk(source, tree.root_node, file_path)

        if not nodes:
            return ParseResult(file_path=file_path, status="success", nodes=[])

        status = "partial" if had_syntax_errors else "success"
        errors = (
            ["File contains syntax errors — partial AST extracted"]
            if had_syntax_errors
            else []
        )

        return ParseResult(
            file_path=file_path,
            status=status,
            nodes=nodes,
            errors=errors,
            fallback_used=False,
        )

    except Exception as e:
        fallback_nodes = _text_chunk_fallback(content, file_path)
        return ParseResult(
            file_path=file_path,
            status="partial",
            nodes=fallback_nodes,
            errors=[f"AST parsing failed: {str(e)} — text chunking fallback used"],
            fallback_used=True,
        )
