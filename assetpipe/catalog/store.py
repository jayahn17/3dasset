"""Asset catalog — the digital-twin backend ('Life Twin Control Center').

A single SQLite file is the source of truth for every digitalized object:
what it is, its mesh + URDF, when/where it was captured, and its world
pose. This is deliberately a thin, portable store — point the real control
center's API at the same schema, or sync rows up to a cloud DB.

The table answers the product question directly: "what items did I have,
and where were they?" -> query by label / category / location / time.
"""

from __future__ import annotations

import json
import os
import sqlite3

from ..types import Asset

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id      TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    category      TEXT NOT NULL,
    mesh_path     TEXT NOT NULL,
    urdf_path     TEXT,
    dim_w         REAL, dim_h REAL, dim_d REAL,
    created_at    REAL,
    source        TEXT,
    location      TEXT,
    world_pose    TEXT,
    thumbnail_path TEXT,
    tags          TEXT,
    extra         TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_label    ON assets(label);
CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);
CREATE INDEX IF NOT EXISTS idx_assets_location ON assets(location);
"""


class AssetCatalog:
    def __init__(self, db_path: str = "twin.db") -> None:
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def add(self, a: Asset) -> None:
        w, h, d = a.dimensions_m
        self.conn.execute(
            """INSERT OR REPLACE INTO assets VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                a.asset_id, a.label, a.category, a.mesh_path, a.urdf_path,
                w, h, d, a.created_at, a.source, a.location,
                json.dumps(a.world_pose), a.thumbnail_path,
                json.dumps(a.tags), json.dumps(a.extra),
            ),
        )
        self.conn.commit()

    def all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM assets ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def find(self, label: str | None = None, category: str | None = None,
             location: str | None = None) -> list[dict]:
        clauses, params = [], []
        if label:
            clauses.append("label LIKE ?"); params.append(f"%{label}%")
        if category:
            clauses.append("category = ?"); params.append(category)
        if location:
            clauses.append("location LIKE ?"); params.append(f"%{location}%")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM assets{where} ORDER BY created_at DESC", params
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
