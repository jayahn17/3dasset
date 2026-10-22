"""Tree structures, generators and step-by-step algorithm traces.

A trace is a list of Step dicts that a renderer turns into frames:
    {"i": 3, "action": "visit", "node": "n5", "note": "...", "current": [...],
     "visited": [...], "frontier": [...], "active_edges": [...], "labels": {...},
     "hidden": [...], "order": {...}, "state": {"stack": [...]}}
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .model import TreeSpec


@dataclass
class TNode:
    id: str
    label: str
    value: Optional[float] = None
    children: List["TNode"] = field(default_factory=list)
    edge_label: str = ""
    # binary trees keep explicit slots so a lone right child stays on the right
    left: Optional["TNode"] = None
    right: Optional["TNode"] = None
    kind: str = "general"

    def kids(self) -> List["TNode"]:
        if self.kind == "binary":
            return [c for c in (self.left, self.right) if c is not None]
        return self.children

    def walk(self):
        yield self
        for c in self.kids():
            yield from c.walk()

    def find(self, nid: str) -> Optional["TNode"]:
        for n in self.walk():
            if n.id == nid:
                return n
        return None

    def parent_of(self, nid: str) -> Optional["TNode"]:
        for n in self.walk():
            if any(c.id == nid for c in n.kids()):
                return n
        return None


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"n{self.n}"


def _from_dict(d: Any, kind: str, ids: _Ids, edge_label: str = "") -> TNode:
    if not isinstance(d, dict):
        d = {"value": d}
    label = str(d.get("label", d.get("value", d.get("name", ""))))
    node = TNode(id=str(d.get("id") or ids.next()), label=label, value=_num(d.get("value", d.get("label"))),
                 edge_label=str(d.get("edge", edge_label) or ""), kind=kind)
    if kind == "binary":
        if d.get("left") is not None:
            node.left = _from_dict(d["left"], kind, ids)
        if d.get("right") is not None:
            node.right = _from_dict(d["right"], kind, ids)
    elif kind == "decision":
        for br in d.get("branches", []) or []:
            child = br.get("child", br.get("node", br))
            node.children.append(_from_dict(child, kind, ids, edge_label=str(br.get("label", br.get("if", "")))))
        for c in d.get("children", []) or []:
            node.children.append(_from_dict(c, kind, ids))
    else:
        for c in d.get("children", []) or []:
            node.children.append(_from_dict(c, kind, ids))
    return node


def gen_fibonacci(n: int, ids: _Ids) -> TNode:
    node = TNode(id=ids.next(), label=f"fib({n})", value=None, kind="general")
    if n >= 2:
        node.children = [gen_fibonacci(n - 1, ids), gen_fibonacci(n - 2, ids)]
    return node


def gen_bst(keys: List[float], ids: _Ids) -> Optional[TNode]:
    root: Optional[TNode] = None
    for k in keys:
        new = TNode(id=ids.next(), label=str(int(k) if float(k).is_integer() else k), value=float(k), kind="binary")
        if root is None:
            root = new
            continue
        cur = root
        while True:
            if k < cur.value:
                if cur.left is None:
                    cur.left = new
                    break
                cur = cur.left
            else:
                if cur.right is None:
                    cur.right = new
                    break
                cur = cur.right
    return root


def gen_complete(depth: int, ids: _Ids, branching: int = 2, label: str = "") -> TNode:
    node = TNode(id=ids.next(), label=label or ids.n.__str__(), value=float(ids.n), kind="general")
    if depth > 0:
        node.children = [gen_complete(depth - 1, ids, branching) for _ in range(branching)]
    return node


def gen_minimax(depth: int, leaves: List[float], ids: _Ids, branching: int = 2) -> TNode:
    it = iter(leaves)

    def build(d: int, player: str) -> TNode:
        if d == 0:
            v = next(it, 0.0)
            return TNode(id=ids.next(), label=str(int(v) if float(v).is_integer() else v), value=float(v),
                         kind="general")
        n = TNode(id=ids.next(), label=player, value=None, kind="general")
        n.children = [build(d - 1, "MIN" if player == "MAX" else "MAX") for _ in range(branching)]
        return n

    return build(depth, "MAX")


def build_tree(spec: TreeSpec) -> TNode:
    ids = _Ids()
    kind = spec.kind
    if spec.generate:
        g = dict(spec.generate)
        name = g.get("name", g.get("kind", ""))
        if name in ("fibonacci", "fib"):
            root = gen_fibonacci(int(g.get("n", 5)), ids)
            root_kind = "general"
        elif name in ("bst", "bst_from_keys"):
            root = gen_bst([float(k) for k in g.get("keys", [50, 30, 70, 20, 40, 60, 80])], ids)
            root_kind = "binary"
        elif name in ("complete", "complete_binary"):
            root = gen_complete(int(g.get("depth", 3)), ids, int(g.get("branching", 2)))
            root_kind = "general"
        elif name in ("minimax", "game"):
            root = gen_minimax(int(g.get("depth", 2)), [float(x) for x in g.get("leaves", [3, 5, 2, 9])], ids,
                               int(g.get("branching", 2)))
            root_kind = "general"
        else:
            raise ValueError(f"unknown tree generator {name!r}")
        if root is None:
            raise ValueError("generator produced an empty tree")
        for n in root.walk():
            n.kind = root_kind
        return root
    if spec.root is None:
        raise ValueError("tree needs root or generate")
    return _from_dict(spec.root, kind if kind in ("binary", "decision") else "general", ids)


def is_bst(root: TNode) -> bool:
    def ok(n: Optional[TNode], lo: float, hi: float) -> bool:
        if n is None:
            return True
        if n.value is None or not (lo <= n.value <= hi):
            return False
        return ok(n.left, lo, n.value) and ok(n.right, n.value, hi)

    return ok(root, float("-inf"), float("inf"))


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------

def layout(root: TNode, x_gap: float = 56.0, y_gap: float = 76.0,
           widths: Optional[Dict[str, float]] = None, gap: float = 18.0) -> Dict[str, Tuple[float, float]]:
    """x from in-order position (binary) or leaf packing by node width (general); y from depth."""
    pos: Dict[str, Tuple[float, float]] = {}
    if root.kind == "binary":
        counter = [0]

        def inorder(n: Optional[TNode], depth: int) -> None:
            if n is None:
                return
            inorder(n.left, depth + 1)
            pos[n.id] = (counter[0] * x_gap, depth * y_gap)
            counter[0] += 1
            inorder(n.right, depth + 1)

        inorder(root, 0)
        return pos

    next_x = [0.0]
    widths = widths or {}

    def w(n: TNode) -> float:
        return widths.get(n.id, x_gap - gap)

    def place(n: TNode, depth: int) -> float:
        if not n.children:
            x = next_x[0] + w(n) / 2
            next_x[0] += w(n) + gap
        else:
            xs = [place(c, depth + 1) for c in n.children]
            x = (xs[0] + xs[-1]) / 2
            # a wide parent over a single narrow child must still clear its neighbours
            if next_x[0] < x + w(n) / 2 + gap:
                next_x[0] = x + w(n) / 2 + gap
        pos[n.id] = (x, depth * y_gap)
        return x

    place(root, 0)
    # keep internal nodes clear of each other on the same level
    by_level: Dict[int, List[str]] = {}
    for nid, (x, y) in pos.items():
        by_level.setdefault(int(round(y / y_gap)), []).append(nid)
    for level, ids in by_level.items():
        ids.sort(key=lambda i: pos[i][0])
        for a, b in zip(ids, ids[1:]):
            need = (widths.get(a, x_gap) + widths.get(b, x_gap)) / 2 + gap
            if pos[b][0] - pos[a][0] < need:
                shift = need - (pos[b][0] - pos[a][0])
                for nid in ids[ids.index(b):]:
                    pos[nid] = (pos[nid][0] + shift, pos[nid][1])
    return pos


# --------------------------------------------------------------------------
# traces
# --------------------------------------------------------------------------

class Recorder:
    def __init__(self, root: TNode):
        self.root = root
        self.steps: List[dict] = []
        self.visited: List[str] = []
        self.labels: Dict[str, str] = {}
        self.order: Dict[str, int] = {}
        self.hidden: List[str] = []

    def step(self, action: str, node: Optional[str], note: str, current: Optional[List[str]] = None,
             frontier: Optional[List[str]] = None, active_edges: Optional[List[Tuple[str, str]]] = None,
             state: Optional[Dict[str, Any]] = None, found: Optional[List[str]] = None) -> None:
        self.steps.append({
            "i": len(self.steps), "action": action, "node": node, "note": note,
            "current": list(current or ([node] if node else [])), "visited": list(self.visited),
            "frontier": list(frontier or []), "active_edges": [list(e) for e in (active_edges or [])],
            "labels": dict(self.labels), "hidden": list(self.hidden), "order": dict(self.order),
            "state": state or {}, "found": list(found or []),
        })

    def visit(self, nid: str) -> None:
        if nid not in self.visited:
            self.visited.append(nid)
            self.order[nid] = len(self.visited)


def _edge(parent: Optional[TNode], child: TNode) -> List[Tuple[str, str]]:
    return [(parent.id, child.id)] if parent else []


def trace_preorder(root: TNode, rec: Recorder, **_: Any) -> None:
    def go(n: TNode, parent: Optional[TNode], stack: List[str]) -> None:
        rec.visit(n.id)
        rec.step("visit", n.id, f"Visit {n.label} first, then its subtrees (root → left → right).",
                 active_edges=_edge(parent, n), state={"call stack": stack + [n.label]})
        for c in n.kids():
            go(c, n, stack + [n.label])

    go(root, None, [])
    rec.step("done", None, "Pre-order complete: every node was recorded before its children.")


def trace_inorder(root: TNode, rec: Recorder, **_: Any) -> None:
    def go(n: TNode, parent: Optional[TNode], stack: List[str]) -> None:
        if n.left:
            rec.step("descend", n.id, f"At {n.label}: go left before recording it.", active_edges=_edge(parent, n),
                     state={"call stack": stack + [n.label]})
            go(n.left, n, stack + [n.label])
        rec.visit(n.id)
        rec.step("visit", n.id, f"Left subtree done → record {n.label}.", active_edges=_edge(parent, n),
                 state={"call stack": stack + [n.label]})
        if n.right:
            go(n.right, n, stack + [n.label])

    go(root, None, [])
    rec.step("done", None, "In-order complete: for a BST this yields the keys in sorted order.")


def trace_postorder(root: TNode, rec: Recorder, **_: Any) -> None:
    def go(n: TNode, parent: Optional[TNode], stack: List[str]) -> None:
        kids = n.kids()
        if kids:
            rec.step("descend", n.id, f"At {n.label}: finish all children before recording it.",
                     active_edges=_edge(parent, n), state={"call stack": stack + [n.label]})
        for c in kids:
            go(c, n, stack + [n.label])
        rec.visit(n.id)
        rec.step("visit", n.id, f"Children of {n.label} done → record {n.label}.", active_edges=_edge(parent, n),
                 state={"call stack": stack + [n.label]})

    go(root, None, [])
    rec.step("done", None, "Post-order complete: parents come after their children (used to free or evaluate trees).")


def trace_bfs(root: TNode, rec: Recorder, **_: Any) -> None:
    queue: List[TNode] = [root]
    rec.step("enqueue", root.id, f"Start: put {root.label} in the queue.", frontier=[root.id],
             state={"queue": [root.label]})
    while queue:
        n = queue.pop(0)
        rec.visit(n.id)
        kids = n.kids()
        queue.extend(kids)
        rec.step("visit", n.id,
                 f"Dequeue {n.label}; enqueue its children {[c.label for c in kids] or 'none'}. "
                 "Level by level, left to right.",
                 frontier=[q.id for q in queue], active_edges=[(n.id, c.id) for c in kids],
                 state={"queue": [q.label for q in queue]})
    rec.step("done", None, "Breadth-first complete: nodes were visited in order of depth.")


def trace_dfs(root: TNode, rec: Recorder, **_: Any) -> None:
    stack: List[TNode] = [root]
    rec.step("push", root.id, f"Start: push {root.label}.", frontier=[root.id], state={"stack": [root.label]})
    while stack:
        n = stack.pop()
        rec.visit(n.id)
        kids = n.kids()
        for c in reversed(kids):
            stack.append(c)
        rec.step("visit", n.id,
                 f"Pop {n.label}; push children in reverse so {kids[0].label if kids else '—'} is explored next."
                 if kids else f"Pop {n.label}: a leaf, backtrack.",
                 frontier=[s.id for s in stack], active_edges=[(n.id, c.id) for c in kids],
                 state={"stack": [s.label for s in stack]})
    rec.step("done", None, "Depth-first complete: one branch is followed to the bottom before the next.")


def trace_bst_search(root: TNode, rec: Recorder, key: float = 0.0, **_: Any) -> None:
    key = float(key)
    n: Optional[TNode] = root
    parent: Optional[TNode] = None
    while n is not None:
        rec.visit(n.id)
        if key == n.value:
            rec.step("found", n.id, f"{n.label} == {key:g}: found after {len(rec.visited)} comparisons.",
                     active_edges=_edge(parent, n), found=[n.id], state={"compare": f"{key:g} == {n.label}"})
            rec.step("done", None, f"Search for {key:g} succeeded. Each comparison halves the remaining tree.")
            return
        side = "left" if key < n.value else "right"
        rec.step("compare", n.id, f"{key:g} {'<' if side == 'left' else '>'} {n.label} → go {side}.",
                 active_edges=_edge(parent, n), state={"compare": f"{key:g} vs {n.label}", "go": side})
        parent, n = n, (n.left if side == "left" else n.right)
    rec.step("done", None, f"Reached an empty slot: {key:g} is not in the tree ({len(rec.visited)} comparisons).")


def trace_bst_insert(root: TNode, rec: Recorder, keys: Optional[List[float]] = None, **_: Any) -> None:
    keys = [float(k) for k in (keys or [])]
    # pre-insert so the final tree (used for layout) contains the new nodes, then hide them
    ids = _Ids()
    ids.n = max((int(n.id[1:]) for n in root.walk() if n.id[1:].isdigit()), default=0)
    new_nodes: List[TNode] = []
    for k in keys:
        new = TNode(id=ids.next(), label=str(int(k) if k.is_integer() else k), value=k, kind="binary")
        new_nodes.append(new)
        cur = root
        while True:
            if k < cur.value:
                if cur.left is None:
                    cur.left = new
                    break
                cur = cur.left
            else:
                if cur.right is None:
                    cur.right = new
                    break
                cur = cur.right
    rec.hidden = [n.id for n in new_nodes]
    rec.step("start", None, f"Insert {[n.label for n in new_nodes]} one at a time, each by a search that ends at an empty slot.")
    for new in new_nodes:
        k = new.value
        rec.visited = []
        rec.order = {}
        cur: Optional[TNode] = root
        parent: Optional[TNode] = None
        while cur is not None and cur.id != new.id:
            rec.visit(cur.id)
            side = "left" if k < cur.value else "right"
            rec.step("compare", cur.id, f"{k:g} {'<' if side == 'left' else '≥'} {cur.label} → go {side}.",
                     active_edges=_edge(parent, cur), state={"inserting": new.label, "go": side})
            parent, cur = cur, (cur.left if side == "left" else cur.right)
        rec.hidden = [h for h in rec.hidden if h != new.id]
        rec.step("insert", new.id, f"Empty slot reached: attach {new.label} under {parent.label if parent else 'root'}.",
                 active_edges=_edge(parent, new), found=[new.id], state={"inserted": new.label})
    rec.visited = []
    rec.step("done", None, "All keys inserted; the BST ordering (left < node ≤ right) still holds everywhere.")


def trace_minimax(root: TNode, rec: Recorder, **_: Any) -> None:
    def go(n: TNode, maximizing: bool, parent: Optional[TNode]) -> float:
        if not n.children:
            rec.visit(n.id)
            rec.step("leaf", n.id, f"Leaf value {n.label}.", active_edges=_edge(parent, n))
            return float(n.value or 0)
        rec.step("descend", n.id, f"{'MAX' if maximizing else 'MIN'} node: evaluate children first.",
                 active_edges=_edge(parent, n))
        vals = [go(c, not maximizing, n) for c in n.children]
        best = max(vals) if maximizing else min(vals)
        rec.labels[n.id] = f"{best:g}"
        rec.visit(n.id)
        rec.step("backup", n.id, f"{'MAX' if maximizing else 'MIN'} of {[f'{v:g}' for v in vals]} = {best:g}; back it up.",
                 active_edges=_edge(parent, n))
        return best

    v = go(root, True, None)
    rec.step("done", None, f"Minimax value of the root is {v:g}: the best outcome MAX can force against a perfect MIN.")


def trace_height(root: TNode, rec: Recorder, **_: Any) -> None:
    def go(n: TNode, parent: Optional[TNode]) -> int:
        if not n.kids():
            rec.labels[n.id] = "h=0"
            rec.visit(n.id)
            rec.step("leaf", n.id, f"{n.label} is a leaf: height 0.", active_edges=_edge(parent, n))
            return 0
        h = 1 + max(go(c, n) for c in n.kids())
        rec.labels[n.id] = f"h={h}"
        rec.visit(n.id)
        rec.step("backup", n.id, f"height({n.label}) = 1 + max(children) = {h}.", active_edges=_edge(parent, n))
        return h

    h = go(root, None)
    rec.step("done", None, f"Tree height is {h}; a balanced tree keeps this near log₂(n).")


def trace_decision(root: TNode, rec: Recorder, path: Optional[List[str]] = None, **_: Any) -> None:
    """Follow a decision tree along the branch labels in ``path`` (or the first branch)."""
    n = root
    parent = None
    rec.visit(n.id)
    rec.step("ask", n.id, f"Start at the root question: {n.label}", active_edges=[])
    wanted = list(path or [])
    while n.children:
        nxt = None
        if wanted:
            want = str(wanted.pop(0)).strip().lower()
            nxt = next((c for c in n.children if c.edge_label.strip().lower() == want), None)
        if nxt is None:
            nxt = n.children[0]
        parent, n = n, nxt
        rec.visit(n.id)
        rec.step("branch", n.id, f"Take branch “{n.edge_label}” → {n.label}", active_edges=_edge(parent, n))
    rec.step("done", None, f"Reached leaf: {n.label}", found=[n.id])


ALGORITHMS = {
    "preorder": trace_preorder, "inorder": trace_inorder, "postorder": trace_postorder,
    "bfs": trace_bfs, "dfs": trace_dfs, "bst_search": trace_bst_search, "bst_insert": trace_bst_insert,
    "minimax": trace_minimax, "height": trace_height, "decision": trace_decision,
}

ALGORITHM_TITLES = {
    "preorder": "Pre-order traversal", "inorder": "In-order traversal", "postorder": "Post-order traversal",
    "bfs": "Breadth-first search", "dfs": "Depth-first search", "bst_search": "Binary search tree lookup",
    "bst_insert": "Binary search tree insertion", "minimax": "Minimax evaluation",
    "height": "Tree height (recursive)", "decision": "Walking a decision tree",
}


def trace(root: TNode, algorithm: Dict[str, Any], kind: str = "binary") -> List[dict]:
    """Run an algorithm on a copy of the tree and return its steps."""
    name = algorithm.get("name")
    if name not in ALGORITHMS:
        raise ValueError(f"unknown algorithm {name!r}")
    work = copy.deepcopy(root)
    rec = Recorder(work)
    params = {k: v for k, v in algorithm.items() if k != "name"}
    ALGORITHMS[name](work, rec, **params)
    rec.steps.insert(0, {"i": -1, "action": "ready", "node": None, "note": "Ready.", "current": [], "visited": [],
                         "frontier": [], "active_edges": [], "labels": {}, "hidden": list(rec.steps[0]["hidden"]) if rec.steps else [],
                         "order": {}, "state": {}, "found": []})
    for i, s in enumerate(rec.steps):
        s["i"] = i
    return rec.steps


def final_tree(root: TNode, algorithm: Dict[str, Any]) -> TNode:
    """Tree after the algorithm ran (matters for insertion); used for layout."""
    work = copy.deepcopy(root)
    rec = Recorder(work)
    name = algorithm.get("name")
    params = {k: v for k, v in algorithm.items() if k != "name"}
    ALGORITHMS[name](work, rec, **params)
    return work
