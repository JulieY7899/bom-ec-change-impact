"""Indexed snapshot traversal for an anchor and one effective date."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from sqlite3 import Connection, Row

from .config import MAX_ENUMERATED_PATHS

RawPath = tuple[str, list[str], list[Row | None]]


@dataclass
class BomTraversal:
    candidate_tops: list[str]
    direct_edges: list[Row]
    paths: list[RawPath]
    cycles: list[list[str]]
    incomplete_branches: int
    truncated: bool
    invalid_anchor_dates: int
    all_anchor_uses: int


def traverse(connection: Connection, anchor: str, effective_date: str, direction: str) -> BomTraversal:
    upstream = direction == "upstream"
    anchor_column, next_column, index = (
        ("component_key", "parent_key", "idx_bom_component") if upstream else
        ("parent_key", "component_key", "idx_bom_parent")
    )
    in_effect = """edge.date_valid=1
        AND (edge.valid_from IS NULL OR edge.valid_from<=:effective)
        AND (edge.valid_to IS NULL OR edge.valid_to>=:effective)"""
    parameters = {"anchor": anchor, "effective": effective_date}
    stop_at_top = "AND reached.node<>reached.top_key" if upstream else ""
    # UNION visits each (top, node) once even in a cycle. Path enumeration below
    # still sees each matching edge, including parallel rows from distinct sources.
    rows = connection.execute(f"""
        WITH RECURSIVE reached(top_key, node) AS (
            SELECT edge.top_key, :anchor
            FROM bom_edges AS edge INDEXED BY {index}
            WHERE edge.{anchor_column}=:anchor AND {in_effect}
            UNION
            SELECT edge.top_key, edge.{next_column}
            FROM reached
            JOIN bom_edges AS edge INDEXED BY {index}
              ON edge.{anchor_column}=reached.node AND edge.top_key=reached.top_key
            WHERE {in_effect} {stop_at_top}
        )
        SELECT edge.*
        FROM reached
        JOIN bom_edges AS edge INDEXED BY {index}
          ON edge.{anchor_column}=reached.node AND edge.top_key=reached.top_key
        WHERE {in_effect}
        ORDER BY edge.top_key, edge.component_key, edge.parent_key, edge.edge_id
    """, parameters)
    adjacency: dict[tuple[str, str], list[Row]] = defaultdict(list)
    tops = set()
    for row in rows:
        adjacency[(row["top_key"], row[anchor_column])].append(row)
        if row[anchor_column] == anchor:
            tops.add(row["top_key"])
    if anchor not in tops and connection.execute(f"""
        SELECT 1 FROM bom_edges AS edge
        WHERE edge.top_key=:anchor AND {in_effect} LIMIT 1
    """, parameters).fetchone():
        tops.add(anchor)
    candidate_tops = sorted(tops)
    all_anchor_uses, invalid_anchor_dates = connection.execute(f"""
        SELECT COUNT(*), COUNT(CASE WHEN date_valid=0 THEN 1 END)
        FROM bom_edges WHERE {anchor_column}=:anchor OR top_key=:anchor
    """, parameters).fetchone()

    raw_paths: list[RawPath] = []
    cycles: list[list[str]] = []
    incomplete_branches = 0
    truncated = False

    def walk_upstream(
        top_key: str,
        current: str,
        nodes: list[str],
        edges: list[Row],
        visited: set[str],
    ) -> None:
        nonlocal incomplete_branches, truncated
        if len(raw_paths) >= MAX_ENUMERATED_PATHS:
            truncated = True
            return
        if current == top_key:
            raw_paths.append((top_key, list(nodes), list(edges)))
            return
        parents = adjacency.get((top_key, current), [])
        if not parents:
            incomplete_branches += 1
            return
        for edge in parents:
            parent = edge["parent_key"]
            if parent in visited:
                cycles.append(nodes + [parent])
                continue
            walk_upstream(
                top_key,
                parent,
                nodes + [parent],
                edges + [edge],
                visited | {parent},
            )

    def walk_downstream(
        top_key: str,
        current: str,
        nodes: list[str],
        edges: list[Row],
        visited: set[str],
    ) -> None:
        nonlocal truncated
        if len(raw_paths) >= MAX_ENUMERATED_PATHS:
            truncated = True
            return
        children = adjacency.get((top_key, current), [])
        if not children:
            if edges:
                raw_paths.append((top_key, list(nodes), list(edges)))
            return
        for edge in children:
            child = edge["component_key"]
            if child in visited:
                cycles.append(nodes + [child])
                continue
            walk_downstream(
                top_key,
                child,
                nodes + [child],
                edges + [edge],
                visited | {child},
            )

    walk = walk_upstream if upstream else walk_downstream
    for top_key in candidate_tops:
        walk(top_key, anchor, [anchor], [], {anchor})

    direct_edges = [edge for top in candidate_tops for edge in adjacency.get((top, anchor), [])]
    return BomTraversal(
        candidate_tops, direct_edges, raw_paths, cycles, incomplete_branches,
        truncated, invalid_anchor_dates, all_anchor_uses,
    )
