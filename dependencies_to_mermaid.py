# mermaid_deps.py
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple


@dataclass(frozen=True)
class MermaidOptions:
    """
    Options for Mermaid graph generation.
    """
    direction: str = "LR"          # LR, TB, RL, BT
    indent: str = "  "
    dedupe_edges: bool = True
    include_isolated_nodes: bool = True  # nodes with no edges still appear


class MermaidDependencyGraphError(ValueError):
    pass


# Conservative allow-list for unquoted Mermaid node ids in flowcharts.
# Allows common database-like identifiers such as: schema.table, db.schema.table, a_b, a-b, a:b
_ALLOWED_NODE_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")


def json_to_mermaid(
    data: Mapping[str, Any],
    *,
    options: MermaidOptions = MermaidOptions()
) -> str:
    """
    Convert JSON-like mapping with key 'view_dependencies' (dict[str, list[str]])
    into a Mermaid diagram.

    Rules:
      1) Node name matches the string in JSON (no quotes added).
      2) Edges use '-.->'
    """
    if "view_dependencies" not in data:
        raise MermaidDependencyGraphError("Missing required key: 'view_dependencies'")

    raw = data["view_dependencies"]
    if not isinstance(raw, dict):
        raise MermaidDependencyGraphError("'view_dependencies' must be a dictionary")

    # Validate and normalize: dict[str, list[str]]
    view_deps: Dict[str, List[str]] = {}
    for view, deps in raw.items():
        if not isinstance(view, str):
            raise MermaidDependencyGraphError("All keys in 'view_dependencies' must be strings")

        if deps is None:
            deps_list: List[str] = []
        elif isinstance(deps, list):
            if not all(isinstance(x, str) for x in deps):
                raise MermaidDependencyGraphError(
                    f"Dependencies for '{view}' must be a list of strings"
                )
            deps_list = deps
        else:
            raise MermaidDependencyGraphError(
                f"Dependencies for '{view}' must be a list (or null)"
            )

        view_deps[view] = deps_list

    return _deps_to_mermaid(view_deps, options=options)


def loads_json_to_mermaid(
    json_str: str,
    *,
    options: MermaidOptions = MermaidOptions()
) -> str:
    """
    Parse a JSON string and convert it to Mermaid.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise MermaidDependencyGraphError(f"Invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise MermaidDependencyGraphError("Top-level JSON must be an object/dictionary")

    return json_to_mermaid(data, options=options)


def _deps_to_mermaid(
    view_deps: Mapping[str, Sequence[str]],
    *,
    options: MermaidOptions
) -> str:
    direction = options.direction.strip().upper()
    if direction not in {"LR", "TB", "RL", "BT"}:
        raise MermaidDependencyGraphError("options.direction must be one of LR, TB, RL, BT")

    # Collect nodes and edges
    nodes: Set[str] = set()
    edges: List[Tuple[str, str]] = []

    for view, deps in view_deps.items():
        _validate_node_name(view)
        nodes.add(view)

        for dep in deps:
            _validate_node_name(dep)
            nodes.add(dep)
            edges.append((dep, view))

    if options.dedupe_edges:
        seen: Set[Tuple[str, str]] = set()
        deduped: List[Tuple[str, str]] = []
        for e in edges:
            if e not in seen:
                seen.add(e)
                deduped.append(e)
        edges = deduped

    lines: List[str] = [f"graph {direction}"]

    if edges:
        for src, dst in edges:
            # IMPORTANT: no quotes around node names
            lines.append(f"{options.indent}{src} -.-> {dst}")
    elif options.include_isolated_nodes:
        for n in sorted(nodes):
            lines.append(f"{options.indent}{n}")

    if options.include_isolated_nodes and edges:
        connected: Set[str] = set()
        for src, dst in edges:
            connected.add(src)
            connected.add(dst)

        isolated = sorted(nodes - connected)
        for n in isolated:
            lines.append(f"{options.indent}{n}")

    return "\n".join(lines) + "\n"


def _validate_node_name(name: str) -> None:
    """
    Validate node name for unquoted Mermaid usage.

    Since the user requires NO quotes, we enforce a conservative safe set:
    - Letters/digits/underscore/dot/colon/hyphen
    """
    if not _ALLOWED_NODE_RE.match(name):
        raise MermaidDependencyGraphError(
            f"Invalid node name for unquoted Mermaid output: {name!r}. "
            "Allowed pattern: [A-Za-z0-9_.:-]+ (no spaces, quotes, brackets, slashes, etc.)"
        )
