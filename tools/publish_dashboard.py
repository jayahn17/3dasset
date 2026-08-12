#!/usr/bin/env python3
"""Publish processed scans to the customer dashboard.

The last mile of the pipeline: iOS capture → Drive → this box processes →
**this** uploads the results and a manifest → the Vercel site renders them.

The manifest lives in blob storage next to the assets, not in the web repo, so
a new scan appears on the site **without a redeploy** — the page fetches the
manifest at request time. Publishing is therefore just an upload.

    # try it with no cloud account at all — writes into web/public/
    python tools/publish_dashboard.py --uploader local \\
        --dest-dir web/public/assets --base-url /assets

    # real publish
    export BLOB_READ_WRITE_TOKEN=vercel_blob_rw_...
    python tools/publish_dashboard.py --uploader vercel

    python tools/publish_dashboard.py --dry-run          # what would go, and how big

    # publish one new scan without wiping the other three off the dashboard
    python tools/publish_dashboard.py --uploader vercel --only 7C3DD25E

Every artifact is tagged with the **target apps** it opens in (Onshape,
Blender, MuJoCo, Isaac, …) so the site can offer "download for X" instead of a
pile of extensions the customer has to guess between. That mapping lives here,
in one table, because it is pipeline knowledge — the web app just renders it.

**Every published file carries at least one target.** That is an invariant, not
a nicety: web/lib/manifest.ts filesByTarget() groups the download tables
strictly by `file.targets`, so a file with an empty list is uploaded, stored,
listed in the manifest — and appears on no screen. object_mesh.ply sat in
exactly that hole ('*_m.ply' does not fnmatch a bare object_mesh.ply): the file
the printed dimensions are measured from, named as the one to check by the
Formats table, by /help twice and by the viewer, and downloadable from nowhere.
So TARGET_RULES must cover every DELIVERABLES pattern, and a run that finds an
untargeted file says so loudly instead of shipping it invisible — see
check_target_coverage.

**Some deliverables are true metres and yet are NOT the object.**
demo_out/*/mesh_preview/object_mesh_m.ply is the capture app's raw ARKit LiDAR
sweep of the whole ROOM: on BE53A423 it is 67.71 × 11.35 × 90.10 in against a
9.00 × 5.75 × 11.00 in object. Its name is one underscore from the CAD meshes,
so it used to pick up the friendly '*_m.ply' Blender note and be offered as if
it were the object; the web viewer has always hard-excluded mesh_preview/ as
"the biggest trap". Such files now publish with

    scope   str   "scene" — real metres, but the room this object was captured
                  in, not the object. Absent on every other file, which is the
                  ordinary case and makes no claim of any kind

and with a single 'generic' target whose note says what the file is. `scope`
is about WHAT WAS MEASURED, never about whether a file can be measured: that
question has exactly one answer, in web/lib/manifest.ts.

Raw 3DGUT PLYs are ~250 MB each and are **excluded by default**: the .splat is
the viewable one at a tenth the size. Pass --include-raw to ship them too.

Four things this does that are not obvious:

**Merge, not replace.** The manifest is one object at one stable pathname, so
publishing rewrites the whole catalogue. `--only <id>` used to therefore *delete*
every other asset from the dashboard — fatal the moment publishing is automated
per-scan. So a run now fetches the published manifest first and merges by asset
id: this run's assets replace their entry, everything else is carried forward
untouched. On by default when --only is used; --no-merge for the old behaviour,
--manifest-url to say where the remote manifest is.

The corollary matters when a record on the dashboard is *wrong*: publishing an
asset is the only way to change what its card says. Stop collecting an asset —
excluding a directory, renaming it — and its published record is not removed,
it is frozen exactly as it is. Correcting a bad card means keeping it in the
run, not dropping it.

**The measure field.** Each asset records *which file* the dimensions on the
page were actually computed from (`measure`), so the browser's measuring tool
loads that exact geometry. It matters: object_mesh.glb is a second, more
aggressively trimmed Poisson solve of the same cloud and comes out 8-29 %
smaller per axis than the object_mesh.ply that dims.json measured. Show one and
print the other's number and the site contradicts itself.

**Upload skipping.** `.publish_cache/<prefix>.json` records dest pathname →
(sha256, bytes, url). An unchanged file is hashed instead of re-PUT, so a cron
that republishes every 10 minutes costs one small manifest PUT, not 138 MB.
The cache is keyed on *everything that decides the resulting URL* — for the
local uploader that includes the destination directory and the base URL — and a
hit against a local destination is stat-checked before it is trusted.

**Not everything is metric.** The RGB-only TRELLIS route produces a normalized
mesh and drops a NO_METRIC_SCALE.txt beside it (tools/rgb_only_trellis.py).
Those assets publish with `metric: false`, **no** dimensions and **no**
`measure` field, so the site cannot offer to measure geometry whose numbers
would be fiction. A generated mesh with no marker and no proof of scale either
way gets the same treatment — see _has_metric_scale.

**And a dimension is only published if a shipped file backs it.** A capture
carries several measurements — one dims.json per rerun, plus the room sweep's
own scene-scale figure — and the one that gets printed is the one whose
measured geometry is a file the customer can download and re-measure in the
browser. See _pick_dims.

**A published dimension says how it was rounded.** Two routes produce these
numbers and they quantise differently — the RGB-D fuse snaps to a 0.25 in
crate grid (dims.json `summary`) while the splat → CAD route prints the raw
AABB (cad_report.json `summary_in`) — so `dims` on its own means two different
things depending on a route the consumer cannot see. The qualifier therefore
ships next to the figure. The published shape, in full:

    dims             str   the figure to print
                           "52.50 in × 66.75 in × 66.75 in"
    dims_raw         str   that same measurement unrounded
                           "52.38 in × 66.82 in × 66.82 in"
                           omitted only if the source recorded no raw figure
    dims_quantized   bool  true   → `dims` is a rounded restatement of
                                    `dims_raw`; say so, or print dims_raw
                           false  → `dims` IS the measurement (dims == dims_raw)
                           absent → there is no dims_raw to compare with, so
                                    which one it is, is unknown
    dims_quantum_in  num   the grid `dims` was snapped to, in inches (0.25).
                           Present only when dims_quantized is true. It is a
                           ROUNDING CONVENTION and NOT an accuracy claim — real
                           precision on this pipeline is ±1–2 in (~19 mm
                           surface RMS), an order coarser than the grid
    dims_mm          obj   the same box in millimetres {length_x, depth_y,
                           height_z}, only from the route that measured in mm

Those five are one unit (DIMENSION_FIELDS) and they live or die with
`measure`: if the geometry the figure came off is not among the published
files, no number is published either — in enrich() when the asset has no
metric scale at all, and in publish() when that one file failed to upload.

The sentence to print already exists on the web side and must not be rewritten
there: web/lib/formats.ts PRECISION_NOTE says the 0.25 in figure is a rounding
convention and not an accuracy claim. dims_quantized only names the assets
whose printed figure IS that rounded one. It says nothing about whether an
asset can be measured at all — that question has exactly one answer, in
web/lib/manifest.ts (hasMetricScale / fileIsMetric / isDeclaredNonMetric), and
this field is not a second one.

Exit codes. tools/autopublish.py gates its "this asset is published" ledger
write on these, so both sides must agree on exactly this table:

    0  clean — every file is in the store and the manifest was published
    1  nothing published — no assets matched, or every upload failed, or the
       manifest itself failed to upload. The live manifest is left intact
    2  usage error (argparse's own exit code; also "no blob token")
    3  merge aborted — the published manifest could not be read, so the prior
       state is unknown. Nothing was written
    4  merge aborted — the merge would have dropped an asset. Nothing written
    5  PARTIAL — the manifest was written and published, but at least one file
       failed to upload. The named assets are INCOMPLETE: do not record them as
       published, re-run to retry. rc 5 is the whole reason a transient blob 5xx
       on one file of thirteen no longer strands an asset forever
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import itertools
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MANIFEST_NAME = "manifest.json"

# Exit codes, contracted with tools/autopublish.py — the table in the module
# docstring is the spec. argparse owns 2, so nothing else may take it.
RC_OK = 0
RC_NOTHING_PUBLISHED = 1
RC_USAGE = 2
RC_MERGE_UNKNOWN_STATE = 3
RC_MERGE_UNSAFE = 4
RC_PARTIAL = 5

SCAN_ID = re.compile(r"CrateScan-([0-9A-F]{8})", re.I)
# Derived dirs (sim_out/DC22F084_couch_splat) carry the id without the prefix.
BARE_ID = re.compile(r"(?<![0-9A-Za-z])([0-9A-Fa-f]{8})(?![0-9A-Za-z])")

# ---------------------------------------------------------------------------
# Which app opens which file. One table, because the customer thinks in
# "I want to measure this in Onshape", not in file extensions.
# (glob, target-id, how-to note)
TARGET_RULES: list[tuple[str, str, str]] = [
    ("*_mm.stl",      "cad",     "The part in millimetres — import as a Mesh."),
    ("*_lite_mm.stl", "cad",     "Lighter mesh (~25k faces) — faster to pick in CAD."),
    ("*.dxf",         "cad",     "Section profile — insert into a sketch and dimension it."),
    ("scale_check_100mm.stl", "cad", "A 100 mm cube — import once to confirm units."),

    ("*_m.ply",       "blender", "Metres, Z-up. Set Units ▸ Length: Millimeters to read mm."),
    # The RGB-D fuse's own mesh. It matched NO rule at all before — '*_m.ply'
    # does not fnmatch a bare object_mesh.ply — so it published with targets []
    # and filesByTarget() dropped it off the page, while four other surfaces
    # told the customer to measure exactly this file. Blender, not cad: it is
    # metres rather than millimetres and Onshape/Fusion/SolidWorks do not read
    # PLY, which is why web/lib/formats.ts routes it to the mesh tools instead.
    ("object_mesh.ply", "blender",
     "The mesh the RGB-D fuse measured — metres, Y-up (ARKit frame). In Blender "
     "set Units ▸ Length: Millimeters to read mm; CloudCompare and MeshLab read "
     "the metres straight off."),
    ("asset_trellis.ply", "blender",
     "TRELLIS generative mesh, straight from the network. Check the unit chip "
     "beside the filename before quoting any size off it."),
    ("*.glb",         "blender", "Metres. Also opens in Unity, Unreal, three.js."),
    ("*.usda",        "blender", "Needs Blender 3.5+ (USD import). Carries colour."),
    ("*_cloud_m.ply", "blender", "Point cloud — vertices only, no faces."),

    ("*.usda",        "isaac",   "Z-up, 1 m/unit, RigidBody + MassAPI + convex colliders."),
    ("*.xml",         "mujoco",  "MJCF — needs the collision_*.obj beside it."),
    ("collision_*.obj", "mujoco", "Convex collision hulls referenced by the MJCF."),
    ("*_visual.obj",  "mujoco",  "Visual mesh referenced by the MJCF."),
    ("*.urdf",        "ros",     "Single-link rigid prop."),

    ("*.splat",       "web",     "Gaussian splat — opens in the browser viewer."),
    ("scene_gaussians.ply", "web", "Raw trained gaussians (large). For retraining, not viewing."),
    # Ahead of '*.obj': first rule per target wins, and KIRI's OBJ has NO vertex
    # colours (its `v` lines carry three floats), only UVs into 3DModel.jpg. The
    # generic note promised colour that is not in the file, which reads as a
    # broken export when it opens grey in another tool.
    ("3DModel.obj",   "generic", "Wavefront OBJ, UV-textured — needs 3DModel.mtl and 3DModel.jpg beside it."),
    ("*.obj",         "generic", "Wavefront OBJ with vertex colours."),
    # Sidecars of the generated OBJ, and the pipeline's own no-scale marker.
    # All three ship (manifest.ts reads the marker out of the file list), so
    # all three need a target or they ride along invisible. material_0.png is
    # matched ahead of '*.png' because it is the texture, not a preview render.
    ("material.mtl",  "generic", "Material sidecar for the .obj beside it — keep the two together."),
    ("material_0.png", "generic", "Texture map the .mtl points at — not a preview image."),
    # Same three-file bundle from KIRI's exporter, under its own names. Without
    # these two rules the .mtl and .jpg publish with targets=[] and are listed on
    # no screen, so the customer downloads a 28 MB OBJ that renders untextured
    # grey and has no way to discover why.
    ("3DModel.mtl",   "generic", "Material sidecar for 3DModel.obj — download both, plus the .jpg."),
    ("3DModel.jpg",   "generic", "Baked 4K texture the .mtl points at — not a preview image."),
    # The literal name, not NO_METRIC_MARKER: this table is evaluated long
    # before that constant is defined. They must stay in step.
    ("NO_METRIC_SCALE.txt", "generic",
     "The pipeline's own marker: an RGB-only capture, no depth frames, so the "
     "mesh beside it was never scaled to anything real."),
    ("*.png",         "generic", "Preview render."),
    ("*.jpg",         "generic", "Preview render."),
    ("*.json",        "generic", "Measurements and provenance."),
]

# Files that are in true metres and are NOT the object. The capture app writes
# demo_out/<scan>/mesh_preview/object_mesh_m.ply — the raw ARKit LiDAR sweep of
# the whole room — one underscore away from the CAD meshes, and it is a real
# deliverable pattern ('*_m.ply'), so it ships. What it must never do is ship
# looking like the object: on BE53A423 it is 67.71 × 11.35 × 90.10 in against a
# printed 9.00 × 5.75 × 11.00 in. Matched on the DIRECTORY, because the filename
# is indistinguishable from a good one.
SCENE_SWEEP_DIRS = ("mesh_preview",)
SCENE_SWEEP_NOTE = (
    "The capture app's LiDAR sweep of the whole ROOM this object was scanned "
    "in — true metres, but its bounding box is the room's, not the object's. "
    "Context only: the object's own mesh is object_mesh.ply."
)

TARGETS = {
    "cad":     {"name": "Onshape / Fusion / SolidWorks", "icon": "📐",
                "blurb": "Measure and dimension. Millimetres."},
    "blender": {"name": "Blender", "icon": "🟠",
                "blurb": "View, edit, render. Metres, Z-up."},
    "mujoco":  {"name": "MuJoCo", "icon": "🦾",
                "blurb": "Physics simulation. MJCF + convex colliders."},
    "isaac":   {"name": "Isaac Sim / Omniverse", "icon": "🌐",
                "blurb": "USD with physics schemas."},
    "ros":     {"name": "ROS / URDF", "icon": "🤖",
                "blurb": "Robot description format."},
    "web":     {"name": "Web viewer", "icon": "🖥️",
                "blurb": "Opens in a browser, no install."},
    "generic": {"name": "Other", "icon": "📦",
                "blurb": "Previews, measurements, raw data."},
}

# An **allowlist**, deliberately. The first version of this collected "every
# 3D extension minus an exclude list" and swept up 3.6 GB of training
# intermediates — TSDF scene meshes, 94 MB contact sheets, per-frame JPEGs.
# A customer dashboard must never ship an intermediate by accident, so
# anything not named here simply does not go.
DELIVERABLES = [
    # generated mesh (TRELLIS)
    "asset_trellis.glb", "asset_trellis.obj", "asset_trellis.ply",
    "material_0.png", "material.mtl", "NO_METRIC_SCALE.txt",
    # KIRI Engine web-app export: OBJ + MTL + one baked texture, all named
    # 3DModel.* by their exporter. The .mtl's `map_Kd 3DModel.jpg` is relative,
    # so the three only render together — publishing the OBJ alone gives an
    # untextured grey mesh. Scale-free (RGB-only), which is why every such
    # capture ships a NO_METRIC_SCALE.txt in its own directory.
    "3DModel.obj", "3DModel.mtl", "3DModel.jpg",
    # trained splat — the .splat is the viewable one, a tenth of the PLY
    "scene_gaussians.splat",
    # per-renderer panel splats (3DGUT `scene_panel`, nerfstudio
    # `splatfacto_panel`, …). Named by whichever trainer produced them, so match
    # the suffix rather than adding a line per renderer. The .meta.json carries
    # radius_m, which the splat viewer needs to frame the object — publishing the
    # splat without it gives a viewer that opens on an empty screen.
    "*_panel.splat", "*_panel.splat.meta.json",
    # CAD exports
    "*_mm.stl", "*_mm.obj", "sections/*.dxf", "scale_check_100mm.stl",
    # metric / sim exports
    "*_m.ply", "*_m.obj", "*_cloud_m.ply", "*.usda", "*.urdf", "*.xml",
    "collision_*.obj", "*_visual.obj", "*_visual.glb",
    # object asset from the RGB-D fuse
    "object_mesh.glb", "object_mesh.ply", "object.splat",
    # the numbers and the provenance
    "cad_report.json", "dims.json", "dims_mm.json", "sim_export.json",
    "route_decision.json", "scene_gaussians.splat.meta.json", "asset.json",
]
# A capture's hero image, best first. Downscaled on publish — the raw contact
# sheets are 8000x12000 PNGs.
PREVIEWS = [
    "render_check/contact_sheet.png", "views_part.png", "preview.png",
    # The fuse writes its hero photo INSIDE object_asset/, and these patterns are
    # matched with a non-recursive d.glob(), so the bare "photo.jpg" below never
    # hit: every RGB-D asset published with preview=null and every card on the
    # dashboard rendered the grey "no preview" placeholder. Listed ahead of the
    # bare names, which still cover assets that keep a hero at the top level.
    "object_asset/photo.jpg", "object_asset/views_picked.jpg",
    "photo.jpg", "views_picked.jpg", "render/*.png", "views/*.png",
]
BIG_RAW = ["scene_gaussians.ply"]          # ~250 MB each; opt-in via --include-raw
PREVIEW_MAX_W = 1400


def is_scene_sweep(rel: str) -> bool:
    """Is this file the ROOM rather than the object? (see SCENE_SWEEP_DIRS)"""
    return any(part in SCENE_SWEEP_DIRS for part in rel.split("/")[:-1])


def targets_for(name: str, rel: str | None = None) -> list[dict]:
    """All (target, note) pairs matching a file, most specific rule first.

    `rel` is the path inside the capture directory. It is optional only so that
    a caller holding a bare filename still works; pass it wherever it is known,
    because it is what distinguishes mesh_preview/object_mesh_m.ply (the room)
    from every other *_m.ply (the object).
    """
    if rel and is_scene_sweep(rel):
        # ONE target, replacing the name-matched ones rather than joining them.
        # Falling through to '*_m.ply' would list the room sweep in the Blender
        # table under "Metres, Z-up. Set Units ▸ Length: Millimeters to read
        # mm." — a note that reads as an invitation to measure the object.
        return [{"target": "generic", "note": SCENE_SWEEP_NOTE}]
    out: dict[str, str] = {}
    for pattern, target, note in TARGET_RULES:
        if fnmatch.fnmatch(name, pattern) and target not in out:
            out[target] = note
    return [{"target": t, "note": n} for t, n in out.items()]


def scan_key(d: Path) -> str:
    """Group artifacts that came from the same capture.

    Matching bare 8-hex anywhere is wrong — a date like `20260730` is valid
    hex and silently became its own "asset". So: the CrateScan-XXXXXXXX id if
    present, else follow the provenance a derived export records about its
    source (cad_out/couch knows it came from DC22F084), else the directory.
    """
    def hex_id(text: str) -> str | None:
        m = SCAN_ID.search(text)
        if m:
            return m.group(1).upper()
        # An all-digit run is a date, not a scan id — `crate_20260730_scooter`
        # was becoming its own bogus "20260730" asset. Check the token itself,
        # not the surrounding text: a case-insensitive [A-F] happily matches
        # the "c" in "scooter".
        for m in BARE_ID.finditer(text):
            if not m.group(1).isdigit():
                return m.group(1).upper()
        return None

    for text in (d.name, d.as_posix()):
        got = hex_id(text)
        if got:
            return got
    for prov, field in (("cad_report.json", "source_splat"),
                        ("sim_export.json", "source")):
        p = d / prov
        if p.is_file():
            try:
                src = json.loads(p.read_text()).get(field, "")
            except Exception:
                continue
            m = SCAN_ID.search(str(src)) or BARE_ID.search(str(src))
            if m:
                return m.group(1).upper()
    return d.name


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def gather(include_raw: bool) -> dict[str, dict]:
    """Walk the output trees and group every shippable artifact by capture."""
    roots = [
        (REPO / "demo_out", "scan"),
        (REPO / "cad_out", "cad"),
        (REPO / "sim_out", "sim"),
    ]
    assets: dict[str, dict] = {}

    for root, kind in roots:
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            keep: list[Path] = []
            seen: set[Path] = set()
            patterns = list(DELIVERABLES) + (BIG_RAW if include_raw else [])
            for pattern in patterns:
                for f in d.rglob(pattern):
                    rel = f.relative_to(d).as_posix()
                    # a deliverable is never inside the training scratch dirs
                    if rel.startswith(("3dgrut_data/", "3dgut_runs/")):
                        continue
                    if f not in seen and f.is_file():
                        seen.add(f)
                        keep.append(f)
            if not keep:
                continue

            key = scan_key(d)
            rec = assets.setdefault(key, {
                "id": key, "title": key, "sources": [], "files": [],
                "kinds": [], "updated": 0.0,
            })
            rec["sources"].append(d.relative_to(REPO).as_posix())
            if kind not in rec["kinds"]:
                rec["kinds"].append(kind)
            for f in sorted(keep):
                try:
                    st = f.stat()          # dangling symlinks exist in curated dirs
                except OSError:
                    continue
                rel = f.relative_to(d).as_posix()
                entry = {
                    "name": f.name,
                    "path": f.relative_to(REPO).as_posix(),
                    "rel": rel,
                    "bytes": st.st_size,
                    "size": human(st.st_size),
                    "targets": targets_for(f.name, rel),
                    "group": d.name,
                }
                if is_scene_sweep(rel):
                    # Machine-readable twin of SCENE_SWEEP_NOTE, so the site can
                    # mark the file rather than relying on a customer reading
                    # prose. Only ever emitted here; absent means "ordinary
                    # deliverable" and asserts nothing.
                    entry["scope"] = "scene"
                rec["files"].append(entry)
                rec["updated"] = max(rec["updated"], st.st_mtime)

            # hero image, first match wins
            if "preview_src" not in rec:
                for pat in PREVIEWS:
                    hit = next(iter(sorted(d.glob(pat))), None)
                    if hit and hit.is_file():
                        rec["preview_src"] = hit.as_posix()
                        break
    return assets


def check_target_coverage(assets: dict[str, dict]) -> list[tuple[str, str]]:
    """Every published file must open in SOMETHING. Say so, loudly, when one does not.

    web/lib/manifest.ts filesByTarget() builds the download tables strictly from
    file.targets, so a file with an empty list is uploaded, stored and then
    invisible — the customer cannot reach it from any screen. That is how the
    mesh the printed dimensions are measured from spent four rounds with no
    download link anywhere on the site while four other surfaces told the
    customer to measure exactly that file.

    Reported, never fatal. The exit codes are contracted with
    tools/autopublish.py and a missing note must not strand a good publish. The
    wording respects that contract too: autopublish greps this output for "!!"
    or "fail" next to an asset id to decide the asset is INCOMPLETE, so these
    lines carry neither word — otherwise every tick would retry, and escalate,
    an asset that published perfectly.
    """
    missing = [(rec["id"], f"{f['group']}/{f['rel']}")
               for rec in assets.values()
               for f in rec["files"] if not f["targets"]]
    if missing:
        print(f"\nWARNING: {len(missing)} file(s) about to be published match no "
              f"rule in TARGET_RULES, so the site can offer no download link for "
              f"them and nobody will ever see them. Add a rule in "
              f"tools/publish_dashboard.py, or stop shipping them:",
              file=sys.stderr)
        for aid, where in missing[:20]:
            print(f"  no target group: {aid}  {where}", file=sys.stderr)
        if len(missing) > 20:
            print(f"  … and {len(missing) - 20} more", file=sys.stderr)
    return missing


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streamed digest — some deliverables are 250 MB and must not be slurped."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def make_preview(src: str, cache: Path) -> Path | None:
    """Downscale a hero image; contact sheets are 8000x12000 and 94 MB."""
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
    except ImportError:
        return None
    # Keyed on the source *bytes*, not its path. The old key was the path
    # sanitised and truncated to 90 chars, which (a) never invalidated, so a
    # regenerated contact sheet kept showing the old thumbnail forever, and
    # (b) could collide between two long paths sharing a tail.
    try:
        key = sha256_file(Path(src))[:16]
    except OSError:
        return None
    out = cache / (key + ".jpg")
    if out.is_file():
        return out
    try:
        im = Image.open(src).convert("RGB")
        if im.width > PREVIEW_MAX_W:
            im = im.resize((PREVIEW_MAX_W,
                            round(im.height * PREVIEW_MAX_W / im.width)),
                           Image.LANCZOS)
        cache.mkdir(parents=True, exist_ok=True)
        im.save(out, "JPEG", quality=82, optimize=True)
        return out
    except Exception:
        return None


def _repo_rel(p: str | None) -> str | None:
    """A path recorded inside a report → the repo-relative form gather() uses."""
    if not p:
        return None
    q = Path(os.path.normpath(REPO / p))
    try:
        return q.relative_to(REPO).as_posix()
    except ValueError:
        pass
    try:
        # a symlinked output dir is the only reason the literal form misses
        return q.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return None                     # measured something outside the repo


def _shipping(rec: dict, repo_rel: str | None) -> dict | None:
    """The file record for a repo-relative path, if it is actually shipping.

    Match on the full path, never on the basename: `object_asset/object_mesh.ply`
    exists in two of BE53A423's groups and they differ by 0.17 in, and
    `couch_visual.glb` exists in two of DC22F084's and they differ by 22 in.
    """
    if not repo_rel:
        return None
    for f in rec["files"]:
        if f["path"] == repo_rel:
            return f
    return None


def _measure_from_cad(rec: dict, report: dict) -> dict | None:
    """Which geometry a cad_report.json's summary_in was taken from.

    The number is the AABB of the full-resolution millimetre mesh, but that
    file is a colourless ~100k-face STL that no browser wants. `ply_meters` is
    the same solve exported in metres (decimated) and measures within 0.1 in of
    the printed figure, so it is what the viewer should load.
    """
    ex = report.get("exports", {}) or {}
    stl = _repo_rel(ex.get("stl"))
    measured = Path(stl).name if stl else "the millimetre CAD mesh"
    for cand in (ex.get("ply_meters"), ex.get("stl"), ex.get("obj_meters"),
                 ex.get("glb_meters")):
        hit = _shipping(rec, _repo_rel(cand))
        if not hit:
            continue
        if hit["path"] == stl:
            src = f"cad_report.json from the splat → CAD route ({hit['name']})"
        else:
            src = (f"cad_report.json from the splat → CAD route "
                   f"(measured on {measured}; {hit['name']} is that same mesh "
                   f"in metres)")
        return {"path": hit["path"], "source": src}
    return None


def _measure_from_dims(rec: dict, obj: dict) -> dict | None:
    """Which geometry a dims.json measured — it records the exact file."""
    hit = _shipping(rec, _repo_rel(obj.get("source_path")))
    if not hit:
        # The measured mesh is not among the deliverables. Substituting a
        # near-neighbour is how the page ends up printing one mesh's numbers
        # over another's geometry, so ship no measure file rather than a guess.
        return None
    return {"path": hit["path"],
            "source": f"dims.json from the RGB-D fuse ({hit['name']})"}


# ------------------------------------------------- fuses that measured nothing
#
# A dims.json can be complete, well-formed, internally consistent — and about
# something that is not the object. _pick_dims already refuses a figure whose
# geometry the asset does not ship; these two checks refuse a figure whose
# geometry the asset ships and which is still not a measurement. Both are
# properties of how the RGB-D fuse builds geometry, so both read the fuse's own
# sidecar files rather than guessing from the number.
#
# 1. THE SQUARE FOOTPRINT. Two of the three AABB sides identical to the last
#    stored decimal. A Poisson solve over a sparse cloud is run on a padded
#    grid, and when the cloud is too thin to constrain the lateral axes the mesh
#    fills that grid instead of the object — so the two lateral extents come
#    back as the grid's own square footprint, exactly equal, at 1.10x the
#    cloud's larger axis. Nothing about the object is in those two numbers.
#    demo_out/CrateScan-0D2F9333 publishes 38.65 x 48.36 x 48.36 in this way off
#    a cloud measuring 43.97 x 42.77, and its OBB (65.10 x 46.07 x 44.68, three
#    distinct sides) is the proof that the geometry itself is not symmetric.
#
# 2. THE SINGLE VIEW. One depth frame is a 2.5D sheet: it records the side
#    facing the camera and nothing behind it. The AABB of that sheet has a real
#    height and width and a depth that is only however far the visible surface
#    happened to curve away — a lower bound presented in the same sentence, same
#    units, same 0.25 in grid as the two sides that were genuinely measured.
#
# Neither is a taste threshold. The tie in (1) is exact — across the 26 fuses on
# this box the three degenerate ones tie at 0.0000 in and the closest healthy
# pair is 0.4332 in apart, so DEGENERATE_TIE_IN sits three orders below the
# nearest true measurement and cannot reach one. (2) counts frames the fuse says
# it kept, which is the fuse's own record of how many views are in the cloud.
DEGENERATE_TIE_IN = 1e-3
MIN_MERGED_VIEWS = 2

_SQUARE_FOOTPRINT_REASON = (
    "Two of its three sides came back identical to four decimal places, which a "
    "reconstruction of a real object does not do — that is the square grid the "
    "mesh was solved on, filled in because the capture was too sparse to pin the "
    "other two axes down, and not the item. Only the remaining side was ever "
    "measured. Re-capture this object before quoting any of it."
)

_SINGLE_VIEW_REASON = (
    "It was rebuilt from a single depth frame, so only the one face turned "
    "toward the camera was ever measured — the far side of the object is not in "
    "this geometry at all, and the figure's depth is a lower bound rather than a "
    "measurement. Re-capture it with several views taken around the object "
    "before quoting a size."
)


def _fuse_dispute(obj: dict, dims_path: Path) -> str | None:
    """Why this RGB-D measurement cannot be stood behind, or None if it can.

    The string is printed to the customer verbatim, beside the figure and in
    place of the tolerance chip — see dimsClaim() in web/lib/manifest.ts, which
    has taken a publisher verdict since before anything emitted one and treats
    it as outranking its own room-scale floor. So it says what is wrong with THIS
    number and what to do, and never merely that something is wrong.
    """
    raw = ((obj.get("aabb") or {}).get("raw_inches") or {})
    sides = [raw.get("length"), raw.get("width"), raw.get("height")]
    if all(isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0
           for s in sides):
        for a, b in itertools.combinations(sides, 2):
            if abs(a - b) < DEGENERATE_TIE_IN:
                return _SQUARE_FOOTPRINT_REASON

    # analysis.json is written beside dims.json by the same fuse run. Absent is
    # not suspicious — older captures predate it — so an unreadable or missing
    # sidecar withholds this second check rather than condemning the asset on it.
    try:
        analysis = json.loads((dims_path.parent / "analysis.json").read_text())
    except Exception:
        return None
    kept = analysis.get("frames_kept")
    if isinstance(kept, list) and len(kept) < MIN_MERGED_VIEWS:
        return _SINGLE_VIEW_REASON
    return None


def _pick_dims(cands: list[dict]) -> dict | None:
    """The one measurement an asset may print, out of everything it carries.

    THE TRAP this replaces: an asset routinely holds SEVERAL measurements —
    one dims.json per rerun directory, plus the room sweep's own, which
    measures the fused scene rather than the object. The old rule was
    positional, "the first dims.json wins", and for demo_out/bench_A7C9_fixed
    that first one is the sweep's: its source_path is an unscaled on-device
    solve under captures/work/ that this asset does not ship, and it published
    259.25 in x 262.00 in x 127.50 in against shipped geometry measuring
    28 in — 9.4x out. Worse, because the file it measured is not a
    deliverable there was no `measure` file either, so the browser's measuring
    tool could not contradict the number on the card. That dims.json says so
    itself: "Fused TSDF extents are scene-scale unless the capture cropped to
    the object".

    So the rule is not positional. A measurement may be published only if the
    geometry it was taken from is a file this asset SHIPS — which is exactly
    the condition for being able to emit `measure`. Order breaks ties only
    among candidates that already pass, so the caller's preference (a
    cad_report over an RGB-D fuse, earlier group over later) still holds.
    If nothing passes, no dimensions are published at all: a blank is honest,
    an unverifiable 259 in is not.
    """
    for c in cands:
        if c.get("measure"):
            return c
    return None


# Every field that carries or qualifies the printed dimension. Named once
# because they are one unit: they are published together and dropped together,
# and the two drop sites (no metric scale in enrich, measure file lost in
# publish) must not drift apart. Dropping `dims` while leaving `dims_quantized`
# behind would be a qualifier with nothing to qualify; keeping `dims` while
# dropping the rest is the bug that shipped an inch figure with no file behind
# it. The shape of each is documented in the module docstring — that block is
# the contract the web app reads.
DIMENSION_FIELDS = ("dims", "dims_raw", "dims_quantized", "dims_quantum_in",
                    "dims_mm", "dims_disputed")


# A generator (TRELLIS) mesh comes out of the network normalized: it has a
# shape but no size. The RGB-only route says so with a marker file; the metric
# route proves the opposite by writing dims_mm.json / a millimetre export.
GENERATED_MESHES = {"asset_trellis.glb", "asset_trellis.obj", "asset_trellis.ply"}
NO_METRIC_MARKER = "NO_METRIC_SCALE.txt"
METRIC_REPORTS = {"dims.json", "dims_mm.json", "cad_report.json", "sim_export.json"}
METRIC_EXPORTS = ("*_mm.stl", "*_mm.obj", "*_m.ply", "*_m.obj", "*_cloud_m.ply")


def _has_metric_scale(rec: dict) -> bool:
    """Does this asset's geometry carry real-world scale?

    False here becomes `metric: false` in the manifest and takes the dimensions
    and the `measure` field out with it — the web viewer's contract for "do not
    print inches for this, there is no file whose numbers mean anything".

    Two ways to be non-metric, and the second one matters:
      1. NO_METRIC_SCALE.txt ships with it (tools/rgb_only_trellis.py writes
         one). This is the contracted signal.
      2. Everything it ships is a generated mesh and *nothing* proves scale.
         The absence of the marker proves nothing — the RGB-only v2 export
         simply never wrote one (tools/bench_3dgut_html.py calls that state
         "unknown" for the same reason) — and a normalized mesh presented as
         metric is the expensive mistake, so unknown is treated as not metric.

    Deliberately asset-level and deliberately conservative: an asset that
    carries the marker in ONE of its groups loses its measure field even if
    another group measured fine. Publishing no number beats publishing a
    number that belongs to different geometry.
    """
    names = {f["name"] for f in rec["files"]}
    if NO_METRIC_MARKER in names:
        return False
    if not names & GENERATED_MESHES:
        return True
    if rec.get("dims") or rec.get("dims_mm") or names & METRIC_REPORTS:
        return True
    return any(fnmatch.fnmatch(n, pat) for n in names for pat in METRIC_EXPORTS)


def enrich(assets: dict[str, dict]) -> None:
    """Attach measurements and quality scores the pipeline already computed."""
    for rec in assets.values():
        # dimensions, from whichever route measured this capture — and, in
        # lockstep, WHICH FILE those dimensions came off. The dashboard's
        # measuring tool has to load that exact geometry: the two meshes named
        # object_mesh.* are independent Poisson solves at different density
        # trims and disagree by inches.
        #
        # Every measurement the asset carries is COLLECTED here and one is
        # chosen after the walk, by _pick_dims. Deciding as we go is what let a
        # scene-scale number win on file order alone; read the trap there.
        # cad_report candidates go first because a cad_report always beat a
        # dims.json before (it overwrote whatever the walk had assigned).
        cad_cands: list[dict] = []
        fuse_cands: list[dict] = []
        for f in rec["files"]:
            full = REPO / f["path"]
            if f["name"] == "cad_report.json":
                try:
                    r = json.loads(full.read_text())
                    d = r.get("dims") or {}
                    # Offer only what this report actually measured. It used
                    # to assign unconditionally, so a cad_report carrying
                    # "dims": {} would wipe a good dims.json figure out of an
                    # asset that ships both.
                    if d.get("summary_in"):
                        cad_cands.append({
                            "dims": d["summary_in"],
                            # summary_in is already the unrounded figure here,
                            # unlike dims.json's summary — so dims_raw restates
                            # it and dims_quantized comes out false. Emitting
                            # dims_raw for BOTH routes is only half the fix: the
                            # consumer still could not tell these two apart
                            # without knowing which route ran, which is what the
                            # quantised/not flag derived below is for.
                            "dims_raw": d["summary_in"],
                            # No grid: this route does not snap to one.
                            "quantum_in": None,
                            # bbox_mm is the same box in millimetres, so it
                            # rides with the figure it restates rather than
                            # being assigned on its own — publishing it from a
                            # report whose geometry we do not ship would leak
                            # the unverifiable number back in through a second
                            # field.
                            "dims_mm": d.get("bbox_mm"),
                            "measure": _measure_from_cad(rec, r),
                        })
                    if r.get("frame"):
                        rec["frame"] = r["frame"]
                except Exception:
                    pass
            elif f["name"] == "dims.json":
                try:
                    o = json.loads(full.read_text()).get("object") or {}
                    aabb = o.get("aabb") or {}
                    if aabb.get("summary"):
                        fuse_cands.append({
                            "dims": aabb.get("summary"),
                            "dims_raw": aabb.get("summary_raw"),
                            # The grid `summary` was snapped to (0.25 in), read
                            # off the file that did the snapping rather than
                            # hardcoded here — one number, one owner.
                            "quantum_in": o.get("quantum_inches"),
                            "dims_mm": None,
                            "measure": _measure_from_dims(rec, o),
                            # Carried on the candidate rather than stamped on
                            # the record here: an asset that also ships a
                            # cad_report may never reach this figure at all, and
                            # a dispute about a number nobody publishes would be
                            # a warning against nothing.
                            "disputed": _fuse_dispute(o, full),
                        })
                except Exception:
                    pass
            elif f["name"] == "sim_export.json":
                try:
                    s = json.loads(full.read_text())
                    rec["mass_kg"] = s.get("mass_kg")
                    rec["mass_source"] = s.get("mass_source")
                except Exception:
                    pass
            elif f["name"] == "route_decision.json":
                try:
                    rec["route"] = json.loads(full.read_text()).get("route")
                except Exception:
                    pass
        pick = _pick_dims(cad_cands + fuse_cands)
        if pick:
            rec["dims"] = pick["dims"]
            # Make the figure self-describing. dims_raw alone was inert: it is
            # the same string as dims on the CAD route and a different one on
            # the fuse route, so a consumer holding only the manifest could not
            # tell "this IS the measurement" from "this is 0.25 in-rounded" —
            # and printing the second as the first is the whole failure mode.
            # Derived by comparison, so it cannot disagree with what shipped.
            raw = pick.get("dims_raw")
            if raw:
                rec["dims_raw"] = raw
                rec["dims_quantized"] = raw != pick["dims"]
            # Only meaningful once we know rounding actually happened, and only
            # ever a rounding grid — never present it as the precision.
            q = pick.get("quantum_in")
            if (rec.get("dims_quantized") and isinstance(q, (int, float))
                    and not isinstance(q, bool) and q > 0):
                rec["dims_quantum_in"] = q
            if pick["dims_mm"]:
                rec["dims_mm"] = pick["dims_mm"]
            # The figure ships WITH its refutation rather than being dropped.
            # Silence would leave the card looking like an ordinary scan that
            # simply published no size, and would tell neither the customer nor
            # the operator that a capture came back unusable. dimsClaim() turns
            # this into "not safe to quote" plus the sentence, above the fold, on
            # the index card and in the viewer's banner alike — so the number is
            # visible and unquotable everywhere instead of absent here and
            # believed by whatever reads the manifest directly.
            if pick.get("disputed"):
                rec["dims_disputed"] = pick["disputed"]
            # Resolved to a URL in publish(), once the file has one.
            rec["measure_src"] = pick["measure"]
        # Contract with the web app: `metric: false` means this geometry has no
        # real-world scale. Omitted means metric, which is the normal case, so
        # the flag only ever appears where it stops a lie being printed.
        # Computed from the files on disk, not from what uploaded, so a marker
        # file that fails to PUT cannot make a normalized mesh look metric.
        if not _has_metric_scale(rec):
            rec["metric"] = False
            # ...and then there are no dimensions either. Suppressing only
            # `measure` left inches sitting in the manifest for an asset the
            # web app has just been told has no scale, and main()'s summary
            # printed the two on consecutive lines: the figure, then "NOT
            # METRIC — normalized mesh, no dimensions published". A number
            # nothing can be measured against is the thing this flag exists to
            # prevent, so it goes with the flag.
            for k in DIMENSION_FIELDS + ("measure_src",):
                rec.pop(k, None)
        # quality, if the splat was rendered against ground truth
        for src in rec["sources"]:
            sheet = REPO / src / "render_check" / "contact_sheet.png"
            if sheet.is_file():
                rec["has_render_check"] = True


class PublishAbort(RuntimeError):
    """Stop before writing anything — the safe state is the one already live."""


class ManifestFetchError(PublishAbort):
    """The previously published manifest could not be established."""


def uploader_identity(uploader) -> str:
    """Everything about a destination that decides the URLs it hands back.

    The ledger is keyed on the blob pathname, so two runs writing the same
    pathname to *different destinations* are indistinguishable unless this
    string says otherwise. That is not theoretical: the head used to be
    (version, store, prefix, access), and LocalCopyUploader has no store id, so
    `--dest-dir deployA --base-url /A` and `--dest-dir deployB --base-url /B`
    shared one head. Run 2 hit run 1's cache, skipped all 13 files, wrote a
    manifest full of /A/… URLs and copied nothing into deployB — a dashboard of
    404s reported as "skipped 13 unchanged".
    """
    dest_dir = getattr(uploader, "dest_dir", None)
    if dest_dir is not None:            # LocalCopyUploader
        return (f"local:{os.path.abspath(dest_dir)}"
                f"|{getattr(uploader, 'base_url', '')}")
    # VercelBlobUploader: the store id, plus prefix/access which the head still
    # carries separately. A PresignedPutUploader mints URLs from a caller
    # closure we cannot inspect, so it gets no stable identity and therefore
    # never shares a ledger with anything (including a previous run of itself).
    store = str(getattr(uploader, "store_id", "") or "")
    if store:
        return f"vercel:{store}"
    return f"{type(uploader).__name__}:{id(uploader)}"


def local_root_of(uploader) -> Path | None:
    """The directory a local uploader writes into, if it is one."""
    dest_dir = getattr(uploader, "dest_dir", None)
    return Path(dest_dir) if dest_dir is not None else None


class UploadLedger:
    """What is already in the store, so an unchanged file is not re-PUT.

    Keyed on the blob **destination pathname**, because that string is what
    decides overwrite identity in the store — anything else can drift from
    reality. A hit needs the size, the content hash *and* a non-empty recorded
    URL to agree; a half-written ledger entry must not silently publish a link
    to nothing.

    The head carries `dest` (see uploader_identity) as well as prefix/access:
    a ledger is only ever consulted for the exact destination that filled it.

    Lives beside make_preview's thumbnails in .publish_cache/, one file per
    blob prefix, and is rewritten after every asset: a crash 300 MB into a run
    must not cost the whole run.

    `trust_hits=False` (--force-upload) turns off *reads* only. The ledger is
    still written, because the run that re-PUTs everything is precisely the run
    whose result the cache should be repaired from.
    """

    # 2: heads gained `dest`. A v1 head cannot be upgraded in place — it cannot
    # say which local directory its URLs point at — so it is discarded and the
    # next run re-uploads. One re-upload beats a manifest of dead links.
    VERSION = 2

    def __init__(self, path: Path, dest: str, prefix: str, access: str,
                 local_root: Path | None = None,
                 trust_hits: bool = True) -> None:
        self.path = path
        self.head = {"version": self.VERSION, "dest": dest,
                     "prefix": prefix, "access": access}
        self.local_root = local_root
        self.trust_hits = trust_hits
        self.files: dict[str, dict] = {}
        self.manifest_url: str | None = None
        try:
            doc = json.loads(path.read_text())
        except Exception:
            return
        # A different destination/prefix/access means none of those URLs are ours.
        if all(doc.get(k) == v for k, v in self.head.items()):
            self.files = doc.get("files") or {}
            self.manifest_url = doc.get("manifest_url") or None

    def hit(self, dest: str, local: Path, size: int) -> str | None:
        """The recorded URL if the store already holds this exact content."""
        if not self.trust_hits:
            return None
        e = self.files.get(dest)
        if not e or not e.get("url") or e.get("bytes") != size:
            return None
        if self.local_root is not None:
            # Emptying the destination is routine for the local uploader
            # (web/public/assets is gitignored; .publish_cache/ is not), which
            # leaves a ledger swearing 391 MB is published against an empty
            # directory. A remote store cannot be checked this cheaply, but a
            # local one is one stat.
            try:
                if (self.local_root / dest).stat().st_size != size:
                    return None
            except OSError:
                return None
        try:
            if e.get("sha256") != sha256_file(local):
                return None
        except OSError:
            return None
        return e["url"]

    def record(self, dest: str, local: Path, size: int, url: str) -> None:
        try:
            digest = sha256_file(local)
        except OSError:
            return
        self.files[dest] = {"sha256": digest, "bytes": size, "url": url,
                            "uploaded": time.time()}

    def save(self) -> None:
        doc = dict(self.head)
        doc["manifest_url"] = self.manifest_url
        doc["files"] = self.files
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(doc, indent=1))
        except OSError as e:      # a lost cache costs time, never correctness
            print(f"    !! upload cache not saved: {e}", file=sys.stderr)


def derive_manifest_url(token: str, prefix: str, access: str) -> str | None:
    """Best guess at where the last manifest was published.

    A read-write token is `vercel_blob_rw_<store>_<secret>` (blob.py reads the
    store id back out of it the same way), and reads of a store go to
    `<store>.<access>.blob.vercel-storage.com`. Only a guess — the authority is
    the URL the uploader returned last run, which is why the ledger keeps it,
    and --manifest-url overrides both.
    """
    if not token or token.count("_") < 4:
        return None
    store = token.split("_")[3]
    if store.startswith("store_"):
        store = store[len("store_"):]
    if not store:
        return None
    kind = "public" if access == "public" else "private"
    tail = f"{prefix.strip('/')}/{MANIFEST_NAME}" if prefix.strip("/") else MANIFEST_NAME
    return f"https://{store.lower()}.{kind}.blob.vercel-storage.com/{tail}"


def fetch_remote_manifest(url: str, token: str, timeout: float = 30.0) -> dict | None:
    """The currently published manifest, or None if there is not one yet.

    `?cache=0` is not optional and is the whole reason this can be trusted:
    the blob CDN can hand back a *previous* version of an overwritten stable
    pathname (web/lib/blob.ts says the same thing for the site's own reads).
    Merging onto a stale base at a 10-minute republish cadence would silently
    drop whatever the previous tick published — exactly the bug merge exists to
    prevent. Anything but 200 or 404 raises: "unknown prior state" must abort
    the run, not quietly become "no prior state".
    """
    import urllib.error
    import urllib.request
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    q["cache"] = "0"
    target = urlunparse(u._replace(query=urlencode(q)))
    headers = {"authorization": f"Bearer {token}"} if token else {}
    try:
        with urllib.request.urlopen(
                urllib.request.Request(target, headers=headers), timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return None
        raise ManifestFetchError(f"HTTP {e.code} {e.reason} reading {url}") from e
    except Exception as e:
        raise ManifestFetchError(f"{type(e).__name__}: {e} reading {url}") from e
    try:
        doc = json.loads(raw)
    except Exception as e:
        raise ManifestFetchError(f"published manifest is not JSON: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
        raise ManifestFetchError("published manifest has no 'assets' list")
    return doc


def merge_manifests(base: dict, fresh: dict, failed_ids: set[str]) -> dict:
    """Fold this run's assets into the published catalogue.

    Whole-record replacement, not a per-file union: a rerun can legitimately
    drop files (--include-raw toggled off) and a union would resurrect dead
    URLs. Assets this run did not touch are carried forward byte for byte —
    their blob pathnames are stable and still resolve.
    """
    merged: dict[str, dict] = {}
    for a in base.get("assets", []):
        if isinstance(a, dict) and a.get("id"):
            merged[a["id"]] = a
    base_ids = set(merged)

    new = replaced = held = 0
    for rec in fresh["assets"]:
        old = merged.get(rec["id"])
        if old is None:
            merged[rec["id"]] = rec
            new += 1
        elif rec["id"] in failed_ids:
            # A partial upload must never overwrite a complete published
            # record; publish() keeps going after a per-file failure, so the
            # record it built is simply missing those files.
            held += 1
            print(f"  holding published {rec['id']} — this run had upload failures for it")
        elif float(rec.get("updated") or 0) >= float(old.get("updated") or 0):
            merged[rec["id"]] = rec
            replaced += 1
        else:
            held += 1
            print(f"  holding published {rec['id']} — it is newer than what we built")

    if base_ids - set(merged):
        # Unreachable by the loop above, so reaching it means a bug. Dropping
        # an asset off a customer dashboard must be an explicit act.
        raise PublishAbort(
            "merge would remove "
            f"{', '.join(sorted(base_ids - set(merged)))} from the dashboard")

    # Newest first: web/app/page.tsx renders the array in manifest order.
    out = sorted(merged.values(), key=lambda r: -float(r.get("updated") or 0))
    carried = len(base_ids - {r["id"] for r in fresh["assets"]})
    print(f"merged: {carried} carried forward, {replaced} replaced, {new} new"
          + (f", {held} held" if held else ""))

    doc = dict(fresh)
    doc["assets"] = out
    # `or []` / `or 0`, not a default argument: a carried-forward record can
    # carry the key with a null value, and `for f in None` would crash here —
    # after every upload has already landed, which is the same
    # publish-then-die failure the summary block at the end of main() had.
    total = sum(f.get("bytes") or 0 for r in out for f in (r.get("files") or []))
    # Recomputed over the MERGED set — the index page prints these, so stats
    # left at this run's numbers put a visible lie in the header. `uploaded`
    # and `failures` stay run-local: they answer "what did this tick do".
    doc["stats"] = dict(fresh["stats"],
                        assets=len(out),
                        files=sum(len(r.get("files") or []) for r in out),
                        bytes=total, size=human(total))
    return doc


def publish(assets: dict, uploader, dry_run: bool, verbose: bool,
            ledger: UploadLedger | None = None) -> tuple[dict, dict[str, list[str]]]:
    """Upload every file, rewriting each record with its public URL.

    Returns the manifest and {asset id: [dest pathnames that failed]}. The
    merge needs the ids to know which records are only partial, and main()
    needs the counts to report a partial publish (rc 5) precisely enough that
    the caller knows what to retry.
    """
    sent = skipped = 0
    out: list = []
    failed: dict[str, list[str]] = {}
    cache = REPO / ".publish_cache"
    for rec in sorted(assets.values(), key=lambda r: -r["updated"]):
        measure_src = rec.pop("measure_src", None)
        if rec.get("preview_src"):
            thumb = make_preview(rec.pop("preview_src"), cache)
            if thumb:
                if dry_run or uploader is None:
                    rec["preview"] = "/" + thumb.relative_to(REPO).as_posix()
                else:
                    dest = f"{rec['id']}/preview.jpg"
                    size = thumb.stat().st_size
                    known = ledger.hit(dest, thumb, size) if ledger else None
                    if known:
                        rec["preview"] = known
                        skipped += 1
                    else:
                        try:
                            rec["preview"] = uploader.upload(str(thumb), dest)
                            sent += size
                            if ledger:
                                ledger.record(dest, thumb, size, rec["preview"])
                        except Exception as e:
                            # Counts as a failed file: the card would otherwise
                            # publish with no image at all and the caller would
                            # mark it done, so the thumbnail would never come
                            # back until the source files happened to change.
                            print(f"    !! preview {rec['id']}: {e}", file=sys.stderr)
                            failed.setdefault(rec["id"], []).append(dest)
        files = []
        for f in rec["files"]:
            if dry_run or uploader is None:
                f["url"] = "/" + f["path"]
            else:
                dest = f"{rec['id']}/{f['group']}/{f['rel']}"
                local = REPO / f["path"]
                known = ledger.hit(dest, local, f["bytes"]) if ledger else None
                if known:
                    f["url"] = known
                    skipped += 1
                    if verbose:
                        print(f"    = {dest}  unchanged")
                    files.append(f)
                    continue
                try:
                    f["url"] = uploader.upload(str(local), dest)
                    sent += f["bytes"]
                    if ledger:
                        ledger.record(dest, local, f["bytes"], f["url"])
                    if verbose:
                        print(f"    ↑ {dest}  {f['size']}")
                except Exception as e:  # keep going; one bad file is not fatal
                    print(f"    !! {dest}: {type(e).__name__}: {str(e)[:200]}",
                          file=sys.stderr)
                    failed.setdefault(rec["id"], []).append(dest)
                    continue
            files.append(f)
        rec["files"] = files
        rec["updated_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                           time.localtime(rec["updated"]))
        # The geometry whose dimensions this page prints. Resolved here and not
        # in enrich() because only now does that file have a URL — and if it
        # failed to upload it is not in `files`, so the field is simply absent
        # rather than pointing at a 404.
        # `metric: false` means there is no file whose numbers mean anything,
        # so there is nothing to point a measuring tool at (contract with the
        # web app: no measure field for a non-metric asset, ever).
        if measure_src and rec.get("metric") is not False:
            hit = next((g for g in files if g["path"] == measure_src["path"]), None)
            if hit:
                rec["measure"] = {"rel": hit["rel"], "url": hit["url"],
                                  "name": hit["name"], "source": measure_src["source"],
                                  "bytes": hit["bytes"]}
            else:
                # That file failed to upload, so the browser's measuring tool
                # has nothing to load — and _pick_dims' rule is that a figure
                # may only be printed when the geometry behind it is a file the
                # customer can download and re-measure. Dropping `measure` on
                # its own left the inches on the card with nothing to check them
                # against, exactly as the metric:false path above refuses to do.
                # The run still exits rc 5 and the retry heals it, but until
                # then this record goes out with no number rather than an
                # unverifiable one — and merge_manifests cannot save a
                # brand-new asset here, since it only HOLDS the published
                # record for ids the base already carries.
                for k in DIMENSION_FIELDS:
                    rec.pop(k, None)
        # what this asset can be opened in, for the dashboard filter
        rec["targets"] = sorted({t["target"] for f in files for t in f["targets"]})
        if files:
            out.append(rec)
        else:
            # Every file of this asset failed: the record left is {"files": [],
            # "targets": [], "preview": null} — a card that offers the customer
            # no download, no format list and nothing to measure. The run-wide
            # "all uploads failed" guard in main() does NOT catch it, because it
            # tests this run's total: publish one healthy asset alongside and
            # stats['files'] is non-zero. And under --merge an id the base does
            # not carry takes the "new asset" branch, so the dead card is added
            # to the dashboard rather than merely failing to update. Same rule
            # as the run-wide guard, applied per asset: no URL of any kind means
            # there is nothing to publish. The id is already in `failed`, so the
            # run still exits rc 5 and the caller retries it — and the "!!" line
            # naming the id is what tools/autopublish.py greps to keep it out of
            # its published ledger.
            print(f"    !! {rec['id']}: every file failed to upload — asset NOT "
                  f"published (it would be an empty card)", file=sys.stderr)
        if ledger:
            ledger.save()

    # Counted over what is actually IN the manifest, never over what we set out
    # to send. The old total summed every candidate file *before* attempting
    # its upload, so a run that lost 2 of 13 PUTs published "11 files, 17.4 MB"
    # for 7.5 MB of listed files — and web/app/page.tsx prints that size as the
    # dashboard headline. bytes and files must describe the same set.
    kept = [f for r in out for f in r["files"]]
    total = sum(f["bytes"] for f in kept)
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "targets": TARGETS,
        "assets": out,
        "stats": {"assets": len(out),
                  "files": len(kept),
                  "bytes": total, "size": human(total),
                  "uploaded": human(sent) if sent else None,
                  "skipped": skipped,
                  "failures": sum(len(v) for v in failed.values())},
    }, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--uploader", choices=["none", "local", "vercel"], default="vercel")
    ap.add_argument("--dest-dir", default=str(REPO / "web" / "public" / "assets"),
                    help="local uploader: where to copy")
    ap.add_argument("--base-url", default="/assets",
                    help="local uploader: URL prefix those files are served at")
    ap.add_argument("--token", default=os.environ.get("BLOB_READ_WRITE_TOKEN"),
                    help="vercel uploader: blob RW token (or $BLOB_READ_WRITE_TOKEN)")
    ap.add_argument("--prefix", default="dashboard", help="vercel blob path prefix")
    ap.add_argument("--only", action="append", default=[], metavar="ID",
                    help="publish just these assets (substring match, "
                         "repeatable) — e.g. --only DC22F084 --only 76B73843")
    ap.add_argument("--access", choices=["public", "private"], default="public",
                    help="blob access level; MUST match how the store was "
                         "created or the API returns 400")
    ap.add_argument("--include-raw", action="store_true",
                    help="also ship the ~250 MB raw gaussian PLYs")
    ap.add_argument("--manifest-out", default=str(REPO / "web" / "public" / MANIFEST_NAME))
    ap.add_argument("--merge", dest="merge", action="store_true", default=None,
                    help="fold this run into the published manifest instead of "
                         "replacing it (default ON whenever --only is used)")
    ap.add_argument("--no-merge", dest="merge", action="store_false",
                    help="replace the published manifest with just this run — "
                         "drops every asset not in it")
    ap.add_argument("--manifest-url", default=os.environ.get("DASHBOARD_MANIFEST_URL"),
                    help="where the published manifest lives (or "
                         "$DASHBOARD_MANIFEST_URL); otherwise the URL the last "
                         "run recorded, else derived from the token + --prefix")
    ap.add_argument("--force-upload", action="store_true",
                    help="ignore the unchanged-file cache and re-PUT everything "
                         "(the cache is still rewritten from the result)")
    ap.add_argument("--first-publish", action="store_true",
                    help="accept a 404 from a GUESSED manifest URL as 'nothing "
                         "published yet' — say this only when the dashboard is "
                         "genuinely empty, it publishes this run as the whole "
                         "catalogue")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    # --only without merge is the destructive combination: it publishes a
    # manifest containing only those assets over the one listing all of them.
    # Auto-enabled only where there is a remote manifest to merge onto — the
    # local uploader has no published catalogue and must not start demanding a
    # URL for commands that worked yesterday.
    merge = args.merge
    if merge is None:
        merge = bool(args.only) and (args.uploader == "vercel"
                                     or bool(args.manifest_url))

    assets = gather(args.include_raw)
    enrich(assets)
    if args.only:
        wanted = [o.upper() for o in args.only]
        # Match BOTH ways round. Asset keys come from scan_key(), which reduces a
        # capture to its bare id — "CrateScan_7C3DD25E" becomes "7C3DD25E" — while
        # recon3 passes the full run name to --only. A one-directional
        # `w in k` test therefore never matched any CrateScan-named capture, and
        # because recon3 treats a publish failure as fatal it aborted the run
        # before the customer-facing page stage ever ran.
        def _hit(key: str, w: str) -> bool:
            k = key.upper()
            return w in k or k in w

        assets = {k: v for k, v in assets.items()
                  if any(_hit(k, w) for w in wanted)}
        missing = [o for o, w in zip(args.only, wanted)
                   if not any(_hit(k, w) for k in assets)]
        if missing:
            print(f"--only matched nothing for: {', '.join(missing)}",
                  file=sys.stderr)
    if not assets:
        print("no publishable assets found", file=sys.stderr)
        return RC_NOTHING_PUBLISHED

    uploader = None
    if not args.dry_run and args.uploader != "none":
        from assetpipe.integrations.blob import make_uploader
        if args.uploader == "vercel" and not args.token:
            print("no blob token — set BLOB_READ_WRITE_TOKEN or use "
                  "--uploader local", file=sys.stderr)
            return RC_USAGE
        uploader = make_uploader(
            args.uploader, dest_dir=args.dest_dir, base_url=args.base_url,
            token=args.token, prefix=args.prefix, access=args.access)

    ledger = None
    if uploader is not None:
        # --force-upload suppresses cache *reads*, it does not switch the cache
        # off. Leaving ledger=None here meant the one command reached for when
        # the cache has gone wrong was also the only one that never repaired
        # it, and it silently dropped the recorded manifest_url — demoting the
        # next merge from the URL the store actually returned to
        # derive_manifest_url's guess.
        ledger = UploadLedger(REPO / ".publish_cache" / f"{args.prefix.strip('/') or 'root'}.json",
                              dest=uploader_identity(uploader),
                              prefix=args.prefix, access=args.access,
                              local_root=local_root_of(uploader),
                              trust_hits=not args.force_upload)

    print(f"{len(assets)} asset(s)")
    # Before a byte moves: an untargeted file is dead weight in the store and
    # invisible on the site, and this is the last place it can be caught by
    # someone who can fix it rather than by a customer who cannot.
    check_target_coverage(assets)
    manifest, failed = publish(assets, uploader, args.dry_run, args.verbose,
                               ledger)

    # A manifest listing zero files is worse than no manifest: it silently
    # replaces a good one and renders a catalogue of broken links. If every
    # upload failed, stop before writing. Evaluated on THIS RUN's counts,
    # before the merge — merged totals would let a run where every upload
    # failed sail through on the strength of the assets it carried forward.
    n_failed = manifest["stats"].get("failures", 0)
    if uploader is not None and manifest["stats"]["files"] == 0:
        print(f"\nALL {n_failed} uploads failed — manifest NOT written "
              f"(the previous one is left intact)", file=sys.stderr)
        return RC_NOTHING_PUBLISHED

    if merge:
        # Where the URL came from decides what a 404 means, so track it.
        # "Explicit" and "recorded" URLs are authoritative: a 404 there really
        # is "nothing published yet". A DERIVED url is a guess from the token,
        # and a guess that misses (wrong access, rotated token, different store
        # id encoding) also 404s — and merging onto {} would then publish this
        # run as the whole catalogue, deleting every other asset from the
        # customer dashboard. That is precisely what merge exists to prevent.
        url = args.manifest_url or (ledger.manifest_url if ledger else None)
        url_source = "explicit" if args.manifest_url else "recorded"
        if not url:
            url = derive_manifest_url(args.token or "", args.prefix, args.access)
            url_source = "derived"
        try:
            if not url:
                raise ManifestFetchError(
                    "no manifest URL to merge onto — pass --manifest-url "
                    "(or set $DASHBOARD_MANIFEST_URL)")
            base = fetch_remote_manifest(url, args.token or "")
            if base is None and url_source == "derived" and not args.first_publish:
                raise ManifestFetchError(
                    f"nothing at {url}, and that URL is only a GUESS derived "
                    f"from the token — a wrong guess 404s exactly like an "
                    f"empty dashboard does. Pass --manifest-url with the real "
                    f"one (or set $DASHBOARD_MANIFEST_URL), or pass "
                    f"--first-publish if the dashboard really is empty")
            if base is None:
                print(f"no manifest published yet at {url} ({url_source} URL) — "
                      f"first publish, nothing to merge")
                base = {"assets": []}
            else:
                print(f"merging onto {len(base.get('assets', []))} published "
                      f"asset(s) from {url}")
            manifest = merge_manifests(base, manifest, set(failed))
        except PublishAbort as e:
            # Unknown prior state. Publishing this run alone here is exactly
            # the truncation the guard above exists to prevent, so write
            # nothing, locally or remotely, and say why.
            if args.dry_run:
                print(f"merge preview unavailable: {e}", file=sys.stderr)
            else:
                print(f"\nMERGE ABORTED: {e}\n"
                      f"nothing was written — the published manifest is left "
                      f"intact. Fix the URL/token, or pass --no-merge to "
                      f"deliberately replace it with just this run.",
                      file=sys.stderr)
                return (RC_MERGE_UNKNOWN_STATE if isinstance(e, ManifestFetchError)
                        else RC_MERGE_UNSAFE)
    elif args.only and not args.dry_run and uploader is not None:
        # Name the cause that is actually in play. This used to read "--no-merge
        # with --only" whatever had happened, so an operator who passed neither
        # flag went hunting for a flag they never typed: merge auto-enables only
        # where there is a published catalogue to merge onto, which for anything
        # but --uploader vercel means "only if you said where it is". The
        # truncation being warned about is real in both cases; only the
        # attribution was wrong.
        cause = ("--no-merge was passed" if args.merge is False else
                 f"--merge was not passed, and with --uploader {args.uploader} "
                 f"and no --manifest-url it does not turn itself on")
        print(f"WARNING: --only without merge ({cause}) — the manifest about to "
              f"be written lists ONLY {', '.join(sorted(assets))}, and every "
              f"other asset disappears from the dashboard. Pass --merge with "
              f"--manifest-url <url> to fold this run into the published one.",
              file=sys.stderr)

    out = Path(args.manifest_out)
    if args.dry_run:
        # --dry-run must not clobber the record of what is actually published.
        # It used to write local "/" paths straight over web/public/manifest.json,
        # which is the one file that says what the live URLs are.
        out = out.with_suffix(".dry-run.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2))
    print(f"\nmanifest → {out}")

    if uploader is not None:
        try:
            url = uploader.upload(str(out), MANIFEST_NAME)
            print(f"manifest URL → {url}")
            if ledger:      # the authority for the next run's merge fetch
                ledger.manifest_url = url
                ledger.save()
            print("\nset this on the Vercel project so the site picks up new scans:")
            print(f"  NEXT_PUBLIC_MANIFEST_URL={url}")
        except Exception as e:
            print(f"!! manifest upload failed: {e}", file=sys.stderr)
            return RC_NOTHING_PUBLISHED

    s = manifest["stats"]
    print(f"\n{s['assets']} assets · {s['files']} files · {s['size']}"
          + (f" · uploaded {s['uploaded']}" if s["uploaded"] else "")
          + (f" · skipped {s['skipped']} unchanged" if s.get("skipped") else ""))
    for rec in manifest["assets"][:12]:
        # EVERY field here is read defensively, and the type is checked wherever
        # the read is indexed or measured. Half of these records were written by
        # a PREVIOUS build of this file and carried forward untouched by
        # merge_manifests, which only ever requires an `id`. rec['files'] was a
        # direct index among .get() neighbours, and a carried-forward record
        # without that key (a published {"id": …, "updated": …, "targets": […]})
        # raised KeyError here — AFTER every asset and the manifest had already
        # been uploaded and the local manifest written. `raise SystemExit(main())`
        # then never ran: the process died with a traceback and rc 1, which
        # tools/autopublish.py reads as "nothing recorded, will retry", so the
        # same successful publish repeated and escalated every ten minutes.
        # A summary line must never crash a good publish.
        files = rec.get("files")
        tgs = rec.get("targets")
        tg = " ".join(TARGETS.get(t, {}).get("icon", "·")
                      for t in (tgs if isinstance(tgs, list) else [])
                      if isinstance(t, str))
        print(f"  {str(rec.get('id') or '?'):12s} "
              f"{len(files) if isinstance(files, list) else 0:3d} files  {tg}  "
              f"{rec.get('dims') or ''}")
        if rec.get("metric") is False:
            print(f"               NOT METRIC — normalized mesh, no dimensions "
                  f"published")
        # The operator reads this list to decide whether a run went well, and a
        # disputed figure prints on the line above looking exactly like a good
        # one. Say it here or a bad capture ships looking like 26 healthy ones.
        disputed = rec.get("dims_disputed")
        if isinstance(disputed, str) and disputed.strip():
            print(f"               DO NOT QUOTE — {disputed.strip()}")
        m = rec.get("measure")
        if isinstance(m, dict):
            print(f"               measure → {m.get('rel') or m.get('name') or '?'}"
                  f"  ({m.get('source') or 'source not recorded'})")

    if failed:
        # rc 5, not 0. The merge held the previously published record for these
        # ids, so the manifest can be byte-identical to the last one and the run
        # still be a failure: a caller that read rc 0 here would mark the asset
        # published and never retry it, and one transient blob 5xx would strand
        # it forever. See the exit-code table in the module docstring.
        print(f"\nPARTIAL PUBLISH: {n_failed} file(s) failed to upload and are "
              f"absent from the manifest", file=sys.stderr)
        for aid, dests in sorted(failed.items()):
            print(f"  {aid}: {len(dests)} file(s) — "
                  f"{', '.join(dests[:4])}{' …' if len(dests) > 4 else ''}",
                  file=sys.stderr)
        print(f"these asset(s) are INCOMPLETE — do not record them as "
              f"published; re-run to retry (rc {RC_PARTIAL})", file=sys.stderr)
        return RC_PARTIAL
    return RC_OK


if __name__ == "__main__":
    raise SystemExit(main())
