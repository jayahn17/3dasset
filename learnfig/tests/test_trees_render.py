import xml.etree.ElementTree as ET

import pytest

from learnfig import load
from learnfig.model import TreeSpec
from learnfig.pipeline import build
from learnfig.trees import build_tree, is_bst, layout, trace
from tests.conftest import EXAMPLES


def _bst():
    return build_tree(TreeSpec(kind="binary", generate={"name": "bst", "keys": [50, 30, 70, 20, 40, 60, 80]}))


def test_inorder_is_sorted():
    root = _bst()
    assert is_bst(root)
    steps = trace(root, {"name": "inorder"}, "binary")
    order = steps[-1]["order"] if steps[-1]["order"] else steps[-2]["order"]
    by_rank = sorted(order, key=order.get)
    labels = [root.find(n).label for n in by_rank]
    assert labels == ["20", "30", "40", "50", "60", "70", "80"]


def test_bst_search_and_insert():
    root = _bst()
    steps = trace(root, {"name": "bst_search", "key": 40}, "binary")
    assert steps[-2]["action"] == "found" and len(steps[-2]["visited"]) == 3
    steps = trace(root, {"name": "bst_insert", "keys": [35]}, "binary")
    assert steps[1]["hidden"]  # new node hidden until inserted
    assert steps[-2]["action"] == "insert" and not steps[-2]["hidden"]


def test_bfs_levels_and_layout():
    root = _bst()
    steps = trace(root, {"name": "bfs"}, "binary")
    visited = [s["node"] for s in steps if s["action"] == "visit"]
    assert [root.find(n).label for n in visited] == ["50", "30", "70", "20", "40", "60", "80"]
    pos = layout(root)
    xs = sorted(pos[n.id][0] for n in root.walk())
    assert len(set(xs)) == 7  # in-order x positions are distinct


def test_minimax_and_height():
    root = build_tree(TreeSpec(kind="general", generate={"name": "minimax", "depth": 2, "leaves": [3, 5, 2, 9]}))
    steps = trace(root, {"name": "minimax"}, "general")
    assert steps[-1]["note"].startswith("Minimax value of the root is 3")
    steps = trace(root, {"name": "height"}, "general")
    assert "height is 2" in steps[-1]["note"].lower()


def _assert_svg(path):
    tree = ET.parse(path)
    assert tree.getroot().tag.endswith("svg")


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.yaml")), ids=lambda p: p.stem)
def test_build_examples(tmp_path, path):
    topic = load.load_topic(path)
    res = build(topic, tmp_path / topic.id)
    assert res.index.exists() and res.figures
    for f in res.figures:
        if str(f["file"]).endswith(".svg"):
            _assert_svg(f["file"])
    html = res.index.read_text()
    assert "Figure" in html and topic.title in html
    if topic.tree:
        assert (tmp_path / topic.id / "tree_steps.html").exists()
    wants_3d = any(f.type == "scene3d" for f in topic.figures) or (not topic.figures and (topic.dynamics or topic.cascade.shock))
    if wants_3d:
        assert (tmp_path / topic.id / "scene3d.html").exists()
