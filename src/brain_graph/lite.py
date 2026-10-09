"""A small directed multigraph with the slice of networkx's API the brain uses.

networkx was imported on every launch (about half a second and ~20 MB) to
hold a few hundred nodes and links; none of its algorithms are on the app's
path. This keeps the same calls — `nodes`, `nodes(data=True)`, `nodes[id]`,
`add_node`, `add_edge`, `out_edges`, `in_edges`, `edges(data=True)`,
`get_edge_data`, `neighbors`, `in`, `len` — so the rest of the code didn't
have to change.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterator


class _NodeView:
    def __init__(self, g: "LiteMultiDiGraph") -> None:
        self._g = g

    def __call__(self, data: bool = False):
        if data:
            return list(self._g._nodes.items())
        return list(self._g._nodes)

    def __getitem__(self, node: str) -> dict[str, Any]:
        return self._g._nodes[node]

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._g._nodes))

    def __len__(self) -> int:
        return len(self._g._nodes)

    def __contains__(self, node: object) -> bool:
        return node in self._g._nodes


class LiteMultiDiGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._out: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._in: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    # ---- structure ------------------------------------------------------
    def clear(self) -> None:
        self._nodes.clear(); self._out.clear(); self._in.clear()

    def add_node(self, node: str, **attrs: Any) -> None:
        if node in self._nodes:
            self._nodes[node].update(attrs)
        else:
            self._nodes[node] = dict(attrs)
            self._out[node] = []
            self._in[node] = []

    def add_edge(self, u: str, v: str, **attrs: Any) -> None:
        for n in (u, v):
            if n not in self._nodes:
                self.add_node(n)
        data = dict(attrs)
        self._out[u].append((v, data))
        self._in[v].append((u, data))

    @property
    def nodes(self) -> _NodeView:
        return _NodeView(self)

    def __contains__(self, node: object) -> bool:
        return node in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._nodes))

    # ---- edges ------------------------------------------------------------
    def out_edges(self, node: str, data: bool = False) -> list:
        return [(node, v, d) if data else (node, v) for v, d in self._out.get(node, [])]

    def in_edges(self, node: str, data: bool = False) -> list:
        return [(u, node, d) if data else (u, node) for u, d in self._in.get(node, [])]

    def edges(self, data: bool = False) -> list:
        out = []
        for u, lst in self._out.items():
            for v, d in lst:
                out.append((u, v, d) if data else (u, v))
        return out

    def get_edge_data(self, u: str, v: str) -> dict[int, dict[str, Any]] | None:
        found = {i: d for i, (t, d) in enumerate(t for t in self._out.get(u, []) if t[0] == v)}
        return found or None

    def successors(self, node: str) -> list[str]:
        seen: list[str] = []
        for v, _ in self._out.get(node, []):
            if v not in seen:
                seen.append(v)
        return seen

    neighbors = successors

    def degree(self, node: str) -> int:
        return len(self._out.get(node, [])) + len(self._in.get(node, []))

    # ---- the few algorithms BrainGraph still offers ---------------------
    def shortest_path(self, source: str, target: str) -> list[str]:
        """Breadth-first, following link direction (as networkx does on a
        directed graph). [] when there is no path."""
        if source not in self._nodes or target not in self._nodes:
            return []
        prev: dict[str, str | None] = {source: None}
        queue = deque([source])
        while queue:
            n = queue.popleft()
            if n == target:
                path = [n]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                return path[::-1]
            for v in self.successors(n):
                if v not in prev:
                    prev[v] = n
                    queue.append(v)
        return []

    def betweenness(self) -> dict[str, float]:
        """Brandes' algorithm, unweighted and directed, normalised the way
        networkx does for directed graphs."""
        nodes = list(self._nodes)
        cb = dict.fromkeys(nodes, 0.0)
        for s in nodes:
            stack, pred = [], {w: [] for w in nodes}
            sigma = dict.fromkeys(nodes, 0.0); sigma[s] = 1.0
            dist = dict.fromkeys(nodes, -1); dist[s] = 0
            q = deque([s])
            while q:
                v = q.popleft(); stack.append(v)
                for w in self.successors(v):
                    if dist[w] < 0:
                        dist[w] = dist[v] + 1; q.append(w)
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]; pred[w].append(v)
            delta = dict.fromkeys(nodes, 0.0)
            while stack:
                w = stack.pop()
                for v in pred[w]:
                    delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
                if w != s:
                    cb[w] += delta[w]
        n = len(nodes)
        if n > 2:
            scale = 1.0 / ((n - 1) * (n - 2))
            cb = {k: v * scale for k, v in cb.items()}
        return cb
