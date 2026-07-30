#!/usr/bin/env python3
"""Open a proper WebGL gaussian / antimatter15 splat viewer.

Writes ``splat_view.html`` beside the asset and serves it over localhost
(GaussianSplats3D can load ``.splat`` and many ``.ply`` splat exports).

Usage:
  python tools/view_splat.py demo_out/CrateScan-1B38880A/scene_object.splat
  python tools/view_splat.py demo_out/.../scene_gaussians.ply --port 8765
"""

from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
from splat_viewer_html import write_splat_viewer  # noqa: E402


def write_viewer(asset: Path) -> Path:
    return write_splat_viewer(asset.parent, asset.name, asset.name)


def serve_and_open(directory: Path, html_name: str, port: int) -> None:
    os.chdir(directory)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter
            if args and str(args[0]).startswith("GET") and "favicon" in str(args[0]):
                return
            super().log_message(fmt, *args)

    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        url = f"http://127.0.0.1:{port}/{html_name}"
        print(f"✔ splat viewer → {url}")
        print(f"  serving {directory}")
        print("  Ctrl+C to stop")
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("splat", help=".splat or gaussian .ply path")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true",
                    help="only write splat_view.html, do not serve")
    args = ap.parse_args()

    asset = Path(args.splat).resolve()
    if not asset.is_file():
        raise SystemExit(f"not found: {asset}")
    if asset.suffix.lower() not in {".splat", ".ply", ".ksplat"}:
        raise SystemExit("expected .splat / .ply / .ksplat")

    html = write_viewer(asset)
    print(f"wrote {html}")
    if args.no_open:
        return 0
    serve_and_open(asset.parent, html.name, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
