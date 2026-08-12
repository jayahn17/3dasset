#!/usr/bin/env python3
"""One capture → three reconstructions → one dashboard asset.

    python tools/recon3.py <session_dir> --name book_20260805 [--stages ...]

The standard product pipeline settled on 2026-08-05: every capture gets a
Meshroom (AliceVision) photogrammetry mesh, a 3DGUT splat, and a TRELLIS
generative mesh, all gravity-aligned (Y up) and metric, collected into ONE
demo_out/<name>/ directory so tools/publish_dashboard.py groups them as a
single asset card.

Stages (each SKIPS when its output already exists; --force redoes):

    fuse      assetpipe object → object_asset/ (dims.json, object_mesh.glb)
              Native metric + gravity-aligned since the anchor-frame fix.
    exif      Stamp the manifest's K_color into the JPEGs as a 35mm-equivalent
              focal — AliceVision otherwise assumes 45° FOV (~50% focal error).
              ONE shared value, or it splits images into intrinsic groups.
    meshroom  meshroom_batch photogrammetry, JPG textures, largest-mesh-only.
    scale     Umeyama similarity from the SfM camera track onto the session's
              metric ARKit track. Yields scale AND the rotation into the
              gravity-aligned frame; the residual is the trust check.
    glb       texturedMesh.obj → <name>/meshroom_visual.glb (decimated, baked
              vertex colours, normals, upright, metres). The *_visual.glb name
              is what the publisher's DELIVERABLES allowlist matches.
    trellis   gen3d server → <name>/asset_trellis.glb + *_mm.stl, scaled from
              the fuse dims. GPU-exclusive (~10 GB): waits for free VRAM.
    gut       LichtFeld Studio by default (RECON3_TRAINER=3dgut opts out, and
              a LichtFeld failure falls back to it automatically). Same COLMAP
              export, same 62-property PLY, same metric/gravity frame — but
              MEASURED 20 min against 3DGUT's ~85 min on the koala, which is
              why this stage stopped being ~85% of a capture's wall clock.
              1M gaussians; tripling the cap bought 3 points of densification.
    page      interactive orbit+measure page on the dashboard: panel splat
              (cropped to the subject), a 3-card benchmark page via
              web/benchmarks/publish_benchmark.py, and an entry in the blob
              benchmarks index the homepage reads at request time — a new page
              appears with NO redeploy. This stage IS the guardrail: an output
              is not delivered until the user can orbit and measure it. →
              <name>/scene_gaussians.splat + meta. GPU-exclusive.
    publish   tools/publish_dashboard.py --only <name>  (private access).

HELD stages — in the pipeline, but only run when NAMED on --stages. Both are a
SECOND reconstruction of a capture that already has one, so by default they are
cost without delivery:

    gut3d     3DGUT, the trainer LichtFeld replaced (~85 min vs ~20). Writes
              recon_work/<name>_gut3d/ and gut3d_panel.splat, so a LichtFeld
              result is never overwritten and both can sit on the page together.
                  python tools/recon3.py <session> --name X --stages gut3d
    kiri      tools/kiri_oss.py — the open-source KIRI clone (BiRefNet mask →
              AliceVision → metric from the AR camera track). Writes to
              kiri_oss_out/<name>/, deliberately OUTSIDE demo_out/: its isolate
              stage keeps only the largest connected part, which cost the koala
              93% of its surface, so its output is NOT publishable unreviewed.
              Check validate.lidar_to_mesh coverage in kiri_oss_report.json.
                  python tools/recon3.py <session> --name X --stages kiri

GPU stages run strictly serial — this box has 16 GB and the OOM half-way
through a TRELLIS generate is exactly the failure this tool exists to prevent.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
MESHROOM = Path.home() / "Meshroom-2025.1.0" / "meshroom_batch"

# Below this, structure-from-motion cannot solve and Meshroom burns GPU minutes
# to fail. Measured captures that DID solve started at 18 images; the three
# uploads this guard exists for carried 1, 1 and 3.
MIN_SFM_IMAGES = 8

ALL_STAGES = ["fuse", "exif", "meshroom", "scale", "glb", "trellis", "gut",
              "gut3d", "kiri", "publish", "page"]

# HELD: in the pipeline, never run unless NAMED on --stages. Both are second
# reconstructions of a capture that already has one, so they are cost with no
# delivery — but keeping them as real stages (rather than loose scripts) means
# they share the GPU lock, the naming, and the parked-intermediate rules.
#
#   gut3d  3DGUT, the trainer LichtFeld replaced. ~85 min vs ~20. Trigger it to
#          A/B a capture, or when LichtFeld is suspected of a bad fit.
#   kiri   tools/kiri_oss.py — the open-source KIRI clone (BiRefNet mask →
#          AliceVision → metric via the AR track). Its `isolate` stage is known
#          to keep only the largest connected part, which cost koala 93% of its
#          surface, so its output is NOT publishable unmodified.
HELD_STAGES = {"gut3d", "kiri"}
DEFAULT_STAGES = [s for s in ALL_STAGES if s not in HELD_STAGES]


def blob_token() -> str:
    """The publish credential, from the environment or web/.env.local.

    Reading the file directly matters: on 2026-08-11 a `vercel env pull` blanked
    the value in web/.env.local, publish logged "not set — skipping" and the run
    exited 0. The pipeline reported success and shipped nothing for days. Two
    lessons are baked in here — recon3 finds the token however it was invoked,
    and a BLANK token is treated as missing, not as a value.
    """
    tok = (os.environ.get("BLOB_READ_WRITE_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        for line in (REPO / "web/.env.local").read_text().splitlines():
            if line.startswith("BLOB_READ_WRITE_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("  $ " + " ".join(str(c) for c in cmd[:6]) + (" …" if len(cmd) > 6 else ""))
    return subprocess.run([str(c) for c in cmd], **kw)


def free_vram_mb() -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
        used, total = (int(x) for x in out.split("\n")[0].split(","))
        return total - used
    except Exception:
        return 0


GPU_LOCK = Path("/tmp/recon3_gpu.lock")


class gpu_lock:
    """Cross-process GPU queue.

    Two recon3 runs waiting on 'no other compute process' would both see the
    GPU free in the same 60 s polling window and start together — the same
    race the VRAM threshold had, one level up. flock makes the queue real:
    whoever holds the lock owns the GPU stages; everyone else blocks in line.
    """
    def __enter__(self):
        import fcntl
        self.fh = open(GPU_LOCK, "w")
        log(f"  queueing for GPU lock ({GPU_LOCK}) …")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        log("  GPU lock acquired")
        return self

    def __exit__(self, *a):
        import fcntl
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


# A compute process this size or smaller is a desktop helper, not a trainer:
# it will not grow and will not OOM us. rustdesk --server sits on ~359 MB
# indefinitely, and the strict "no other compute process" rule treated that as
# a busy GPU — every GPU stage waited out its full 4-hour timeout and the
# pipeline stalled with the card 97% idle.
SMALL_PROC_MB = 1500


def other_compute_pids() -> list[str]:
    """PIDs of compute processes big enough to actually contend for the GPU."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout
        pids = []
        for line in out.splitlines():
            if not line.strip():
                continue
            pid, _, mem = line.partition(",")
            try:
                if int(mem.strip()) <= SMALL_PROC_MB:
                    continue
            except ValueError:
                pass
            pids.append(pid.strip())
        return pids
    except Exception:
        return []


