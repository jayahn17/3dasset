"""Multi-agent loop: reader (sources → spec), checker (science review),
writer (captions and learning notes), orchestrated as read → check → revise
→ write → render.  The deterministic checker in ``learnfig.checks`` always
runs too; the model reviews what rules cannot (physical plausibility,
missing variables, misleading simplifications).
"""
from .llm import LLM, ClaudeLLM, AgentError  # noqa: F401
from .orchestrator import run_pipeline  # noqa: F401
