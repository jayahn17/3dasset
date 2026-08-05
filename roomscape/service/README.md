# service/

The system of record, running on the 4080 box.

- **ingest API** — authenticated, resumable session upload; validate against the
  capture spec; privacy scrub (person/face blur) *before* anything persists
- **job queue** — reconstruction runs on arrival, unattended
- **registry** — SQLite → Postgres later; sessions, runs, scenes, artifacts
- **blob store** — content-addressed; local dir → S3/MinIO later
- **retention + delete** — raw frames expire; `forget` really removes

Seeds already in the repo: `services/capture_worker.py`,
`scripts/watch_ipad_captures.sh`, `tools/watch_inbox.py`.

Schema and CLI shape: [../docs/DATA_MODEL.md](../docs/DATA_MODEL.md)