# --------------------------------------------------------------------- trainer
LFS_BIN = Path.home() / "LichtFeld-Studio" / "build" / "LichtFeld-Studio"
LFS_ENV = Path.home() / "miniconda3" / "envs" / "lichtfeld"


def use_lichtfeld() -> bool:
    """LichtFeld is the default trainer; RECON3_TRAINER=3dgut opts out.

    Measured on this box, identical COLMAP input, 30k iters / 1M cap:
    LichtFeld 20 min vs 3DGUT ~85 min on the koala (4.3x). The exported PLY is
    the same 62-property 3DGS schema, assetpipe.scene.splat.load_ply reads it
    unchanged, and the world frame matches 3DGUT's to ~3 cm — so nothing
    downstream needs to know which trainer ran.
    """
    if os.environ.get("RECON3_TRAINER", "").lower() in ("3dgut", "3dgrut"):
        return False
    return LFS_BIN.is_file()


def train_lichtfeld(gdir: Path, iterations: int, max_gaussians: int) -> int:
    """Train on the COLMAP export already sitting in gdir/3dgrut_data.

    Two flags are load-bearing:

    --output-name export_last  watch_export_3dgut.py globs ONLY `export_last.ply`.
        Left at the default LichtFeld writes splat_30000.ply, the glob finds
        nothing, and the stage dies on its 30-minute timeout with no error.
    --centralize off           the shift is applied at load and NEVER undone at
        export, so enabling it silently and permanently destroys the ARKit
        metric origin. It is already the default; pinning it means a future
        default flip cannot corrupt a capture.
    """
    # LichtFeld resolves -d against ITS OWN cwd, not ours, so a relative gdir
    # fails with "[Path not found] <path>" naming a path that plainly exists.
    # recon3 always passes an absolute gdir; other callers do not.
    gdir = gdir.resolve()
    data = gdir / "3dgrut_data"
    if not (data / "sparse" / "0").is_dir():
        log(f"lichtfeld: no COLMAP export at {data}")
        return 1
    env = os.environ.copy()
    # SET, never prepend: LichtFeld embeds its own Python runtime
    # (liblfs_python_runtime.so), and assetpipe's torch/lib on the inherited
    # path loads the wrong libstdc++ into it.
    env["LD_LIBRARY_PATH"] = str(LFS_ENV / "lib")
    for k in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    cmd = [str(LFS_BIN), "--headless",
           "--data-path", str(data), "--output-path", str(gdir),
           "--output-name", "export_last",
           "--iter", str(iterations), "--max-cap", str(max_gaussians),
           "--centralize", "off"]
    log(f"lichtfeld: training {iterations} iters, cap {max_gaussians}")
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=str(gdir), env=env).returncode
    log(f"lichtfeld: rc={rc} in {time.time() - t0:.0f}s")
    return rc


