#!/usr/bin/env python3
"""
ClickHouse VIEW dependency extractor (ANTLR-based)

What it does:
1) Connects to ClickHouse using clickhouse_connect
2) Collects all ClickHouse views (VIEW / MATERIALIZED VIEW / LIVE VIEW)
3) Fetches DDL for each view
4) Parses DDL using ClickHouse ANTLR4 grammar (from ClickHouse repo utils/antlr)
5) Produces: { "db.view": ["db.table1", "table2", ...] }

Prereqs (one-time):
- pip install clickhouse-connect antlr4-python3-runtime

Generate parser from ClickHouse grammar (one-time):
1) git clone https://github.com/ClickHouse/ClickHouse.git
2) cd ClickHouse/utils/antlr
3) Download ANTLR tool (jar) from https://www.antlr.org/download.html or use your antlr4 installation
4) Generate Python parser into some folder, e.g. ./generated_ch_parser:

   java -jar antlr-4.13.2-complete.jar -Dlanguage=Python3 -visitor -o generated_ch_parser \
        ClickHouseLexer.g4 ClickHouseParser.g4

5) Ensure generated_ch_parser/ is importable (same folder as this script or on PYTHONPATH)

Run:
  CH_HOST=localhost CH_PORT=8123 CH_USER=default CH_PASSWORD= CH_DATABASE=default \
  python3 ch_view_deps.py <output.mmd>

Output:
- Prints JSON to stdout
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from pathlib import Path

import clickhouse_connect

# ---- ANTLR runtime ----
from antlr4 import CommonTokenStream, InputStream

# ---- Generated from ClickHouse/utils/antlr ----
# Adjust these imports to match where you generated the parser.
# Example: if you generated into ./generated_ch_parser, you might do:
#   from generated_ch_parser.ClickHouseLexer import ClickHouseLexer
#   from generated_ch_parser.ClickHouseParser import ClickHouseParser
#   from generated_ch_parser.ClickHouseParserVisitor import ClickHouseParserVisitor
#
# Below assumes they are importable directly.
from generated_ch_parser.ClickHouseLexer import ClickHouseLexer
from generated_ch_parser.ClickHouseParser import ClickHouseParser
from generated_ch_parser.ClickHouseParserVisitor import ClickHouseParserVisitor
from dependencies_to_mermaid import MermaidOptions, json_to_mermaid

# ----------------------------
# Helpers
# ----------------------------

_IDENTIFIER_CLEAN_RE = re.compile(r'(^`|`$|^"|"$|^\[|\]$)')

def clean_ident(s: str) -> str:
    """Remove common ClickHouse identifier quoting."""
    s = s.strip()
    s = _IDENTIFIER_CLEAN_RE.sub("", s)
    # ClickHouse can escape backticks by doubling; handle minimal cases:
    s = s.replace("``", "`").replace('""', '"')
    return s

def split_qualified(name: str) -> Tuple[Optional[str], str]:
    """
    Split db.table -> (db, table). If unqualified -> (None, name).
    Handles backticks/quotes in a simplistic way by cleaning each part.
    """
    name = name.strip()
    parts = name.split(".")
    if len(parts) == 2:
        return clean_ident(parts[0]), clean_ident(parts[1])
    return None, clean_ident(name)

def normalize_table_name(
    raw: str,
    default_db: Optional[str],
) -> str:
    """
    Normalize to 'db.table' when db is known, otherwise return 'table'.
    """
    db, tbl = split_qualified(raw)
    if db:
        return f"{db}.{tbl}"
    if default_db:
        return f"{default_db}.{tbl}"
    return tbl


# ----------------------------
# ANTLR Visitor to collect table identifiers
# ----------------------------

class TableNameCollector(ClickHouseParserVisitor):
    """
    Best-effort table collector for ClickHouse SQL parse trees.

    Grammar evolves; instead of relying on one exact rule name, this visitor:
    - Searches for context types whose class name suggests table identifiers
    - Extracts identifier text from those contexts
    - Tries to avoid collecting CTE names (WITH ...)
    - Tries to avoid collecting function names / columns by being conservative
      (still best-effort; validate against your grammar version)

    If you want 100% precision, tailor visit methods to your specific
    generated ClickHouseParser rules for:
      - tableExpr / tableExpression
      - joinExpr
      - tableIdentifier
      - tableFunctionExpr (exclude)
      - cte statements (exclude)
    """

    def __init__(self, default_db: Optional[str]):
        super().__init__()
        self.default_db = default_db
        self.tables: Set[str] = set()
        self.cte_names: Set[str] = set()

    # --- CTE capture (best-effort) ---
    def visitWithClause(self, ctx):  # type: ignore[override]
        # Try to collect CTE aliases so we can exclude them later.
        # Depending on grammar, ctx might have children like: withExprList / withExpr
        # We'll do a generic walk and look for patterns resembling "name AS (select ...)".
        text = ctx.getText()
        # Extremely heuristic: capture leading identifiers before "AS("
        # Example: WITH cte AS (SELECT ...) SELECT ...
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)(?=AS\()', text, flags=re.IGNORECASE):
            self.cte_names.add(clean_ident(m.group(1)))
        return self.visitChildren(ctx)

    # --- Core extraction heuristics ---
    def visitChildren(self, node):  # type: ignore[override]
        # Before default recursion, attempt to extract if this looks like a table identifier context.
        cls_name = type(node).__name__

        # These names are common in ClickHouse grammars; adjust if needed.
        looks_like_table_ctx = any(
            key in cls_name.lower()
            for key in [
                "tableidentifier",
                "tableexpr",
                "tableexpression",
                "join",
                "fromclause",
            ]
        )

        if looks_like_table_ctx:
            self._try_extract_table_like_text(node)

        return super().visitChildren(node)

    def _try_extract_table_like_text(self, ctx) -> None:
        """
        Attempt to extract table names from a context by checking for
        subrules/methods often present in the generated parser.
        """
        # 1) Prefer explicit tableIdentifier() if present
        if hasattr(ctx, "tableIdentifier"):
            try:
                ti = ctx.tableIdentifier()
                if ti is not None:
                    self._add_table_text(ti.getText())
            except Exception:
                pass

        # 2) Some grammars use tableIdentifierWithDot / compoundIdentifier
        for meth in ["compoundIdentifier", "identifierOrNull", "identifier"]:
            if hasattr(ctx, meth):
                try:
                    sub = getattr(ctx, meth)()
                    # If it's a list, iterate; else treat as single.
                    if sub is None:
                        continue
                    if isinstance(sub, list):
                        # A compound identifier might be split into tokens; fallback to ctx.getText()
                        # We don't add from bare identifiers; too noisy.
                        pass
                except Exception:
                    pass

        # 3) Last-resort: look for patterns after FROM/JOIN in ctx.getText()
        # This is *not* the main mechanism; it only helps when grammar method
        # names differ.
        text = ctx.getText()

        # Avoid table functions like: FROM s3('...') etc by requiring identifier-ish tokens.
        # Capture:
        #   FROM db.table
        #   JOIN `db`.`table`
        #   FROM table AS t
        # Keep it simple; ClickHouse allows many constructs.
        for m in re.finditer(
            r'\b(?:FROM|JOIN)\b\s*([`"\[]?[A-Za-z_][A-Za-z0-9_]*[`"\]]?(?:\s*\.\s*[`"\[]?[A-Za-z_][A-Za-z0-9_]*[`"\]]?)?)',
            text,
            flags=re.IGNORECASE,
        ):
            candidate = re.sub(r"\s+", "", m.group(1))
            self._add_table_text(candidate)

    def _add_table_text(self, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return

        # Exclude obvious non-tables
        if "(" in raw or ")" in raw:
            return

        # If grammar gave us something like db.table or table
        norm = normalize_table_name(raw, self.default_db)

        # Exclude CTEs by name (unqualified compare)
        _, tbl = split_qualified(norm)
        if tbl in self.cte_names:
            return

        self.tables.add(norm)


def parse_view_tables(ddl: str, default_db: Optional[str]) -> List[str]:
    """
    Parse a CREATE VIEW/MV/LIVE VIEW statement and return referenced tables.
    """
    input_stream = InputStream(ddl)
    lexer = ClickHouseLexer(input_stream)
    stream = CommonTokenStream(lexer)
    parser = ClickHouseParser(stream)

    # Entry rule name may vary by grammar version.
    # Common options: parser.statement(), parser.query(), parser.sqlStatements(), etc.
    # We'll try a few.
    root = None
    for entry in ["statement", "sqlStatement", "sqlStatements", "query", "selectStmt"]:
        if hasattr(parser, entry):
            try:
                root = getattr(parser, entry)()
                break
            except Exception:
                continue
    if root is None:
        raise RuntimeError("Could not find a suitable entry rule on ClickHouseParser for this grammar.")

    visitor = TableNameCollector(default_db=default_db)
    visitor.visit(root)
    return sorted(visitor.tables)


# ----------------------------
# ClickHouse access
# ----------------------------

@dataclass(frozen=True)
class CHConnInfo:
    host: str
    port: int
    username: str
    password: str
    database: Optional[str]
    secure: bool

def get_conn_info_from_env() -> CHConnInfo:
    host = os.getenv("CH_HOST", "localhost")
    port = int(os.getenv("CH_PORT", "18123"))
    username = os.getenv("CH_USER", "default")
    password = os.getenv("CH_PASSWORD", "")
    database = os.getenv("CH_DATABASE")  # optional
    secure = os.getenv("CH_SECURE", "0").lower() in ("1", "true", "yes")
    return CHConnInfo(host, port, username, password, database, secure)

def connect_ch(ci: CHConnInfo):
    return clickhouse_connect.get_client(
        host=ci.host,
        port=ci.port,
        username=ci.username,
        password=ci.password,
        database=ci.database,
        secure=ci.secure,
    )

def fetch_views(client, include_system: bool = False) -> List[Tuple[str, str, str]]:
    """
    Returns list of (database, name, engine).
    Includes VIEW, MaterializedView, LiveView when present.
    """
    where_db = "" if include_system else "AND database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema')"
    sql = f"""
        SELECT database, name, engine
        FROM system.tables
        WHERE (engine IN ('View', 'MaterializedView', 'LiveView')
               OR engine LIKE '%View%')
          {where_db}
        ORDER BY database, name
    """
    rows = client.query(sql).result_rows
    return [(r[0], r[1], r[2]) for r in rows]

def fetch_tables(client, include_system: bool = False) -> List[Tuple[str, str, str]]:
    """
    Returns list of (database, name, engine).
    Includes Tables.
    """
    where_db = "" if include_system else "AND database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema')"
    sql = f"""
        SELECT database, name, engine
        FROM system.tables
        WHERE engine NOT LIKE '%View%'
          {where_db}
        ORDER BY database, name
    """
    rows = client.query(sql).result_rows
    return [(r[0], r[1], r[2]) for r in rows]

def fetch_view_ddl(client, database: str, name: str) -> str:
    """
    Prefer system.tables.create_table_query (fast), fallback to SHOW CREATE TABLE.
    """
    ddl_sql = """
        SELECT create_table_query
        FROM system.tables
        WHERE database = %(db)s AND name = %(name)s
        LIMIT 1
    """
    res = client.query(ddl_sql, parameters={"db": database, "name": name}).result_rows
    if res and res[0] and res[0][0]:
        return res[0][0]

    show_sql = f"SHOW CREATE TABLE `{database}`.`{name}`"
    show = client.query(show_sql).result_rows
    if not show:
        raise RuntimeError(f"Could not fetch DDL for {database}.{name}")
    return show[0][0]

def _views_to_json(client, views):
    result: Dict[str, List[str]] = {}
    errors: Dict[str, str] = {}

    for db, view_name, _engine in views:
        fq_view = f"{db}.{view_name}"
        try:
            ddl = fetch_view_ddl(client, db, view_name)
            tables = parse_view_tables(ddl, default_db=db)
            result[fq_view] = tables
        except Exception as e:
            # Keep going; store error for visibility
            errors[fq_view] = f"{type(e).__name__}: {e}"

    payload = {"view_dependencies": result, "errors": errors}
    return payload

# ----------------------------
# Main
# ----------------------------

def main() -> None:

    parser = argparse.ArgumentParser(
        description="Generates Mermaid .mmd diagram from ClickHouse VIEWS dependencies."
    )
    
    parser.add_argument(
        "output",
        help="Path to output .mmd file."
    )

    args = parser.parse_args()
        
    ci = get_conn_info_from_env()
    client = connect_ch(ci)

    print(f"Fetching views and tables from ClickHouse at {ci.host}:{ci.port}...")
    views = fetch_views(client, include_system=False)
    tables = {f"{db}.{name}" for db, name, _ in fetch_tables(client, include_system=False)}

    payload = _views_to_json(client, views)    
   
    print(f"Converting to Mermaid diagram...")
    mermaid_graph = json_to_mermaid(payload, tables, options=MermaidOptions(include_isolated_nodes=False))
   
    print(f"Writing Mermaid diagram to: {args.output}")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(mermaid_graph, encoding="utf-8", newline="\n")
   
if __name__ == "__main__":
    main()