"use client";

/**
 * In-page 3D for a Gaussian splat.
 *
 * Viewer.tsx renders ply/glb/gltf/obj/stl and deliberately not .splat — a splat
 * is a cloud of sorted, alpha-blended ellipsoids rather than a mesh, so it needs
 * its own renderer, and until now the page told the customer it "needs a desktop
 * viewer". That sent them off the site to look at the one output the 3DGUT route
 * actually produces.
 *
 * Rather than bundle a splat renderer into this app, the pipeline publishes the
 * standalone WebGL viewer (tools/splat_viewer_html.py) into blob storage right
 * beside the splat, and this frames it. Two consequences worth knowing:
 *
 *  - The iframe is SAME-ORIGIN: both the viewer document and the splat it pulls
 *    come through /api/download on this domain, so the session cookie the
 *    private store requires is sent on both. A cross-origin blob URL would 403.
 *  - three.js and the splat library load from a CDN inside that document, so
 *    they never touch this app's bundle.
 */

import { useState } from "react";
import type { Asset, AssetFile } from "../../../lib/manifest";

const VIEWER = "splat_view.html";

/**
 * The published viewer sits in the same blob directory as its splat, so its URL
 * is the splat's with the final path segment swapped. Deriving it costs one
 * string operation and keeps the manifest schema untouched — nothing in the
 * publisher has to learn about this file.
 */
function viewerUrlFor(file: AssetFile): string | null {
  if (!/^https?:\/\//.test(file.url)) return null;
  return file.url.replace(/[^/]+$/, VIEWER);
}

/** scene_gaussians.splat is the trained 3DGUT scene — the reconstruction a
 *  customer wants to turn around. object.splat is the fuse's small crop, so it
 *  only wins when there is no scene splat at all. */
function pickSplat(asset: Asset): AssetFile | null {
  const splats = asset.files.filter((f) => /\.splat$/i.test(f.name));
  if (!splats.length) return null;
  return (
    splats.find((f) => f.name === "scene_gaussians.splat") ??
    [...splats].sort((a, b) => b.bytes - a.bytes)[0]
  );
}

export default function SplatEmbed({ asset }: { asset: Asset }) {
  const splat = pickSplat(asset);
  const src = splat ? viewerUrlFor(splat) : null;
  // Click to load. A scene splat is tens of megabytes and every byte crosses a
  // serverless function, so it is never pulled just because someone opened the
  // page — the same bargain Viewer.tsx strikes with AUTOLOAD_LIMIT.
  const [open, setOpen] = useState(false);

  if (!splat || !src) return null;

  const href =
    `/api/download?inline=1&name=${encodeURIComponent(VIEWER)}` +
    `&url=${encodeURIComponent(src)}`;

  return (
    <div className="panel">
      <h3>
        <span aria-hidden>🌐</span> Gaussian splat — view in 3D
      </h3>
      <p className="note">
        The trained reconstruction itself, rendered in your browser. Drag to
        orbit, scroll to zoom. Nothing to install.
      </p>
      {/* This is the best-looking render on the page, so customers reach for it
          to measure and nothing happens. It is a SCENE reconstruction with no
          object boundary: measured on the coffee-table splat, the densest
          horizontal surface is the rug rather than the table, and the extent of
          one plane moves 69.8 -> 78.1 in as the opacity cutoff changes. Say so
          here rather than letting them find out by clicking. */}
      <p className="note" style={{ color: "var(--warn)" }}>
        Best for looking, not for sizing — a splat has no surface to click and
        includes the surroundings. Use <b>View and measure</b> above for
        dimensions.
      </p>
      <div className="inner">
        {open ? (
          <iframe
            src={href}
            title={`${asset.title} — Gaussian splat`}
            allow="fullscreen"
            style={{
              width: "100%",
              height: "70vh",
              minHeight: 420,
              border: 0,
              display: "block",
              background: "#0b0b0d",
            }}
          />
        ) : (
          <button type="button" className="dl" onClick={() => setOpen(true)}>
            ▶ Load the splat in 3D ({splat.size})
          </button>
        )}
      </div>
    </div>
  );
}
