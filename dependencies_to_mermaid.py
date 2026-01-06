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


def json_to_mermaid(
    data: Mapping[str, Any],
    tables: Set[str] = {},
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

    return _deps_to_mermaid(view_deps, tables, options=options)


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
    tables: Set[str],
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
        nodes.add(view)

        for dep in deps:
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

    lines: List[str] = [f"graph {direction}",
                        f"{options.indent}classDef chTable fill:#ffdd00,stroke:#000000,stroke-width:2px,color:#000000",
                        f"{options.indent}classDef chView fill:#d6e4f8,stroke:#154360,stroke-width:2px,color:#154360",
                        ""]

    # Render nodes with types
    for n in nodes:
        if n in tables:
            lines.append(f"{options.indent}{n}:::chTable")
        else:
            lines.append(f"{options.indent}{n}:::chView")

    lines.append("")

    # Render edges
    if edges:
        for src, dst in edges:
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