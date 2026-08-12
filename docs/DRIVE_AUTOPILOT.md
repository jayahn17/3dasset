# Hands-off Google Drive loop (Engin170)

Poll the shared Drive folder → fuse on Linux → size-route GPU → upload RESULT zips back.

**The layout is now MULTI-TENANT.** As of 2026-08-06 the app uploads into
`CrateScans/<account email>/`, not the folder root:

```text
CrateScans/
  crate_2026….zip                    <- older, root-level captures
  alice@example.com/                 <- per-account capture folders
    crate_20260806_11_57_13.zip
  bob@example.com/
    crate_20260806_12_14_09.zip
  Chest/  leg press/                 <- loose HEIC photo sets, NOT sessions
```

`list_zips()` therefore walks one level down, but **only into subfolders whose
name contains `@`** — recursing into `Chest/` would queue 50 stills as if they
were captures. Captures found in a subfolder are keyed `"<account>/<zip>"` so two
accounts uploading the same filename stay distinct, and the account is recorded
in the ledger entry.

> **Tenancy is not solved downstream.** recon3's `publish` stage writes into ONE
> manifest and ONE blob store, so a second customer's capture would appear on the
> same dashboard as the first's. Processing is therefore gated by an allowlist
> that lives **outside the repo** (it is a list of real email addresses and this
> repository is public):
>
> ```bash
> cat captures/status/accounts_allow.json     # ["me@example.com"]
> RECON3_ACCOUNTS="me@example.com" python tools/recon3_autopilot.py
> RECON3_ACCOUNTS='*' …                       # every account — only once the
>                                             # dashboard is per-tenant
> ```
>
> Accounts not on the list are still discovered and logged by name as
> `HELD BACK`, never silently dropped. With no list configured at all, only
> root-level captures process — the safe default for a fresh checkout.

**Two folders are watched, and that is deliberate:**

