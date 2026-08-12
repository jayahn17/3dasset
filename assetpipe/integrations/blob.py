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

    The endpoint, header names and ``api_version`` all track the official SDK
    and Vercel bumps them periodically. To re-derive them rather than guess::

        npm install @vercel/blob
        grep -rhoE '"x-[a-z0-9-]+"' node_modules/@vercel/blob/dist/*.js | sort -u
        grep -rhoE 'BLOB_API_VERSION = [0-9]+' node_modules/@vercel/blob/dist/*.js
        grep -rhoE 'defaultVercelBlobApiUrl = "[^"]*"' node_modules/@vercel/blob/dist/*.js
        # and how put() builds its request:
        grep -rhoE 'const params = new URLSearchParams\(\{ pathname \}\)' -A3 \
            node_modules/@vercel/blob/dist/*.js

    Checked against @vercel/blob 2.6.1 (api version 12).
    For production, minting client tokens from a son endpoint +
    PresignedPutUploader is the sturdier path.
    """

    # v12 uploads go to the API host with the pathname as a QUERY PARAM.
    # The old scheme — PUT https://blob.vercel-storage.com/<pathname> — still
    # resolves but rejects every request with 400 "Invalid pathname", because
    # that host now only serves *reads* (of public blobs; private reads live at
    # https://<storeid>.private.blob.vercel-storage.com/<pathname>).
    API_URL = "https://vercel.com/api/blob"

    def __init__(self, token: str, prefix: str = "scans", api_version: str = "12",
                 add_random_suffix: bool = False, allow_overwrite: bool = True,
                 access: str = "public") -> None:
        self.token = token
        self.prefix = prefix.strip("/")
        self.api_version = api_version
        # The store id is not encoded in an OIDC token, so the SDK always sends
        # it as its own header. A read-write token is `vercel_blob_rw_<store>_
        # <secret>`, so for our case it can just be read back out of the token.
        self.store_id = token.split("_")[3] if token.count("_") >= 4 else ""
        # A store's access level is fixed when it is created and there is no
        # update command. Requesting the wrong one fails the whole upload with
        # 400 "Cannot use public access on a private store", so this must match
        # `vercel blob get-store <id>` → Access.
        self.access = access
        # Random suffixes default OFF. The dashboard publishes its manifest to a
        # URL that is baked into the site as NEXT_PUBLIC_MANIFEST_URL; a suffix
        # would mint a new URL on every publish and silently strand the site on
        # the first one, defeating the whole publish-without-redeploy design.
        # Stable pathnames also make re-publishing idempotent, which needs
        # overwrite permission.
        self.add_random_suffix = add_random_suffix
        self.allow_overwrite = allow_overwrite

    def upload(self, local_path: str, dest_name: str | None = None) -> str:
        import requests  # lazy
        from urllib.parse import urlencode

        name = dest_name or os.path.basename(local_path)
        pathname = f"{self.prefix}/{name}" if self.prefix else name
        headers = {
            "authorization": f"Bearer {self.token}",
            "x-api-version": self.api_version,
            "x-content-type": content_type_for(local_path),
            "x-add-random-suffix": "1" if self.add_random_suffix else "0",
            "x-allow-overwrite": "1" if self.allow_overwrite else "0",
            # NOT "x-access" — that name is ignored, the request then defaults
            # to public, and a private store rejects the whole upload with 400.
            "x-vercel-blob-access": self.access,
        }
        if self.store_id:
            headers["x-vercel-blob-store-id"] = self.store_id
        with open(local_path, "rb") as fh:
            # A real file object lets requests set Content-Length from the file
            # size rather than chunking, which the API needs for large meshes.
            resp = requests.put(
                f"{self.API_URL}/?{urlencode({'pathname': pathname})}",
                data=fh, headers=headers, timeout=600,
            )
        if not resp.ok:  # the body says *why*; raise_for_status alone does not
            raise RuntimeError(
                f"blob PUT {pathname} → {resp.status_code}: {resp.text[:300]}")
        return resp.json()["url"]


def make_uploader(kind: str | None, **kw):
    """Factory used by the CLI. Returns None for 'none' (keep local paths)."""
    if kind in (None, "none"):
        return None
    if kind == "local":
        return LocalCopyUploader(kw["dest_dir"], kw["base_url"])
    if kind == "vercel":
        return VercelBlobUploader(kw["token"], prefix=kw.get("prefix", "scans"),
                                  access=kw.get("access", "public"))
    raise ValueError(f"unknown uploader kind: {kind!r} (use none|local|vercel)")
