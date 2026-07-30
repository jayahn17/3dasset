"""Capture-agnostic preprocess: iPad / photos / video → curated dataset pack."""

from .normalize import (
    build_dataset_pack,
    find_session_dir,
    load_pack,
    pack_frame_paths,
)

__all__ = [
    "build_dataset_pack",
    "find_session_dir",
    "load_pack",
    "pack_frame_paths",
]
