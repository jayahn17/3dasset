#!/usr/bin/env python3
"""Serial GPU route queue: TRELLIS (product) or 3DGUT (furniture/scene).

Watches ``captures/status/CrateScan-*.json`` written by ``watch_inbox.py``.
Uses ``assetpipe route`` (AABB ≤24″ → trellis, else 3dgrut):

    trellis  → gen3d → metric GLB/STL (mm) → *_TRELLIS_RESULT.zip
    3dgrut   → run_3dgut_from_session → splat → *_3DGUT_RESULT.zip

One GPU job at a time. Start with Engin170 autopilot:

    bash scripts/start_engin170_autopilot.sh
    # or:
    python tools/watch_3dgut_queue.py

Done markers: ``captures/status/.gpu_route_done.json`` (also reads legacy
``.3dgut_done.json``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from assetpipe.scene.route import classify_capture  # noqa: E402
from assetpipe.scene.trellis_metric import (  # noqa: E402
    apply_rgbd_scale_to_glb,
    write_trellis_view_html,
)

DONE_NAME = ".gpu_route_done.json"
LEGACY_DONE_NAME = ".3dgut_done.json"
LOCK_NAME = ".3dgut_queue.lock"
GEN3D_HEALTH = "http://127.0.0.1:8080/health"
GEN3D_GENERATE = "http://127.0.0.1:8080/generate"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _done_path(captures: Path) -> Path:
    status = captures / "status"
    primary = status / DONE_NAME
    legacy = status / LEGACY_DONE_NAME
    if primary.is_file():
        return primary
    if legacy.is_file() and not primary.is_file():
        # Migrate once.
        data = _load_json(legacy)
        _save_json(primary, data)
        return primary
    return primary


def _load_done(captures: Path) -> dict:
    return _load_json(_done_path(captures))


def _save_done(captures: Path, data: dict) -> None:
    path = _done_path(captures)
    _save_json(path, data)
    # Keep legacy in sync for older tooling.
    _save_json(captures / "status" / LEGACY_DONE_NAME, data)


def _acquire_lock(captures: Path) -> Path | None:
    lock = captures / "status" / LOCK_NAME
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.is_file():
        try:
            pid = int(lock.read_text().strip().split()[0])
            if Path(f"/proc/{pid}").exists():
                return None
        except Exception:
            pass
    lock.write_text(f"{os.getpid()} {time.time():.0f}\n")
    return lock


def _release_lock(lock: Path | None) -> None:
    if lock and lock.is_file():
        try:
            lock.unlink()
        except OSError:
            pass


def _find_session(status: dict, captures: Path, name: str) -> Path | None:
    """Locate RGB-D session dir (has manifest.json)."""
    rel = status.get("session")
    if rel:
        cand = (REPO / rel).resolve() if not os.path.isabs(rel) else Path(rel)
        if (cand / "manifest.json").is_file():
            return cand
        if cand.is_file() and cand.name == "manifest.json":
            return cand.parent

    for root in (
        captures / "work" / name,
        Path(status.get("out") or "") if status.get("out") else None,
        REPO / "demo_out" / name,
    ):
        if root is None or not Path(root).exists():
            continue
        for dirpath, _dns, fns in os.walk(root):
            if "manifest.json" in fns:
                return Path(dirpath)

    done_zip = captures / "done" / f"{name}.zip"
    if done_zip.is_file():
        work = captures / "work" / name
        _log(f"  re-extract {done_zip.name} → work/")
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(done_zip) as zf:
            zf.extractall(work)
        for dirpath, _dns, fns in os.walk(work):
            if "manifest.json" in fns:
                return Path(dirpath)
    return None


def _route_root(session: Path, status: dict, out_root: Path, name: str) -> Path:
    out = Path(status.get("out") or (out_root / name))
    for cand in (out / "object_asset", out, session):
        if cand.is_dir():
            return cand
    return session


def _object_dirs(status: dict, out_root: Path, name: str) -> list[Path]:
    out = Path(status.get("out") or (out_root / name))
    return [
        out / "object_asset",
        out_root / name / "object_asset",
        out_root / f"{name}_object",
        out,
    ]


def _find_dims(status: dict, out_root: Path, name: str) -> Path | None:
    for d in _object_dirs(status, out_root, name):
        p = d / "dims.json"
        if p.is_file():
            return p
    return None


def _find_analysis(status: dict, out_root: Path, name: str) -> Path | None:
    for d in _object_dirs(status, out_root, name):
        p = d / "analysis.json"
        if p.is_file():
            return p
    return None


def _pack_3dgut_result(out_dir: Path, name: str) -> Path | None:
    result = out_dir.parent / f"{name}_3DGUT_RESULT.zip"
    if result.exists():
        result.unlink()
    files = (
        "OPEN_ME.html",
        "splat_view.html",
        "scene_gaussians.splat",
        "scene_gaussians.ply",
        "dims.json",
        "measurement.txt",
        "route_decision.json",
    )
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in files:
            p = out_dir / fname
            if p.is_file():
                zf.write(p, arcname=f"{name}_3DGUT_RESULT/{fname}")
        meta = out_dir / "3dgut_queue.json"
        if meta.is_file():
            zf.write(meta, arcname=f"{name}_3DGUT_RESULT/3dgut_queue.json")
    if not result.is_file() or result.stat().st_size == 0:
        return None
    return result


def _pack_trellis_result(out_dir: Path, name: str) -> Path | None:
    result = out_dir.parent / f"{name}_TRELLIS_RESULT.zip"
    if result.exists():
        result.unlink()
    files = (
        "view.html",
        "asset_trellis.glb",
        "asset_trellis_raw.glb",
        "asset_trellis_mm.stl",
        "dims.json",
        "dims_mm.json",
        "route_decision.json",
        "trellis_queue.json",
    )
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in files:
            p = out_dir / fname
            if p.is_file():
                zf.write(p, arcname=f"{name}_TRELLIS_RESULT/{fname}")
    if not result.is_file() or result.stat().st_size == 0:
        return None
    return result


def _upload_result(local_zip: Path, folder_id: str, captures: Path) -> bool:
    try:
        from drive_rgbd_autopilot import rclone_available, upload_result
    except ImportError:
        sys.path.insert(0, str(REPO / "tools"))
        from drive_rgbd_autopilot import rclone_available, upload_result  # type: ignore

    rclone = rclone_available()
    if rclone:
        return upload_result(rclone, local_zip, folder_id)
    outbox = captures / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local_zip, outbox / local_zip.name)
    _log(f"  RESULT → captures/outbox/{local_zip.name} (no rclone)")
    return True


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    _log("  $ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd or REPO), env=env)
    return int(proc.returncode)


def _gen3d_healthy() -> bool:
    try:
        with urllib.request.urlopen(GEN3D_HEALTH, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return bool(data.get("ok"))
    except Exception:
        return False


def _ensure_gen3d() -> bool:
    if _gen3d_healthy():
        return True
    _log("  starting gen3d server (TRELLIS)…")
    log = REPO / "logs" / "gen3d_server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    py = Path.home() / "miniconda3" / "envs" / "gen3d" / "bin" / "python"
    if not py.is_file():
        py = Path(sys.executable)
    env = os.environ.copy()
    env["GEN3D_BACKENDS"] = "trellis"
    env["PORT"] = "8080"
    for k in ("LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    env["PATH"] = str(py.parent) + os.pathsep + env.get("PATH", "")
    with open(log, "a", encoding="utf-8") as fh:
        subprocess.Popen(
            [str(py), str(REPO / "services" / "gen3d_server.py")],
            cwd=str(REPO),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(60):
        time.sleep(2)
        if _gen3d_healthy():
            _log("  gen3d ready")
            return True
    _log("  !! gen3d failed to become healthy")
    return False


def _prepare_trellis_views(
    session: Path,
    status: dict,
    out_root: Path,
    name: str,
    views_dir: Path,
    max_views: int = 8,
) -> list[Path]:
    from PIL import Image

    views_dir.mkdir(parents=True, exist_ok=True)
    for p in views_dir.glob("*"):
        if p.is_file():
            p.unlink()

    man = json.loads((session / "manifest.json").read_text())
    frames = man.get("frames") or man.get("keyframes") or []
    if not frames:
        raise RuntimeError(f"no frames in {session}")

    idxs: list[int] = []
    analysis_path = _find_analysis(status, out_root, name)
    if analysis_path:
        analysis = json.loads(analysis_path.read_text())
        idxs.extend(int(t["index"]) for t in (analysis.get("top") or [])[:8]
                    if "index" in t)

    n = len(frames)
    idxs.extend(int(i * (n - 1) / 5) for i in range(6))
    seen: set[int] = set()
    ordered: list[int] = []
    for i in idxs:
        if i in seen or i < 0 or i >= n:
            continue
        seen.add(i)
        ordered.append(i)
    ordered = ordered[:max_views]

    out_paths: list[Path] = []
    for j, i in enumerate(ordered):
        fr = frames[i]
        color = fr.get("color") or fr.get("image")
        src = session / color
        if not src.is_file():
            alt = session / "color" / Path(str(color)).name
            if alt.is_file():
                src = alt
        if not src.is_file():
            continue
        im = Image.open(src).convert("RGB")
        w, h = im.size
        scale = 1024 / max(w, h)
        if scale < 1:
            im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        dst = views_dir / f"view_{j:02d}.jpg"
        im.save(dst, quality=92)
        out_paths.append(dst)

    # Prefer object photo as an extra view if present.
    for d in _object_dirs(status, out_root, name):
        photo = d / "photo.jpg"
        if photo.is_file():
            im = Image.open(photo).convert("RGB")
            w, h = im.size
            scale = 1024 / max(w, h)
            if scale < 1:
                im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
            dst = views_dir / "photo.jpg"
            im.save(dst, quality=92)
            out_paths.append(dst)
            break

    if not out_paths:
        raise RuntimeError("no TRELLIS views prepared")
    return out_paths


def _run_trellis(
    *,
    name: str,
    session: Path,
    status: dict,
    out_root: Path,
    decision: dict,
    folder_id: str,
    captures: Path,
    dry_run: bool,
) -> str:
    out_dir = out_root / f"{name}_trellis"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "route_decision.json").write_text(
        json.dumps(decision, indent=2) + "\n"
    )

    if dry_run:
        _log(f"  dry-run: would TRELLIS → {out_dir}")
        return "trained"

    if not _ensure_gen3d():
        return "failed"

    views_dir = out_dir / "views"
    try:
        paths = _prepare_trellis_views(session, status, out_root, name, views_dir)
    except Exception as e:  # noqa: BLE001
        _log(f"  !! view prep failed: {e}")
        return "failed"
    _log(f"  {len(paths)} views → {views_dir}")

    py = sys.executable
    # Prefer assetpipe env if we're somehow not in it.
    assetpipe_py = Path.home() / "miniconda3" / "envs" / "assetpipe" / "bin" / "python"
    if assetpipe_py.is_file():
        py = str(assetpipe_py)

    rc = _run([
        py, "-m", "assetpipe", "generate",
        str(views_dir),
        "--out", str(out_dir),
        "--backend", "trellis",
        "--endpoint", GEN3D_GENERATE,
        "--views", "4",
        "--seed", "1",
    ])
    glb = out_dir / "asset_trellis.glb"
    if rc != 0 or not glb.is_file():
        _log(f"  ✗ TRELLIS generate failed rc={rc}")
        return "failed"

    dims_path = _find_dims(status, out_root, name)
    if dims_path is None:
        _log("  !! no dims.json — GLB left unscaled (no mm)")
        write_trellis_view_html(out_dir, title=f"{name} TRELLIS")
    else:
        try:
            meta = apply_rgbd_scale_to_glb(glb, dims_path, out_dir=out_dir)
            _log(f"  metric {meta['dims_mm_summary']}  scale={meta['scale_factor']:.4f}")
            dims_mm = json.loads(Path(meta["dims_mm"]).read_text())
            write_trellis_view_html(out_dir, title=f"{name} TRELLIS", dims_mm=dims_mm)
        except Exception as e:  # noqa: BLE001
            _log(f"  !! metricize failed: {e}")
            write_trellis_view_html(out_dir, title=f"{name} TRELLIS")
            return "failed"

    queue_meta = {
        "name": name,
        "route": "trellis",
        "session": str(session),
        "out": str(out_dir),
        "finished": time.time(),
        "reasons": decision.get("reasons"),
        "dims_mm": str(out_dir / "dims_mm.json"),
        "stl_mm": str(out_dir / "asset_trellis_mm.stl"),
    }
    (out_dir / "trellis_queue.json").write_text(json.dumps(queue_meta, indent=2) + "\n")

    result = _pack_trellis_result(out_dir, name)
    uploaded = False
    if result:
        uploaded = _upload_result(result, folder_id, captures)

    done = _load_done(captures)
    done[name] = {
        "ok": True,
        "route": "trellis",
        "out": str(out_dir),
        "result_zip": str(result) if result else None,
        "uploaded": uploaded,
        "finished": time.time(),
    }
    _save_done(captures, done)
    _log(f"  ✓ TRELLIS {name} → {out_dir}" + (" + uploaded" if uploaded else ""))
    return "trained"


def _run_3dgut(
    *,
    name: str,
    session: Path,
    decision: dict,
    out_dir: Path,
    iterations: int,
    every: int,
    max_frames: int,
    folder_id: str,
    captures: Path,
    dry_run: bool,
) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "route_decision.json").write_text(
        json.dumps(decision, indent=2) + "\n"
    )

    if dry_run:
        _log(f"  dry-run: would train → {out_dir}")
        return "trained"

    py = sys.executable
    train_cmd = [
        py,
        str(REPO / "tools/run_3dgut_from_session.py"),
        str(session),
        "--out",
        str(out_dir),
        "--iterations",
        str(iterations),
        "--every",
        str(every),
        "--max-frames",
        str(max_frames),
    ]
    rc = _run(train_cmd)
    if rc != 0:
        done = _load_done(captures)
        done[name] = {
            "ok": False,
            "route": "3dgrut",
            "error": f"train_exit_{rc}",
            "out": str(out_dir),
            "finished": time.time(),
        }
        _save_done(captures, done)
        _log(f"  ✗ train failed rc={rc}")
        return "failed"

    export_cmd = [
        py,
        str(REPO / "tools/watch_export_3dgut.py"),
        str(out_dir),
        "--title",
        f"{name} 3DGRUT",
        "--timeout-h",
        "0.05",
    ]
    rc = _run(export_cmd)
    if rc != 0:
        export_cmd[-1] = "1.0"
        rc = _run(export_cmd)
    if rc != 0:
        done = _load_done(captures)
        done[name] = {
            "ok": False,
            "route": "3dgrut",
            "error": f"export_exit_{rc}",
            "out": str(out_dir),
            "finished": time.time(),
        }
        _save_done(captures, done)
        _log(f"  ✗ export failed rc={rc}")
        return "failed"

    meta = {
        "name": name,
        "route": "3dgrut",
        "session": str(session),
        "out": str(out_dir),
        "iterations": iterations,
        "finished": time.time(),
        "reasons": decision.get("reasons"),
    }
    (out_dir / "3dgut_queue.json").write_text(json.dumps(meta, indent=2) + "\n")

    result = _pack_3dgut_result(out_dir, name)
    uploaded = False
    if result:
        uploaded = _upload_result(result, folder_id, captures)

    done = _load_done(captures)
    done[name] = {
        "ok": True,
        "route": "3dgrut",
        "out": str(out_dir),
        "result_zip": str(result) if result else None,
        "uploaded": uploaded,
        "finished": time.time(),
    }
    _save_done(captures, done)
    _log(f"  ✓ 3DGUT {name} → {out_dir}" + (" + uploaded" if uploaded else ""))
    return "trained"


def process_one(
    status_path: Path,
    *,
    captures: Path,
    out_root: Path,
    folder_id: str,
    iterations: int,
    every: int,
    max_frames: int,
    force_route: str | None,
    dry_run: bool,
    enable_trellis: bool = True,
) -> str:
    """Returns 'trained' | 'skipped' | 'failed' | 'busy-skip'."""
    try:
        status = json.loads(status_path.read_text())
    except Exception as e:  # noqa: BLE001
        _log(f"  !! bad status {status_path.name}: {e}")
        return "failed"

    name = status.get("name") or status_path.stem
    if not status.get("ok"):
        return "busy-skip"
    if status.get("usable_asset") is False:
        return "busy-skip"

    done = _load_done(captures)
    if name in done:
        return "busy-skip"

    session = _find_session(status, captures, name)
    if session is None:
        _log(f"  !! {name}: no session (work/ or done zip missing)")
        done[name] = {
            "ok": False,
            "error": "session_not_found",
            "finished": time.time(),
        }
        _save_done(captures, done)
        return "failed"

    route_root = _route_root(session, status, out_root, name)
    decision_obj = classify_capture(
        str(route_root),
        force=force_route if force_route in ("trellis", "3dgrut") else None,
    )
    decision = decision_obj.to_dict()
    route = decision_obj.route
    _log(f"→ {name}  route={route}  ({'; '.join(decision_obj.reasons[:2])})")

    if route == "trellis":
        if not enable_trellis:
            done[name] = {
                "ok": True,
                "skipped": True,
                "route": route,
                "reasons": decision_obj.reasons,
                "finished": time.time(),
            }
            _save_done(captures, done)
            _log("  skip TRELLIS (ENABLE_TRELLIS=0)")
            return "skipped"
        return _run_trellis(
            name=name,
            session=session,
            status=status,
            out_root=out_root,
            decision=decision,
            folder_id=folder_id,
            captures=captures,
            dry_run=dry_run,
        )

    if route == "3dgrut":
        return _run_3dgut(
            name=name,
            session=session,
            decision=decision,
            out_dir=out_root / f"{name}_3dgut",
            iterations=iterations,
            every=every,
            max_frames=max_frames,
            folder_id=folder_id,
            captures=captures,
            dry_run=dry_run,
        )

    done[name] = {
        "ok": True,
        "skipped": True,
        "route": route,
        "reasons": decision_obj.reasons,
        "finished": time.time(),
    }
    _save_done(captures, done)
    _log(f"  skip unknown route={route}")
    return "skipped"


def _already_done(out_root: Path, name: str) -> tuple[bool, str | None]:
    trellis = out_root / f"{name}_trellis"
    if (trellis / "asset_trellis.glb").is_file() and (trellis / "dims_mm.json").is_file():
        return True, "trellis"
    if (trellis / "asset_trellis.glb").is_file():
        # Raw trellis without metric — still count as done to avoid regen storm;
        # operator can delete marker + folder to redo.
        return True, "trellis"
    dg = out_root / f"{name}_3dgut"
    if (dg / "scene_gaussians.splat").is_file():
        return True, "3dgrut"
    if list(dg.glob("**/export_last.ply")):
        return True, "3dgrut"
    return False, None


def pending_jobs(
    captures: Path,
    out_root: Path,
    *,
    boot_ts: float,
    backfill: bool,
) -> list[Path]:
    status_dir = captures / "status"
    if not status_dir.is_dir():
        return []
    done = _load_done(captures)
    dirty = False
    for st in status_dir.glob("CrateScan-*.json"):
        name = st.stem
        if name in done:
            continue
        ok, route = _already_done(out_root, name)
        if ok:
            done[name] = {
                "ok": True,
                "seeded_existing": True,
                "route": route,
                "out": str(out_root / f"{name}_{'trellis' if route == 'trellis' else '3dgut'}"),
                "finished": time.time(),
            }
            dirty = True
    if dirty:
        _save_done(captures, done)

    jobs: list[Path] = []
    for st in sorted(status_dir.glob("CrateScan-*.json")):
        if st.stem in done:
            continue
        try:
            data = json.loads(st.read_text())
        except Exception:
            continue
        if not (data.get("ok") and data.get("usable_asset") is not False):
            continue
        finished = float(data.get("finished") or data.get("started") or 0)
        if not backfill and finished and finished < boot_ts - 600:
            continue
        if not backfill and not finished and st.stat().st_mtime < boot_ts - 600:
            continue
        jobs.append(st)
    return jobs


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--captures", default=str(REPO / "captures"))
    ap.add_argument("--out", default=str(REPO / "demo_out"))
    ap.add_argument(
        "--folder-id",
        default="1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI",
        help="Drive folder for RESULT upload",
    )
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--once", action="store_true", help="process at most one job, exit")
    ap.add_argument("--iterations", type=int, default=30_000)
    ap.add_argument("--every", type=int, default=4)
    ap.add_argument("--max-frames", type=int, default=100)
    ap.add_argument(
        "--force-route",
        choices=["trellis", "3dgrut"],
        default=None,
        help="override router (debug)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--backfill",
        action="store_true",
        help="also process historical ok statuses (default: only new completions)",
    )
    ap.add_argument(
        "--no-trellis",
        action="store_true",
        help="skip TRELLIS arm (3DGUT-only, legacy behavior)",
    )
    args = ap.parse_args()

    captures = Path(args.captures)
    out_root = Path(args.out)
    (captures / "status").mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)

    lock = _acquire_lock(captures)
    if lock is None:
        _log("another watch_3dgut_queue is running — exit")
        return 0

    boot_ts = time.time()
    enable_trellis = not args.no_trellis
    _log(
        f"GPU route queue watching {captures / 'status'}  "
        f"trellis={enable_trellis}  iters={args.iterations}  "
        f"interval={args.interval}s  backfill={args.backfill}"
    )
    try:
        while True:
            jobs = pending_jobs(
                captures,
                out_root,
                boot_ts=boot_ts,
                backfill=args.backfill,
            )
            if jobs:
                _log(f"{len(jobs)} pending — next {jobs[0].stem}")
                process_one(
                    jobs[0],
                    captures=captures,
                    out_root=out_root,
                    folder_id=args.folder_id,
                    iterations=args.iterations,
                    every=args.every,
                    max_frames=args.max_frames,
                    force_route=args.force_route,
                    dry_run=args.dry_run,
                    enable_trellis=enable_trellis,
                )
                if args.once:
                    return 0
            else:
                if args.once:
                    _log("no pending GPU route jobs")
                    return 0
            time.sleep(args.interval)
    finally:
        _release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(main())
