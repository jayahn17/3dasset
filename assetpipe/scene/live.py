"""Live scanning session — watch the point cloud grow while you capture.

Scaniverse shows the reconstruction building up as you move; this is the
two-piece equivalent: a client streams frames in, a background thread
re-solves the scene every few frames, and pollers get the latest cloud
(versioned, so unchanged clouds aren't re-sent).

Designed for the COLMAP backend: every solve reuses the same work dir, so
features/matches of frames already seen are cached in ``database.db`` and a
re-solve pays only for the new frames + the mapping pass. Any
:class:`~assetpipe.scene.backends.SceneBackend` works (tests use the stub).

    sess = LiveScanSession(work_dir)
    sess.add_frame(jpg_path)     # kicks a background solve every `solve_every`
    sess.status()                # {frames, points, version, solving, ...}
    sess.cloud()                 # (version, ScenePointCloud)
    sess.finalize(out_dir)       # final solve -> scene.ply/.splat + viewer
"""

from __future__ import annotations

import os
import threading

from .backends import SceneBackend, ScenePointCloud, make_scene_backend


class LiveScanSession:
    def __init__(
        self,
        work_dir: str,
        backend: str | SceneBackend = "auto",
        solve_every: int = 4,
        min_frames: int = 6,
        title: str = "live scan",
    ) -> None:
        self.work_dir = work_dir
        self.backend = (backend if isinstance(backend, SceneBackend)
                        else make_scene_backend(backend))
        self.solve_every = solve_every
        self.min_frames = min_frames
        self.title = title
        self.frames: list[str] = []
        os.makedirs(work_dir, exist_ok=True)

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cloud = ScenePointCloud([], [])
        self._version = 0
        self._solved_at = 0  # len(frames) the last finished solve saw
        self._error: str | None = None
        self._closed = False

    # -- capture ------------------------------------------------------------
    def add_frame(self, image_path: str) -> int:
        """Register a stored frame; may kick a background solve. -> frame idx."""
        with self._lock:
            if self._closed:
                raise RuntimeError("session is finalized")
            self.frames.append(image_path)
            idx = len(self.frames) - 1
        self._maybe_solve()
        return idx

    # -- solving ------------------------------------------------------------
    def _maybe_solve(self, force: bool = False) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            n = len(self.frames)
            due = n >= self.min_frames and n - self._solved_at >= self.solve_every
            if not (force or due):
                return False
            paths = list(self.frames)
            self._thread = threading.Thread(
                target=self._solve, args=(paths,), daemon=True
            )
            self._thread.start()
            return True

    def _solve(self, paths: list[str]) -> None:
        try:
            cloud = self.backend.reconstruct(paths, self.work_dir)
            with self._lock:
                self._cloud = cloud
                self._version += 1
                self._solved_at = len(paths)
                self._error = None
        except Exception as e:  # noqa: BLE001 — early frames often can't solve
            with self._lock:
                self._error = str(e)[:300]
                self._solved_at = len(paths)  # don't retry same frames in a loop

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    # -- reading ------------------------------------------------------------
    def cloud(self) -> tuple[int, ScenePointCloud]:
        with self._lock:
            return self._version, self._cloud

    def status(self) -> dict:
        with self._lock:
            cloud = self._cloud
            base = {
                **cloud.stats,  # backend stats (may lag behind capture)
                "frames": len(self.frames),
                "points": len(cloud.xyz),
                "version": self._version,
                "solving": self._thread is not None and self._thread.is_alive(),
                "backend": self.backend.name,
                "error": self._error,
            }
        # outside the lock: azimuths do real math on the (immutable) cloud
        base["coverage"] = cloud.coverage_azimuths()
        return base

    # -- finish -------------------------------------------------------------
    def finalize(self, out_dir: str, splat: bool = True, viewer: bool = True,
                 clean: bool = False, mesh: bool = False,
                 timeout: float = 600.0) -> dict:
        """Final full solve, then write the same artifacts as ``scan_scene``."""
        from . import write_artifacts  # lazy: avoids package-import cycle

        self.wait(timeout)
        with self._lock:
            self._closed = True
            stale = self._solved_at < len(self.frames) or self._version == 0
        if stale:
            self._maybe_solve(force=True)
            self.wait(timeout)
        version, cloud = self.cloud()
        if version == 0 or not cloud.xyz:
            raise RuntimeError(self._error or "no reconstruction "
                               f"(got {len(self.frames)} frames)")
        return write_artifacts(cloud, out_dir, self.backend.name, splat=splat,
                               viewer=viewer, title=self.title, clean=clean,
                               mesh=mesh)