def wait_for_vram(need_mb: int, timeout_s: int = 14400) -> bool:
    """Wait for the GPU to be EXCLUSIVELY ours.

    A free-memory threshold alone is a race, not serialization: training jobs
    GROW as they densify, so "10 GB free right now" can be 6 GB by the time
    this stage peaks — which is precisely how a TRELLIS generate OOM'd while a
    3DGUT run was mid-flight. So: no other SUBSTANTIAL compute process, AND the
    headroom, before a GPU stage may start.

    "Substantial" is the correction: the original rule was "no compute process
    at ALL", which a 359 MB rustdesk --server satisfies forever. See
    SMALL_PROC_MB — anything under it cannot grow into an OOM and is ignored.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        others = other_compute_pids()
        free = free_vram_mb()
        if not others and free >= need_mb:
            return True
        # Poll fast, log slow. The 60 s sleep cost a fixed ~60 s of dead time on
        # EVERY capture: `pkill gen3d` returns immediately but the server takes
        # a moment to release ~9 GB, so the first check almost always failed and
        # then slept a full minute for a condition met seconds later.
        if int(time.time() - t0) % 30 < 2:
            log(f"  waiting for GPU: {free} MB free, {len(others)} compute proc(s) running")
        time.sleep(2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", help="session/ dir with manifest.json")
    ap.add_argument("--name", required=True, help="project name = demo_out subdir = asset id")
    ap.add_argument("--stages", nargs="+", default=DEFAULT_STAGES, choices=ALL_STAGES,
                    help=f"held (run only when named): {' '.join(sorted(HELD_STAGES))}")
    ap.add_argument("--force", action="store_true", help="redo stages whose output exists")
    ap.add_argument("--iterations", type=int, default=30000)
    ap.add_argument("--max-gaussians", type=int, default=1_000_000)
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    session = Path(args.session).resolve()
    # Does this capture carry depth at all? An RGB-only session (app recorded no
    # depth, or the scan was cut short) cannot fuse and cannot seed 3DGUT, but
    # AliceVision and TRELLIS need only colour — so it must still DELIVER rather
    # than abort with nothing. Three real uploads were lost to a fatal fuse.
    try:
        _man = json.loads((session / "manifest.json").read_text())
        _fr = _man.get("keyframes") or _man.get("frames") or []
        _fr = [f for f in _fr if isinstance(f, dict)]   # some manifests list ids
        n_frames = len(_fr)
        n_depth = sum(1 for f in _fr if f.get("depth"))
    except Exception:
        n_frames = n_depth = 0
    has_depth = n_depth >= 3
    out = REPO / "demo_out" / args.name
    work = Path.home() / "meshroom_work" / args.name
    out.mkdir(parents=True, exist_ok=True)
    stages = [s for s in args.stages if not (s == "publish" and args.no_publish)]
    apy = Path.home() / "miniconda3/envs/assetpipe/bin/python"
    apy = apy if apy.is_file() else Path(PY)

    # ---- fuse -------------------------------------------------------------
    if "fuse" in stages:
        dims = out / "object_asset" / "dims.json"
        if dims.is_file() and not args.force:
            log("fuse: exists, skip")
        else:
            rc = run([apy, "-m", "assetpipe", "object", session,
                      "--out", out / "object_asset", "--max-frames", "10",
                      "--stride", "1"], cwd=REPO).returncode
            if rc != 0:
                # Not fatal. Without dims.json TRELLIS ships unscaled (it says
                # so itself) and the card is flagged non-metric — far better
                # than delivering nothing.
                log(f"fuse FAILED — continuing without metric dims "
                    f"({n_depth}/{n_frames} frames carry depth)")

    # ---- exif -------------------------------------------------------------
    imgs = work / "images_exif"
    if "exif" in stages:
        if imgs.is_dir() and any(imgs.glob("*.jpg")) and not args.force:
            log("exif: exists, skip")
        else:
            src = session / "keyframes"
            if not src.is_dir():
                src = session / "images"
            rc = run([apy, REPO / "tools/stamp_focal_exif.py", session, src, imgs]).returncode
            if rc != 0:
                log("exif FAILED"); return 1

    # ---- meshroom ---------------------------------------------------------
    mesh_obj = work / "output" / "texturedMesh.obj"
    if "meshroom" in stages:
        if mesh_obj.is_file() and not args.force:
            log("meshroom: exists, skip")
        else:
            if len(list(imgs.glob("*.jpg"))) < MIN_SFM_IMAGES:
                log(f"meshroom: skipped — {len(list(imgs.glob('*.jpg')))} images, "
                    f"SfM needs ~{MIN_SFM_IMAGES}")
                rc = 1
            else:
              with gpu_lock():
                if not wait_for_vram(4000):
                    log("meshroom: GPU never freed"); return 1
                shutil.rmtree(work / "cache", ignore_errors=True)
                shutil.rmtree(work / "output", ignore_errors=True)
                rc = run([MESHROOM, "-i", imgs, "-o", work / "output",
                          "--cache", work / "cache", "-p", "photogrammetry",
                          "--paramOverrides",
                          "Texturing:colorMapping.colorMappingFileType=jpg",
                          "MeshFiltering:keepLargestMeshOnly=True"],
                         cwd=MESHROOM.parent).returncode
            if rc != 0 or not mesh_obj.is_file():
                log("meshroom FAILED — no photogrammetry mesh; "
                    "scale/glb skipped, TRELLIS still runs")

    # ---- scale ------------------------------------------------------------
    xform = out / "meshroom_arkit_transform.json"
    if "scale" in stages and not mesh_obj.is_file():
        log("scale: skipped — no photogrammetry mesh")
    elif "scale" in stages:
        if xform.is_file() and not args.force:
            log("scale: exists, skip")
        else:
            # newest SfM cache belongs to the run we just did (or --force redid)
            sfms = sorted((Path("/tmp/MeshroomCache/StructureFromMotion")).glob("*/cameras.sfm"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not sfms:
                log("scale: no cameras.sfm found — mesh stays unscaled")
                sfms = [None]
            rc = 1 if sfms[0] is None else run(
                [apy, REPO / "tools/metric_scale_sfm.py", sfms[0], session,
                 "photo", xform]).returncode
            if rc != 0 or not xform.is_file():
                log("scale FAILED — mesh will be published unscaled")
                xform = None
            verdict = "" if xform is None else json.loads(xform.read_text()).get("verdict", "")
            if "TRUST" not in verdict:
                log(f"scale verdict: {verdict} — glb will still be written, dims must not be quoted")

    # ---- glb --------------------------------------------------------------
    vis = out / "meshroom_visual.glb"
    xform = xform if (xform is not None and xform.is_file()) else None
    if "glb" in stages and not mesh_obj.is_file():
        log("glb: skipped — no photogrammetry mesh")
    elif "glb" in stages:
        if vis.is_file() and not args.force:
            log("glb: exists, skip")
        else:
            rc = run([apy, REPO / "tools/obj_to_web_glb.py", mesh_obj, vis,
                      "300000"] + ([] if xform is None else [xform])).returncode
            if rc != 0:
                log("glb FAILED — photogrammetry mesh not published")

    # ---- trellis ----------------------------------------------------------
    if "trellis" in stages:
        glb = out / "asset_trellis.glb"
        if glb.is_file() and not args.force:
            log("trellis: exists, skip")
        else:
            sys.path.insert(0, str(REPO))
            import importlib
            q = importlib.import_module("tools.watch_3dgut_queue")
            with gpu_lock():
                if not wait_for_vram(9500):
                    log("trellis: GPU never freed"); return 1
                if not q._ensure_gen3d():
                    log("trellis: gen3d server failed"); return 1
                status = {"name": args.name, "ok": True, "mode": "object",
                          "session": str(session),
                          "quality": {"verdict": "partial", "usable_asset": True, "reasons": []}}
                decision = {"route": "trellis", "forced": "recon3 standard pipeline"}
                res = q._run_trellis(name=args.name, session=session, status=status,
                                     out_root=REPO / "demo_out", decision=decision,
                                     folder_id="", captures=REPO / "captures", dry_run=False)
                # the gen3d server holds ~9 GB — never leave it resident for the
                # next queued GPU stage to trip over
                subprocess.run(["pkill", "-f", "services/gen3d_server.py"], check=False)
            # _run_trellis writes to demo_out/<name>_trellis — fold into the project dir
            tdir = REPO / "demo_out" / f"{args.name}_trellis"
            if res == "trained" and tdir.is_dir():
                for f in tdir.iterdir():
                    if f.is_file():
                        shutil.copy2(f, out / f.name)
                # park the work dir OUTSIDE demo_out — publisher --only matches
                # by scan-key prefix, and a sibling <name>_trellis dir becomes
                # its own asset card next to the project it belongs to
                park = REPO / "recon_work"; park.mkdir(exist_ok=True)
                shutil.move(str(tdir), str(park / tdir.name)) if not (park / tdir.name).exists() else shutil.rmtree(tdir)
            if not glb.is_file():
                log("trellis FAILED — no generative mesh")

    # ---- gut --------------------------------------------------------------
    if "gut" in stages and not has_depth:
        log(f"gut: skipped — capture has no depth ({n_frames} frames); "
            "3DGUT seeds its point cloud from depth")
    elif "gut" in stages:
        splat = out / "scene_gaussians.splat"
        if splat.is_file() and not args.force:
            log("gut: exists, skip")
        else:
            # The dir keeps the _3dgut name whichever trainer runs: the `page`
            # stage and watch_export_3dgut both hard-code it, and renaming it
            # per-renderer silently yields no panel splat and no Splat card.
            gdir = REPO / "demo_out" / f"{args.name}_3dgut"
            with gpu_lock():
                if not wait_for_vram(9000):
                    log("gut: GPU never freed"); return 1
                # The COLMAP export is pure CPU but has to happen before either
                # trainer; --colmap-only keeps it cheap and shared.
                rc = run([apy, REPO / "tools/run_3dgut_from_session.py", session,
                          "--out", gdir, "--iterations", args.iterations,
                          "--max-gaussians", args.max_gaussians,
                          *(["--colmap-only"] if use_lichtfeld() else [])],
                         cwd=REPO).returncode
                if rc != 0:
                    log("gut: COLMAP export FAILED — no splat; "
                        "other results still publish")
                elif use_lichtfeld():
                    rc = train_lichtfeld(gdir, int(args.iterations),
                                         int(args.max_gaussians))
                    if rc != 0:
                        log("lichtfeld FAILED — falling back to 3DGUT")
                        rc = run([apy, REPO / "tools/run_3dgut_from_session.py",
                                  session, "--out", gdir,
                                  "--iterations", args.iterations,
                                  "--max-gaussians", args.max_gaussians],
                                 cwd=REPO).returncode
                        if rc != 0:
                            log("gut train FAILED — no splat; other results still publish")
            if rc == 0:
                rc = run([apy, REPO / "tools/watch_export_3dgut.py", gdir,
                          "--title", f"{args.name} 3DGUT", "--timeout-h", "0.5"],
                         cwd=REPO).returncode
                if rc != 0:
                    log("gut export FAILED — no splat; "
                        "other results still publish")
            for f in ("scene_gaussians.splat", "scene_gaussians.splat.meta.json"):
                if (gdir / f).is_file():
                    shutil.copy2(gdir / f, out / f)
            park = REPO / "recon_work"; park.mkdir(exist_ok=True)
            if not (park / gdir.name).exists():
                shutil.move(str(gdir), str(park / gdir.name))


    # ---- gut3d (HELD) ------------------------------------------------------
    # The old trainer, kept triggerable for A/B. Its own dir so a LichtFeld
    # result is never overwritten, and its own panel name so both can appear
    # side by side on the page ("*_panel.splat" is already in DELIVERABLES).
    if "gut3d" in stages:
        gdir3 = REPO / "recon_work" / f"{args.name}_gut3d"
        panel3 = out / "gut3d_panel.splat"
        if panel3.is_file() and not args.force:
            log("gut3d: exists, skip")
        else:
            with gpu_lock():
                if not wait_for_vram(9000):
                    log("gut3d: GPU never freed"); return 1
                rc = run([apy, REPO / "tools/run_3dgut_from_session.py", session,
                          "--out", gdir3, "--iterations", args.iterations,
                          "--max-gaussians", args.max_gaussians], cwd=REPO).returncode
            if rc != 0:
                log("gut3d FAILED"); return 1
            ply3 = next(iter(sorted(gdir3.glob("**/export_last.ply"))), None) \
                or (gdir3 / "scene_gaussians.ply")
            if ply3.is_file():
                gpy3 = Path.home() / "miniconda3/envs/3dgrut/bin/python"
                run([gpy3 if gpy3.is_file() else apy, REPO / "tools/ply_to_splat.py",
                     ply3, "--out", panel3, "--max", "400000",
                     "--min-opacity", "0.06"], cwd=REPO)
            log(f"gut3d: {panel3.name}" if panel3.is_file() else "gut3d: no splat produced")

    # ---- kiri (HELD) -------------------------------------------------------
    # RGB-only reconstruction of the same capture, for comparison against the
    # depth-fused one. Deliberately writes OUTSIDE demo_out/: its isolate stage
    # can return a fragment, and an unvetted fragment must not become a card.
    if "kiri" in stages:
        kdir = REPO / "kiri_oss_out" / args.name
        if (kdir / "kiri_oss_report.json").is_file() and not args.force:
            log("kiri: exists, skip")
        else:
            rc = run([apy, REPO / "tools/kiri_oss.py", session, "--out", kdir],
                     cwd=REPO).returncode
            if rc != 0:
                log("kiri FAILED (held stage — pipeline continues)")
            else:
                log(f"kiri: {kdir}  — review coverage in kiri_oss_report.json "
                    "before publishing anything from it")

    # ---- publish ----------------------------------------------------------
    if "publish" in stages:
        made = [f.name for f in sorted(out.glob("*.glb")) ] + \
               [f.name for f in sorted(out.glob("*.splat"))]
        if not made:
            log("publish: nothing was reconstructed — no card to publish")
            log(f"DONE {args.name}: 0 reconstructions")
            return 1
        log("publish: " + ", ".join(made))
        tok = blob_token()
        if not tok:
            log("publish FAILED — no blob token. It is missing or BLANK in "
                "web/.env.local (a `vercel env pull` empties it). Restore with "
                "`cd web && vercel env pull` and confirm the value is non-empty; "
                "nothing reached the dashboard.")
            return 1
        else:
            os.environ["BLOB_READ_WRITE_TOKEN"] = tok
            rc = run([apy, REPO / "tools/publish_dashboard.py", "--uploader", "vercel",
                      "--access", "private", "--only", args.name], cwd=REPO).returncode
            if rc != 0:
                log("publish FAILED"); return 1

    # ---- page -------------------------------------------------------------
    if "page" in stages:
        if not blob_token():
            log("page FAILED — no blob token (see publish)"); return 1
        else:
            os.environ["BLOB_READ_WRITE_TOKEN"] = blob_token()
            import urllib.parse
            import urllib.request
            # panel splat: the viewing copy — cropped to the subject, thinned
            panel = out / "scene_panel.splat"
            ply = REPO / "recon_work" / f"{args.name}_3dgut" / "scene_gaussians.ply"
            meta_p = out / "scene_gaussians.splat.meta.json"
            if ply.is_file() and (not panel.is_file() or args.force):
                crop = 0.9
                if meta_p.is_file():
                    try:
                        crop = max(0.75, 3.0 * float(json.loads(meta_p.read_text())["radius_m"]))
                    except Exception:
                        pass
                gpy = Path.home() / "miniconda3/envs/3dgrut/bin/python"
                run([gpy if gpy.is_file() else apy, REPO / "tools/ply_to_splat.py", ply,
                     "--out", panel, "--max", "400000", "--min-opacity", "0.06",
                     "--crop-radius", f"{crop:.2f}"])
            # spec: one card per artifact that exists
            cards = []
            if (out / "meshroom_visual.glb").is_file():
                res_note = ""
                xf = out / "meshroom_arkit_transform.json"
                av_metric = False
                if xf.is_file():
                    x = json.loads(xf.read_text())
                    res_note = f" (residual {x['residual_mean_m']*1000:.1f} mm, {x['verdict']})"
                    av_metric = True
                cards.append({"id": f"av_{args.name}", "title": "Photo Mesh",
                    "method": "built from your photos",
                    "glb": str(out / "meshroom_visual.glb"), "input": "posed keyframes",
                    "views": "-", "time": "-",
                    "scale": f"METRIC{res_note}" if av_metric else "NOT MEASURED — no scale fit",
                    "note": "True to life. Best for measuring." if av_metric
                            else "Shape only. Sizes here are not real."})
            if (out / "asset_trellis.glb").is_file():
                mm = ""
                dm = out / "dims_mm.json"
                if dm.is_file():
                    try: mm = json.loads(dm.read_text())["summary_mm"]
                    except Exception: pass
                nv = str(n_frames) if n_frames else "-"
                cards.append({"id": f"tr_{args.name}", "title": "AI Mesh",
                    "method": "AI-completed shape", "glb": str(out / "asset_trellis.glb"),
                    "input": f"{nv} view{'' if nv == '1' else 's'}", "views": nv,
                    "time": "~40 s",
                    # Without dims_mm.json the GLB is UNIT-NORMALISED, and the viewer
                    # reads its units as metres. Saying "metric via fuse dims " (the
                    # empty interpolation this used to emit) invites a measurement
                    # that is meaningless.
                    "scale": f"metric via fuse dims {mm}" if mm
                             else "NOT MEASURED — shape only, do not measure",
                    "note": "Complete shape. Sizes are estimates." if mm
                            else "Complete shape. Proportions only — no real sizes."})
            if panel.is_file():
                cards.append({"id": f"gut_{args.name}", "title": "Splat",
                    "method": "photoreal view", "splat": str(panel),
                    "input": "all posed keyframes", "views": "-", "time": "~20 min",
                    "scale": "METRIC (ARKit poses)",
                    "note": "Photoreal. Spin it — no tape measure here."})
            if not cards:
                log("page: no artifacts to show — skipping")
            else:
                any_metric = any("NOT MEASURED" not in c["scale"] for c in cards)
                spec = {"title": args.name.replace("_", " ").title(),
                        "dest": f"benchmark/{args.name}",
                        "lede": ("Drag to spin, scroll to zoom. Turn Measure on and "
                                 "click two points to read inches." if any_metric else
                                 "Drag to spin, scroll to zoom. This scan had no depth, "
                                 "so it shows shape only — the measure tool will not "
                                 "give real sizes."),
                        "cards": cards}
                spec_p = REPO / "web/benchmarks/specs" / f"auto_{args.name}.json"
                spec_p.write_text(json.dumps(spec, indent=1))
                env = dict(os.environ)
                env.setdefault("DASHBOARD_SITE", "https://web-jayahn-3302s-projects.vercel.app")
                rc = subprocess.run([str(apy), str(REPO / "web/benchmarks/publish_benchmark.py"),
                                     str(spec_p)], env=env).returncode
                if rc != 0:
                    log("page: publish FAILED")
                else:
                    # homepage reads this index at request time — no redeploy
                    tok = os.environ["BLOB_READ_WRITE_TOKEN"]
                    idx_url = ("https://sf4pvvi6x7hevyhw.private.blob.vercel-storage.com/"
                               "dashboard/benchmark/index.json")
                    try:
                        req = urllib.request.Request(idx_url + "?cache=0",
                              headers={"authorization": f"Bearer {tok}"})
                        idx = json.loads(urllib.request.urlopen(req, timeout=30).read())
                    except Exception:
                        idx = {"pages": []}
                    entry = {"label": f"\u25a4 {args.name.replace(chr(95), chr(32)).title()}",
                             "blob": idx_url.rsplit("/", 1)[0] + f"/{args.name}/benchmark.html?cache=0"}
                    idx["pages"] = [p for p in idx.get("pages", [])
                                    if p.get("blob") != entry["blob"]] + [entry]
                    sys.path.insert(0, str(REPO))
                    from assetpipe.integrations.blob import VercelBlobUploader
                    up = VercelBlobUploader(token=tok, prefix="dashboard", access="private")
                    tmp = out / "benchmark_index.json"
                    tmp.write_text(json.dumps(idx, indent=1))
                    up.upload(str(tmp), "benchmark/index.json")
                    log(f"page: published + indexed ({len(idx['pages'])} pages)")

    log(f"DONE — demo_out/{args.name}/: " +
        ", ".join(sorted(p.name for p in out.iterdir() if p.is_file())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
