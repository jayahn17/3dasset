"""learnfig: textbook-figure generation for any learning topic.

Pipeline:  topic spec (YAML)  ->  checks  ->  analysis  ->  figures  ->  page
Agents:    reader (source -> spec), writer (spec -> figures + captions),
           checker (science / consistency review), orchestrator (the loop).
"""

__version__ = "0.1.0"

from .load import load_topic, topic_from_dict, topic_to_dict  # noqa: F401
from .model import Topic, Variable, Relation, Event  # noqa: F401
