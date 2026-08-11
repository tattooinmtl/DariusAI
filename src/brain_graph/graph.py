"""BrainGraph - NetworkX graph wrapper for the DariusAI brain.

Provides:
- Force-directed layout computation
- Graph traversal algorithms (shortest path, neighbors, centrality)
- Frontend payload serialization
- Tool node registration
"""

from __future__ import annotations

import json
from typing import Any

import networkx as nx


COORDINATOR_ID = "brain-coordinator"


class BrainGraph:
    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()

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
        pos = nx.spring_layout(
            self.graph,
            k=2.5,
            iterations=50,
            seed=seed,
        )
        return {node: (float(x), float(y)) for node, (x, y) in pos.items()}

    def shortest_path(self, source: str, target: str) -> list[str]:
        if source not in self.graph or target not in self.graph:
            return []
        try:
            return nx.shortest_path(self.graph, source, target)
        except nx.NetworkXNoPath:
            return []

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

    def degree_centrality(self) -> dict[str, float]:
        return nx.degree_centrality(self.graph)

    def betweenness_centrality(self) -> dict[str, float]:
        return nx.betweenness_centrality(self.graph)

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