| folder | id |
|---|---|
| [Engin170_sync](https://drive.google.com/drive/folders/1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI) | `1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI` |
| [CrateScans](https://drive.google.com/drive/folders/1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl) | `1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl` |

> **Do not "switch" to one of them.** On 2026-08-06 the pipeline was pointed at
> CrateScans alone, and it turned out the iPad was still uploading to
> Engin170_sync — `crate_20260806_10_54_59.zip` arrived there that morning while
> CrateScans' newest was a day older. Each folder also holds files the other does
> not. `recon3_autopilot.FOLDERS` therefore watches **both** and unions the
> listings; a zip in both is processed once, because the ledger is keyed by name.
>
> This failure is silent by construction: an empty listing is indistinguishable
> from "nothing new", so a stale id looks exactly like an idle day. Nothing logs
> a miss. Two independent things cause it — the id constant, and the fact that
> the second scrape regex is *anchored on the folder id*.
>
> **Editing the id is not enough.** `tools/recon3_autopilot.py` reads it once at
> import, and the cron line only starts the daemon when one is *not* already
> running (`pgrep … || nohup …`), so a live daemon keeps polling the old folder
> forever:
>
> ```bash
> pkill -f "python tools/recon3_autopilot.py"   # cron restarts it within 10 min
> ```
>
> Confirm with this line in `logs/recon3_autopilot.log`:
> `recon3 autopilot watching Engin170_sync (…), CrateScans (…) every 120s`,
> and note each upload now logs which folder it came from.

```text
iPad Share package
      │
      ▼
Google Drive (CrateScan-*.zip)
      │  every ~60s  tools/drive_rgbd_autopilot.py  (per-file pull)
      ▼
captures/inbox/
      │  every ~5s   tools/watch_inbox.py --mode object
      ▼
demo_out/<name>/object_asset/   (curate → views → object.ply + dims.json)
      │  pack *_RESULT.zip
      │
      │  every ~30s  tools/watch_3dgut_queue.py  (GPU route, serial)
      ├─ longest AABB ≤ 24″ → TRELLIS → metric GLB + STL(mm) → *_TRELLIS_RESULT.zip
      └─ longest AABB  > 24″ → 3DGUT train → splat           → *_3DGUT_RESULT.zip
      ▼
Google Drive (rclone) / captures/outbox/
```

Default: **object** fuse + **GPU route** (`ENABLE_GPU_ROUTE=1`, alias `ENABLE_3DGUT=1`).
- Product-scale (bottle, small appliances) → TRELLIS + `dims_mm.json` + `asset_trellis_mm.stl` (Onshape/Fusion import).
- Furniture / scene → 3DGUT.
Full TSDF: `MODE=rgbd bash scripts/start_engin170_autopilot.sh`.
Disable GPU: `ENABLE_GPU_ROUTE=0 bash scripts/start_engin170_autopilot.sh`.
3DGUT-only (skip TRELLIS): `ENABLE_TRELLIS=0 bash scripts/start_engin170_autopilot.sh`.

## Start (Linux)

```bash
cd ~/3dasset
bash scripts/start_engin170_autopilot.sh
tail -f logs/drive_autopilot.log logs/watch_inbox.log logs/watch_3dgut.log
```

Stop:

```bash
kill $(cat logs/drive_autopilot.pid logs/watch_inbox.pid logs/watch_3dgut.pid 2>/dev/null)
```

## TRELLIS metric mm + Onshape

TRELLIS meshes are normalized. After generate, the queue scales the GLB from RGB-D `dims.json` and writes:

- `dims_mm.json` — L×W×H in millimeters
- `asset_trellis.glb` — scaled (meters)
- `asset_trellis_mm.stl` — vertices in **mm** for Onshape / Fusion / SolidWorks mesh import
- `view.html` — HUD shows mm summary

Import STL in Onshape: **Create → Import → Insert as Mesh**. Measure should match `dims_mm.json` (± mesh noise). See also [ONSHAPE.md](ONSHAPE.md).

## One-time: upload back to Drive (rclone)

Download/pull works with the public link (gdown). **Upload** needs your Google account once:

```bash
# if needed
mkdir -p ~/bin && ln -sfn /tmp/rclone-v1.74.4-linux-amd64/rclone ~/bin/rclone
export PATH="$HOME/bin:$PATH"

rclone config
# n) New remote
# name = gdrive
# storage = drive
# scope = drive
# follow prompts (browser auth)
rclone lsf "gdrive,root_folder_id=1DpLpm4m4TxUn_Vn9Rfhr0o39HKM-Xkbl:"
```

Share the folder as **Editor** for the same Google account (Viewer-only cannot upload).

Until rclone is configured, results are still fused locally and copied to
`captures/outbox/*_RESULT.zip` — drag those into Drive manually.

## Measurements (0.25″)

Every fuse also runs `assetpipe measure` on the package `mesh.obj`:

- `demo_out/<name>/dims.json` — AABB + OBB, raw + **rounded to 0.25 in**
- `demo_out/<name>/measurement.txt` — human-readable L×W×H + crate (+2″/side)
- Shown in `scan_view.html` HUD
- Included in `*_RESULT.zip`

Manual:

```bash
python -m assetpipe measure captures/work/CrateScan-ID/CrateScan-ID \
  --out demo_out/CrateScan-ID
```

Object quote comes from on-device `mesh.obj`. Fused TSDF extents are usually
room-scale — use them only as a scene check, not for crate quoting.

## Already processed

- `CrateScan-2A92CC43` → `demo_out/CrateScan-2A92CC43_rgbd/`
- New zips on Drive (e.g. `CrateScan-5C0D0369`) are picked up automatically once autopilot is running.

## Scale reference for RGB-only captures (no depth)

Photos-only intake (plain camera shots, WhatsApp images) has no metric scale.
Print `docs/aruco_50mm.png` at 100% (the black square must measure 50 mm) and
lay it flat next to the object in at least one photo.

```bash
python tools/scale_from_marker.py photo.jpg --marker-mm 50 --mask mask.png --out dims_marker.json
```

- Detects the marker (DICT_4X4_50), reports mm-per-pixel (±0.1–0.3%).
- With an object mask: same-plane approximate bbox in mm (±5–10%).
- RGB-D sessions ignore this — LiDAR depth stays authoritative; a detected
  marker is only a cross-check.
- A 1 cm cube is too small: at 0.5–1 m it spans too few pixels and sits inside
  the depth sensor's noise floor. 50 mm printed marker ≥10× more accurate.

Generate other sizes: `python tools/scale_from_marker.py --make-marker 80`.
