"""Graph utilities for variable-interaction diagrams.

Layered layout (Sugiyama-lite): break cycles with DFS back edges, assign
layers by longest path, order each layer with a barycenter sweep.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Set, Tuple

Edge = Tuple[str, str]


class Graph:
    def __init__(self, nodes: Iterable[str], edges: Iterable[Edge]):
        self.nodes: List[str] = list(dict.fromkeys(nodes))
        self.edges: List[Edge] = []
        self.out: Dict[str, List[str]] = defaultdict(list)
        self.inc: Dict[str, List[str]] = defaultdict(list)
        seen: Set[Edge] = set()
        for s, t in edges:
            if s not in self.nodes or t not in self.nodes or (s, t) in seen:
                continue
            seen.add((s, t))
            self.edges.append((s, t))
            self.out[s].append(t)
            self.inc[t].append(s)

    # -- cycle handling ----------------------------------------------------
    def back_edges(self) -> Set[Edge]:
        """Edges whose removal makes the graph acyclic (DFS back edges)."""
        color: Dict[str, int] = {n: 0 for n in self.nodes}
        back: Set[Edge] = set()

        def visit(u: str) -> None:
            color[u] = 1
            for v in self.out[u]:
                if color[v] == 0:
                    visit(v)
                elif color[v] == 1:
                    back.add((u, v))
            color[u] = 2

        for n in self.nodes:
            if color[n] == 0:
                visit(n)
        return back

    def simple_cycles(self, max_len: int = 10, limit: int = 60) -> List[List[str]]:
        """Enumerate simple cycles (small graphs only), deduplicated by rotation."""
        found: List[List[str]] = []
        seen: Set[Tuple[str, ...]] = set()
        index = {n: i for i, n in enumerate(self.nodes)}

        def dfs(start: str, u: str, path: List[str]) -> None:
            if len(found) >= limit:
                return
            for v in self.out[u]:
                if v == start:
                    cyc = list(path)
                    k = cyc.index(min(cyc, key=lambda x: index[x]))
                    canon = tuple(cyc[k:] + cyc[:k])
                    if canon not in seen:
                        seen.add(canon)
                        found.append(list(canon))
                elif v not in path and index[v] > index[start] and len(path) < max_len:
                    dfs(start, v, path + [v])

        for n in self.nodes:
            dfs(n, n, [n])
        return found

    # -- layering ------------------------------------------------------------
    def layers(self) -> Tuple[List[List[str]], Set[Edge]]:
        back = self.back_edges()
        fwd = [(s, t) for s, t in self.edges if (s, t) not in back and s != t]
        out: Dict[str, List[str]] = defaultdict(list)
        indeg: Dict[str, int] = {n: 0 for n in self.nodes}
        for s, t in fwd:
            out[s].append(t)
            indeg[t] += 1
        layer: Dict[str, int] = {n: 0 for n in self.nodes}
        queue = [n for n in self.nodes if indeg[n] == 0]
        order: List[str] = []
        while queue:
            u = queue.pop(0)
            order.append(u)
            for v in out[u]:
                layer[v] = max(layer[v], layer[u] + 1)
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)
        # pull sinks right next to their last parent (compact long chains)
        n_layers = max(layer.values(), default=0) + 1
        layered: List[List[str]] = [[] for _ in range(n_layers)]
        for n in self.nodes:
            layered[layer[n]].append(n)
        layered = self._barycenter(layered, fwd)
        return layered, back

    def _barycenter(self, layered: List[List[str]], fwd: List[Edge], sweeps: int = 4) -> List[List[str]]:
        inc: Dict[str, List[str]] = defaultdict(list)
        out: Dict[str, List[str]] = defaultdict(list)
        for s, t in fwd:
            inc[t].append(s)
            out[s].append(t)
        pos: Dict[str, float] = {}
        for L in layered:
            for i, n in enumerate(L):
                pos[n] = i
        for sweep in range(sweeps):
            rng = range(1, len(layered)) if sweep % 2 == 0 else range(len(layered) - 2, -1, -1)
            nbrs = inc if sweep % 2 == 0 else out
            for li in rng:
                L = layered[li]

                def bary(n: str) -> float:
                    ns = [pos[m] for m in nbrs[n] if m in pos]
                    return sum(ns) / len(ns) if ns else pos[n]

                L.sort(key=lambda n: (bary(n), pos[n]))
                for i, n in enumerate(L):
                    pos[n] = i
        return layered

    def reachable(self, start: str) -> Set[str]:
        seen: Set[str] = set()
        stack = [start]
        while stack:
            u = stack.pop()
            for v in self.out[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        return seen


def loop_polarity(cycle: List[str], sign_of: Dict[Edge, str]) -> str:
    """Reinforcing (even number of negative links) or balancing (odd)."""
    neg = 0
    for i, s in enumerate(cycle):
        t = cycle[(i + 1) % len(cycle)]
        sign = sign_of.get((s, t), "?")
        if sign == "?":
            return "unknown"
        if sign == "-":
            neg += 1
    return "balancing" if neg % 2 else "reinforcing"
