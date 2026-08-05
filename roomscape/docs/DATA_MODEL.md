# Data model — "record all the data in our system"

The point of building our own Hyperscape is that the captures are ours. That is
only true if there is a system of record: something that knows every session
ever captured, every scene ever produced, what produced it, and who is allowed
to see it. Otherwise we have a folder of zips.

SQLite + a local content-addressed blob store to start. The schema is what
matters; swapping to Postgres + S3 later is a migration, not a redesign.

---

## Tables

```sql
-- a physical device that captures
CREATE TABLE device (
  id            TEXT PRIMARY KEY,        -- dev_01J...
  kind          TEXT NOT NULL,           -- 'ipad' | 'quest' | 'iphone'
  model         TEXT,
  os            TEXT,
  first_seen    TEXT NOT NULL,
  last_seen     TEXT NOT NULL
);

-- one sweep of one space
CREATE TABLE capture_session (
  id            TEXT PRIMARY KEY,        -- cap_01J...
  device_id     TEXT NOT NULL REFERENCES device(id),
  captured_at   TEXT NOT NULL,
  mode          TEXT NOT NULL,           -- 'room' | 'object' | 'hybrid'
  label         TEXT,                    -- 'living room'
  space_type    TEXT,                    -- 'private_residence' | 'office' | 'public'
  n_frames      INTEGER NOT NULL,
  n_depth       INTEGER NOT NULL,
  coverage      REAL,
  bytes_raw     INTEGER NOT NULL,
  manifest_hash TEXT NOT NULL,           -- sha256 of manifest.json
  privacy_state TEXT NOT NULL,           -- 'raw' | 'scrubbed' | 'raw_deleted'
  retention_due TEXT,                    -- when raw frames get purged
  UNIQUE(device_id, captured_at)
);

-- one attempt to turn a session into a scene
CREATE TABLE training_run (
  id            TEXT PRIMARY KEY,        -- run_01J...
  session_id    TEXT NOT NULL REFERENCES capture_session(id),
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  status        TEXT NOT NULL,           -- 'queued'|'running'|'ok'|'failed'
  git_commit    TEXT NOT NULL,
  config_json   TEXT NOT NULL,           -- the full resolved config, verbatim
  trainer       TEXT NOT NULL,           -- 'gsplat-1.5.x' etc
  gpu           TEXT,
  iterations    INTEGER,
  n_gaussians   INTEGER,
  psnr          REAL,
  wall_seconds  INTEGER,
  error         TEXT
);

-- a viewable place
CREATE TABLE scene (
  id            TEXT PRIMARY KEY,        -- scn_01J...
  run_id        TEXT NOT NULL REFERENCES training_run(id),
  session_id    TEXT NOT NULL REFERENCES capture_session(id),
  name          TEXT NOT NULL,
  version       INTEGER NOT NULL,        -- re-trains bump this, same lineage
  published_at  TEXT,
  bounds_json   TEXT NOT NULL,
  spawn_json    TEXT NOT NULL,
  n_gaussians   INTEGER NOT NULL,
  bytes         INTEGER NOT NULL,
  visibility    TEXT NOT NULL DEFAULT 'private'   -- 'private'|'link'|'shared'
);

-- every file we keep, content-addressed
CREATE TABLE artifact (
  sha256        TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,           -- 'session_zip'|'splat'|'collision'|'preview'|'lod'
  bytes         INTEGER NOT NULL,
  path          TEXT NOT NULL,           -- blob store key
  created_at    TEXT NOT NULL
);
CREATE TABLE scene_artifact (
  scene_id TEXT REFERENCES scene(id),
  sha256   TEXT REFERENCES artifact(sha256),
  role     TEXT NOT NULL,                -- 'splat_lod0' | 'collision' | ...
  PRIMARY KEY (scene_id, role)
);

-- optional, but this is what makes the catalog feel alive
CREATE TABLE view_event (
  id         INTEGER PRIMARY KEY,
  scene_id   TEXT NOT NULL REFERENCES scene(id),
  at         TEXT NOT NULL,
  client     TEXT,                       -- 'webxr-quest' | 'native' | 'desktop'
  seconds    INTEGER,
  p99_frame_ms REAL                      -- real-world perf telemetry, from real sessions
);
```

---

## Two properties worth designing for on day one

**Reproducibility.** `training_run` stores the commit, the full resolved
config, and the input hash. When the trainer improves — and it will, several
times — re-running every scene is a loop over rows, not an archaeology project.
This is also what makes an A/B ("does depth supervision actually help?") a
measurement instead of an argument.

**Lineage.** `scene.version` means a re-trained room replaces its predecessor in
the UI while the old artifacts stay addressable. Never mutate a published
scene's artifacts in place; the viewer caches aggressively and content-addressing
is what keeps that safe.

---

## Privacy — the part that is actually load-bearing

These are photographs of the inside of someone's home, at 2–4 Hz, with metric
geometry. Treated casually, that is the most sensitive dataset either of us has
ever built. Three concrete commitments, each cheap if done early:

**1. Scrub at ingest, before anything persists.** Person and face detection on
every frame; blur before the frames are written to the blob store. The
detection models are already adjacent in this repo (`assetpipe/detect/`). Set
`privacy_state='scrubbed'`. Frames that fail detection entirely (model error)
quarantine rather than pass through.

**2. Raw frames have an expiry.** They are 10–50× the size of the output and,
after a successful train, are needed only for re-training. Default retention
90 days, tracked by `retention_due`, enforced by a job that flips
`privacy_state` to `raw_deleted` and drops the blobs. The scene survives; the
photos of your living room do not linger forever.

**3. Delete means delete.** One command removes a session, its runs, its scenes,
and every artifact whose refcount drops to zero — from the registry *and* the
blob store. Test it. An untested delete path is a promise you have not kept.

If scenes ever become shareable (`visibility='link'`), two more things become
mandatory before the first link is issued: unguessable URLs with revocation,
and a documented answer to "someone shared a scan of a space they had no right
to scan." That is a policy question, not a code question, and it is the reason
`space_type` and `consent` are captured at the source in
[CAPTURE_SPEC.md](CAPTURE_SPEC.md).

---

## CLI shape

```bash
roomscape ingest ./crate_20260730_17_12_16     # validate, scrub, register, enqueue
roomscape runs ls --status failed
roomscape train cap_01J8Z9 --config room-hq    # re-run with a named config
roomscape scenes ls
roomscape scenes show scn_01J...               # provenance, artifacts, perf
roomscape publish scn_01J... --visibility link
roomscape gc --dry-run                         # orphaned blobs, expired raws
roomscape forget cap_01J8Z9                    # the real delete
```
