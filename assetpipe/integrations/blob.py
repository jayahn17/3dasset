"""Blob-storage upload for large meshes / scans (PLY, GLB) referenced by URL.

Raw scans — Gaussian-splat / point-cloud PLYs, textured GLBs — are far too big
for git (100 MB hard cap + permanent history bloat) and for son's API body
(4 MB) / Vercel functions (~4.5 MB). They belong in blob storage; only the
resulting URL travels into the twin as `model_3d_ref`.

Three uploaders, smallest-setup first:
  * LocalCopyUploader   — copy into a served directory (dev / self-hosted 4080).
  * PresignedPutUploader — HTTP PUT to a per-file presigned URL (S3 / R2 / GCS /
    Vercel client-upload). The standard, provider-agnostic path.
  * VercelBlobUploader  — convenience REST PUT to Vercel Blob with a token.

Only the PUT-based uploaders need `requests` (imported lazily), so the stdlib
core stays dependency-free.
"""

from __future__ import annotations

import mimetypes
import os
import shutil

# 3D types mimetypes doesn't know reliably.
_CONTENT_TYPES = {
    ".ply": "application/octet-stream",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".obj": "text/plain",
    ".usdz": "model/vnd.usdz+zip",
    ".usd": "application/octet-stream",
}


def content_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _CONTENT_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


class LocalCopyUploader:
    """Copy the file into a locally-served directory; return base_url/<name>.

    Point ``base_url`` at whatever serves ``dest_dir`` (nginx, `python -m
    http.server`, the 4080 box on your tailnet). Zero external deps.
    """

    def __init__(self, dest_dir: str, base_url: str) -> None:
        self.dest_dir = dest_dir
        self.base_url = base_url.rstrip("/")
        os.makedirs(dest_dir, exist_ok=True)

    def upload(self, local_path: str, dest_name: str | None = None) -> str:
        name = dest_name or os.path.basename(local_path)
        dst = os.path.join(self.dest_dir, name)
        os.makedirs(os.path.dirname(dst) or self.dest_dir, exist_ok=True)
        shutil.copyfile(local_path, dst)
        return f"{self.base_url}/{name}"


class PresignedPutUploader:
    """PUT to a per-file presigned URL.

    ``url_for`` maps a filename -> (put_url, public_url). Mint presigned URLs
    from S3/R2/GCS, or from a small son endpoint using Vercel client-upload
    tokens. This keeps large uploads off the serverless function body entirely.
    """

    def __init__(self, url_for) -> None:
        self.url_for = url_for

    def upload(self, local_path: str, dest_name: str | None = None) -> str:
        import requests  # lazy

        put_url, public_url = self.url_for(dest_name or os.path.basename(local_path))
        with open(local_path, "rb") as fh:
            resp = requests.put(
                put_url, data=fh,
                headers={"Content-Type": content_type_for(local_path)}, timeout=600,
            )
        resp.raise_for_status()
        return public_url or put_url.split("?")[0]


class VercelBlobUploader:
    """Convenience REST PUT to Vercel Blob.

    NOTE: confirm ``api_version`` against the @vercel/blob version your son
    deploy uses — Vercel bumps it periodically. For production, minting client
    tokens from a son endpoint + PresignedPutUploader is the sturdier path.
    """

    def __init__(self, token: str, prefix: str = "scans", api_version: str = "7") -> None:
        self.token = token
        self.prefix = prefix.strip("/")
        self.api_version = api_version

    def upload(self, local_path: str, dest_name: str | None = None) -> str:
        import requests  # lazy

        name = dest_name or os.path.basename(local_path)
        pathname = f"{self.prefix}/{name}" if self.prefix else name
        with open(local_path, "rb") as fh:
            resp = requests.put(
                f"https://blob.vercel-storage.com/{pathname}",
                data=fh,
                headers={
                    "authorization": f"Bearer {self.token}",
                    "x-api-version": self.api_version,
                    "x-content-type": content_type_for(local_path),
                    "x-add-random-suffix": "1",
                },
                timeout=600,
            )
        resp.raise_for_status()
        return resp.json()["url"]


def make_uploader(kind: str | None, **kw):
    """Factory used by the CLI. Returns None for 'none' (keep local paths)."""
    if kind in (None, "none"):
        return None
    if kind == "local":
        return LocalCopyUploader(kw["dest_dir"], kw["base_url"])
    if kind == "vercel":
        return VercelBlobUploader(kw["token"], prefix=kw.get("prefix", "scans"))
    raise ValueError(f"unknown uploader kind: {kind!r} (use none|local|vercel)")
