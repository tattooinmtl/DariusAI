"""BrainGraph - the in-memory graph of the DariusAI brain.

Provides:
- Force-directed layout computation
- Graph traversal algorithms (shortest path, neighbors, centrality)
- Frontend payload serialization
- Tool node registration
"""

from __future__ import annotations

import json
import math
from typing import Any

from .lite import LiteMultiDiGraph


COORDINATOR_ID = "brain-coordinator"


class BrainGraph:
    def __init__(self) -> None:
        self.graph = LiteMultiDiGraph()   # was networkx; see lite.py

    def load_from_rows(
        self,
        node_rows: list[dict[str, Any]],
        edge_rows: list[tuple[str, str, str]],
    ) -> None:
        self.graph.clear()
        self.graph.add_node(
            COORDINATOR_ID,
            category="brain",
            label="Central Brain",
            tags=[],
            usage_count=0,
            created_at="",
            updated_at="",
            source_count=0,
        )
        for row in node_rows:
            self.graph.add_node(
                row["id"],
                category=row["category"],
                label=row["label"],
                file_path=row["file_path"],
                tags=json.loads(row["tags"]) if isinstance(row["tags"], str) else row.get("tags", []),
                source_count=row.get("source_count", 0),
                usage_count=row.get("usage_count", 0),
                created_at=row.get("created_at", ""),
                updated_at=row.get("updated_at", ""),
            )

        has_parent: set[str] = set()
        for source, target, kind in edge_rows:
            if source in self.graph and target in self.graph:
                self.graph.add_edge(source, target, kind=kind)
                has_parent.add(source)

        for node_id in list(self.graph.nodes):
            if node_id != COORDINATOR_ID and node_id not in has_parent:
                self.graph.add_edge(COORDINATOR_ID, node_id, kind="index")

    def add_tool_nodes(self, tools: list[dict[str, str]]) -> None:
        for tool in tools:
            tool_id = f"tool-{tool['name']}"
            if tool_id not in self.graph:
                self.graph.add_node(
                    tool_id,
                    category="tool",
                    label=tool["name"],
                    tags=["tool"],
                    usage_count=0,
                    created_at="",
                    updated_at="",
                    source_count=0,
                )

    def layout(self, seed: int | None = None) -> dict[str, tuple[float, float]]:
        if len(self.graph.nodes) == 0:
            return {}
        # A plain circle: nothing in the app uses a server-side layout any
        # more (the page lays the graph out itself), so this only has to be
        # deterministic and in [-1, 1].
        ids = sorted(self.graph.nodes)
        n = len(ids)
        return {node: (math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n))
                for i, node in enumerate(ids)}

    def shortest_path(self, source: str, target: str) -> list[str]:
        if source not in self.graph or target not in self.graph:
            return []
        return self.graph.shortest_path(source, target)

    def neighbors(self, node_id: str) -> list[str]:
        if node_id not in self.graph:
            return []
        return list(self.graph.neighbors(node_id))

    def children_of(self, node_id: str) -> list[str]:
        """What hangs beneath a node, following the graph's own two directions.

        The tree is not built from one edge direction. The coordinator points
        *down* at each branch with an `index` edge, while a skill points *up* at
        its branch with a `related` edge — because a skill declares its own
        parent at import time, and the coordinator adopts whatever is left over.

        So descending means reading `index` edges forward and `related` edges
        backward. Taking plain successors instead returns the branches at the
        top and nothing at all under them, which looks like an empty library.

        `superseded_by` edges are deliberately not traversed: a replacement is
        not a child, and following them would file v2 underneath v1.
        """
        if node_id not in self.graph:
            return []
        children = {
            target for _, target, data in self.graph.out_edges(node_id, data=True)
            if data.get("kind") == "index"
        }
        children |= {
            source for source, _, data in self.graph.in_edges(node_id, data=True)
            if data.get("kind") == "related"
        }
        children.discard(node_id)
        return sorted(children)

    def superseded_by(self, node_id: str) -> str | None:
        """The node that replaces this one, if any."""
        if node_id not in self.graph:
            return None
        for _, target, data in self.graph.out_edges(node_id, data=True):
            if data.get("kind") == "superseded_by":
                return target
        return None

    def lineage(self, node_id: str) -> list[str]:
        """The route a charge takes from the coordinator down to `node_id`.

        The inverse of children_of: climb `related` edges up to the branch,
        then stop where the coordinator's `index` edge points in. The viz walks
        these hops in order, so a skill under a group lights coordinator →
        group → skill instead of a straight line through empty space.
        """
        if node_id not in self.graph:
            return []
        path = [node_id]
        current = node_id
        while current != COORDINATOR_ID:
            if any(d.get("kind") == "index"
                   for d in (self.graph.get_edge_data(COORDINATOR_ID, current) or {}).values()):
                path.append(COORDINATOR_ID)
                break
            parent = next(
                (t for _, t, d in self.graph.out_edges(current, data=True)
                 if d.get("kind") == "related" and t not in path),
                None,
            )
            if parent is None:
                path.append(COORDINATOR_ID)
                break
            path.append(parent)
            current = parent
        path.reverse()
        return path

    def degree_centrality(self) -> dict[str, float]:
        n = len(self.graph)
        if n <= 1:
            return {node: 1.0 for node in self.graph.nodes}
        return {node: self.graph.degree(node) / (n - 1) for node in self.graph.nodes}

    def betweenness_centrality(self) -> dict[str, float]:
        return self.graph.betweenness()

    def to_payload(self) -> dict[str, Any]:
        nodes = [{"id": n, **d} for n, d in self.graph.nodes(data=True)]
        edges = [
            {"source": u, "target": v, "kind": d.get("kind", "related")}
            for u, v, d in self.graph.edges(data=True)
        ]
        return {
            "coordinatorId": COORDINATOR_ID,
            "nodes": nodes,
            "edges": edges,
            "counts": {"nodes": len(nodes), "edges": len(edges)},
        }
