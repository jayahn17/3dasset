# End-to-end pipeline: phone → Drive → GPU → dashboard

One capture's whole life, and an honest account of which parts are automatic
today. Written to be read by someone who did not build it.

```text
┌── iOS: CrateScanner ────────────────────────────────────────────────────────┐
│ ARKit LiDAR RGB-D + pose · sign in with Google · upload on finish           │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │  OAuth (drive.file), one subfolder per account
                                ▼
┌── Google Drive: CrateScans/ ────────────────────────────────────────────────┐
│   crate_<ts>.zip                  ← legacy, folder root                     │
│   alice@example.com/crate_<ts>.zip ← per-account (current)                  │
│   Chest/ · leg press/             ← loose HEIC photo sets, NOT sessions      │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │  poll every 120 s, public-HTML listing
                                ▼
┌── Linux GPU box (RTX 4080 SUPER) ───────────────────────────────────────────┐
│ tools/recon3_autopilot.py   discover → allowlist → download → extract        │
│ tools/recon3.py             fuse → exif → meshroom → scale → glb            │
│                             → trellis → 3dgut → publish → page              │
│                             (GPU stages serialise on /tmp/recon3_gpu.lock)  │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │  upload assets + manifest.json
                                ▼
┌── Vercel: web/ (Next.js) ───────────────────────────────────────────────────┐
│ manifest fetched from Blob at request time → new scan appears, NO redeploy  │
│ per-asset page: orbit + measure viewer, per-tool download table             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Why the manifest lives in blob storage

Publishing a scan is an **upload, never a redeploy**. `web/lib/manifest.ts`
fetches `manifest.json` from Blob with `no-store`, and both routes are
`force-dynamic`, so the next page view shows the new capture. This is the single
most important design choice in the web layer — it decouples "we processed a
scan" from "we shipped code".

## Multi-tenancy: real in Drive, not yet downstream

The iOS app creates `CrateScans/<signed-in email>/` (`GoogleDriveSync.ensureFolder`,
gated by `GoogleDriveConfig.perAccountSubfolders`, falling back to the folder root
when the account is unknown). So **ingest is multi-tenant**.

Everything after it is not: `recon3`'s publish stage writes into ONE manifest and
ONE blob store, and `web/lib/auth.ts` gates the whole dashboard rather than
scoping it per account. A second person's capture would therefore appear on the
first person's dashboard.

Until that is fixed, processing is gated by an allowlist that lives **outside the
repo** (it is a list of real email addresses; this repository is public):

```bash
cat captures/status/accounts_allow.json    # ["me@example.com"]
RECON3_ACCOUNTS="me@example.com" python tools/recon3_autopilot.py
RECON3_ACCOUNTS='*' …                      # every account
```

Accounts not on the list are **discovered and logged** as `HELD BACK`, never
silently dropped. Making this genuinely multi-tenant means namespacing asset ids
by account and scoping `auth.ts` — that work is not done.

## Two ingest paths exist. Prefer one.

| path | pulls with | feeds | state |
|---|---|---|---|
| `tools/recon3_autopilot.py` | public-HTML scrape, no auth | `recon3.py` (9 stages, ends at the dashboard page) | **preferred** |
| `tools/drive_pull_to_inbox.sh` → `watch_inbox.py` → `watch_3dgut_queue.py` | `rclone` | the older object/GPU-route chain | legacy |

They can ingest the same capture twice under different names, and they do not
share a GPU lock. `rclone` is also **not installed on the GPU box**, so the
legacy path's Drive pull cannot currently run there at all. Pick `recon3` unless
you have a reason not to.

## A thin capture must still deliver

Until 2026-08-11 a stage that could not run **aborted the whole capture**:
`log("… FAILED"); return 1`. Three real uploads produced nothing at all — 1, 1
and 3 frames, two of them with no depth at all — because `fuse` cannot run
without depth and killed the run before TRELLIS, which needs only a single
photo, ever got a chance.

Every stage now steps aside instead, and only `publish` refuses (there is
genuinely nothing to publish):

| capture | fuse | meshroom | gut | trellis | delivered |
|---|---|---|---|---|---|
| 20+ frames, depth | ✓ | ✓ | ✓ | ✓ | 3 models, metric |
| 20+ frames, no depth | skip | ✓ | skip | ✓ | 2 models, NOT METRIC |
| 1–3 frames | skip | skip | skip | ✓ | 1 model, ~2 min |
| unreadable images | skip | skip | skip | skip | nothing, logged |

The two guards that decide this:

- `MIN_SFM_IMAGES = 8` — below it, structure-from-motion cannot solve and
  Meshroom burns GPU minutes to fail. Captures that *did* solve started at 18.
- `has_depth` (≥3 depth frames) — 3DGUT seeds its point cloud from depth, so
  without it the stage is skipped rather than attempted.

A capture with no `dims.json` is published **flagged `NOT METRIC`**. That
distinction is the point: a shape-only model is the right thing to look at and
the wrong thing to quote.

**Degrading gracefully creates a labelling hazard — the page must degrade too.**
The React dashboard read `metric: false` correctly, but the generated benchmark
page did not, and for a few hours the two contradicted each other. The `page`
stage built its cards by string interpolation:

```python
"scale": f"metric via fuse dims {mm}"      # mm == "" whenever dims_mm.json is absent
"input": "4 views", "views": "4"           # hardcoded, on a 1-frame capture
"scale": f"METRIC{res_note}"               # res_note == "" when the scale fit failed
```

So a no-depth capture published a live orbit page reading *"metric via fuse dims"*
with nothing after it, claiming four views it never had, over a **unit-normalised**
TRELLIS mesh whose units the viewer reads as metres — under a lede inviting the
user to "click two points to read inches". Every stage had behaved correctly; only
the copy lied.

All three now branch on the artefact existing rather than interpolating it, and
the lede switches to "this scan had no depth, so it shows shape only" when no card
is metric. The rule: **an empty interpolation is a missing fact, not a blank
value** — never let one land inside a claim about measurement.

## What actually runs, and what does not

Verified on 2026-08-06, re-verified 2026-08-11:

- **Working:** discovery across both Drive folders and per-account subfolders;
  download; `recon3` fuse → … → 3DGUT; blob upload; the dashboard rendering the
  manifest; the orbit + measure viewer.
- **Fixed this round:** the cron keepalive (`pgrep -f "tools/…"` matched cron's
  own `sh -c`, so it never fired); ledger seeding (the daemon re-queued captures
  finished a week earlier); `publish --only` (never matched a `CrateScan_*` name,
  which aborted the run before the customer-facing page stage).
- **Fixed 2026-08-11:** graceful degradation (above), so a thin capture delivers
  instead of aborting; `Image.Resampling` in `watch_3dgut_queue` (added in
  Pillow 9.1, the system python here is 9.0.1 — running `recon3` outside the
  `assetpipe` env crashed view prep); and **the blob token in `web/.env.local`
  had been blanked by a `vercel env pull`**, so `blob_token()` returned `""` and
  every automated publish logged `BLOB_READ_WRITE_TOKEN not set — skipping` and
  exited 0. The pipeline looked healthy and shipped nothing. Restore it with
  `vercel env pull` and check the value is non-empty, not just present.
- **Still open:** nothing survives a reboot (no systemd unit, lingering off);
  two uncoordinated GPU locks; no alerting of any kind; the Drive return path is
  dead (`rclone` absent, ~23 result zips stranded in `captures/outbox/`).

Do not read this section as boilerplate — a pipeline whose supervisor silently
never fires looks exactly like a pipeline with nothing to do.

## Mixing in output from paid tools

The dashboard is deliberately source-agnostic: an asset is a directory of files
plus a manifest entry. A mesh from KIRI, Polycam or any paid service can be
published beside a locally reconstructed one, and `tools/bench_*.py` already
submits to and scores several commercial APIs.

The rule that matters is **provenance and scale honesty**, enforced in
`web/lib/manifest.ts`:

- `hasMetricScale()` / `fileIsMetric()` decide, in ONE place, whether anything in
  an asset may be measured. A generative mesh normalised to a unit cube is not
  metric even when its file is written in millimetres.
- `dimsClaim()` decides what any server-rendered surface may *say* about a size —
  `measured`, `disputed`, `not-measurable`, or silent.
- Every quoted size carries **± 1–2 in**. The `0.25 in` grid in `dims.json` is a
  rounding convention, **not** an accuracy claim. See
  [KIRI_OSS.md](KIRI_OSS.md#error-budget-against-a-025-in-635-mm-spec) for the
  measured error budget behind that number.

If you add a paid tool's output, you must decide which of those buckets it falls
in. Getting it wrong is how a customer quotes a crate built to the size of the
room.

### KIRI Engine, automated — and the part that cannot be

**The web library at `kiriengine.app/webapp/mymodel` is not automatable.** Their
developer API exposes exactly two Model endpoints — retrieve status, download zip
— and **both require a `serialize` id you already hold**. There is no
list/enumerate endpoint, so a scan made in the phone app or the web app is not
reachable programmatically at all. Only jobs submitted *through the API* can be
collected, because only then do you know the id.

So the automation owns the ids:

```bash
export KIRI_API_KEY=kiri-…
python tools/kiri_watch.py --submit bench_out/<bundle> --mode 3dgs   # records the id
python tools/kiri_watch.py --poll     # from cron — collects whatever is ready
python tools/kiri_watch.py --list
```

`tools/bench_kiri.py` blocks for up to 45 minutes on a single job, which is fine
by hand and useless from cron; `kiri_watch` writes every accepted submission to
`captures/status/.kiri_jobs.json` and advances all pending jobs one step per
call. That matters because **KIRI deletes results after ~3 days** — a submitted
job nobody polls is a credit spent and thrown away, so the poller belongs on a
schedule. Each submission costs 1 credit ($1).

Every collected result gets a `NO_METRIC_SCALE.txt` written beside it. That is
not decoration: KIRI only ever sees RGB, and that marker is what
`web/lib/manifest.ts::hasMetricScale()` reads to refuse a tape measure over a
scale-free mesh.

Getting app/webapp scans in without the API means exporting them by hand into a
Drive folder — the same intake as any other capture.

## Run it

```bash
conda activate assetpipe

# one capture, by hand, end to end
python tools/recon3.py <session-dir> --name <run-name>

# the standing daemon (also kept alive by cron every 10 min)
python tools/recon3_autopilot.py

# publish/refresh the dashboard for one asset
python tools/publish_dashboard.py --uploader vercel --only <run-name>
```

Related: [DRIVE_AUTOPILOT.md](DRIVE_AUTOPILOT.md) (ingest, folders, allowlist) ·
[DASHBOARD.md](DASHBOARD.md) (publishing, blob layout) ·
[KIRI_OSS.md](KIRI_OSS.md) (open-source reconstruction benchmark + error budget) ·
[../mobile/CrateScannerApp/WORKFLOW.md](../mobile/CrateScannerApp/WORKFLOW.md) (the capture app).
