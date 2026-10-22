"""Figure renderers.  Each returns an SVG/HTML string; the pipeline saves it."""
from .variable_graph import render_variable_graph  # noqa: F401
from .influence_matrix import render_influence_matrix  # noqa: F401
from .tree_figure import render_tree_static, render_tree_steps_html  # noqa: F401
from .timeline_figure import render_timeline  # noqa: F401
from .scene3d import render_scene3d  # noqa: F401
from .spatial import render_spatial, resolve_live  # noqa: F401
from .figure_page import render_page  # noqa: F401
