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

/**
 * The splat whose blob directory holds splat_view.html.
 *
 * DO NOT "improve" this ordering. tools/publish_splat_view.mjs runs the same
 * preference to decide where to PUT the viewer, and derives the pathname by
 * swapping that file's last path segment. The two are one contract: if this
 * function prefers a different file and that file lives in a different blob
 * directory, every asset's splat panel answers `blob 404` again — the failure
 * that hid for weeks because the URL only exists after JS runs, so neither a
 * manifest crawl nor a page crawl can see it.
 *
 * What the viewer DISPLAYS is chosen inside the published document, not here;
 * see shownSplat below for the one thing this page still needs to know about it.
 */
function anchorSplat(asset: Asset): AssetFile | null {
  const splats = asset.files.filter((f) => /\.splat$/i.test(f.name));
  if (!splats.length) return null;
  return (
    splats.find((f) => f.name === "scene_gaussians.splat") ??
    [...splats].sort((a, b) => b.bytes - a.bytes)[0]
  );
}

/**
 * The splat the published viewer will actually load — for the button's byte
 * count, and nothing else.
 *
 * scene_panel.splat is the scene cropped to the measured object with the floor
 * plane removed; scene_gaussians.splat is the whole room. The publisher prefers
 * the panel when one exists, so the button was quoting the room's size for a
 * download that never happens: "Load the splat in 3D (29.7 MB)" in front of a
 * 0.5 MB file. Overstating the cost by 60x is the kind of label that stops
 * someone clicking the best thing on the page.
 *
 * Mirrors publish_splat_view.mjs's `show` choice. Falls back to the anchor, so
 * an asset with no panel (sofa_20260805 — no scene_gaussians.ply survives to
 * crop from) still quotes the file it really loads.
 */
function shownSplat(asset: Asset, anchor: AssetFile): AssetFile {
  return asset.files.find((f) => f.name === "scene_panel.splat") ?? anchor;
}

export default function SplatEmbed({ asset }: { asset: Asset }) {
  const splat = anchorSplat(asset);
  // Prefer the URL the publisher SAYS it wrote; derive only for manifests
  // published before that field existed. Once every live manifest carries it,
  // viewerUrlFor and the anchor-ordering rule it depends on can both go, and
  // with them the mirrored-picker trap they exist to work around.
  const src = asset.splat_view_url ?? (splat ? viewerUrlFor(splat) : null);
  const shown = splat ? shownSplat(asset, splat) : null;
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
        <span aria-hidden>🌐</span> Photoreal view (3DGS)
      </h3>
      {/* The splat is the best-looking thing on the page, so it is where people
          reach to measure. It is a SCENE reconstruction with no object boundary:
          on the coffee table the densest horizontal surface is the RUG, and one
          plane's extent moves 69.8 -> 78.1 in on the opacity cutoff alone. It
          now carries a tape of its own, which makes saying this MORE important,
          not less — a number you can read off it is still not a size. */}
      <p className="note">
        Drag to spin · scroll to zoom.{" "}
        <b style={{ color: "var(--warn)" }}>For looking, not for sizing</b> — it
        includes the surroundings. Measure above.
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
            ▶ Load 3D view ({shown!.size})
          </button>
        )}
      </div>
    </div>
  );
}
