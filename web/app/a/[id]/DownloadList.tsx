"use client";

/**
 * The file rows for one target, each with its own 3D window.
 *
 * The list used to offer downloads and nothing else, so the only way to look at
 * anything but the ranked default was to download it and open Blender. Every
 * renderable file now has a View button that opens a window right under its row.
 *
 * ONE AT A TIME, and that is not a stylistic choice. Each window is a live WebGL
 * context; browsers cap them at roughly 16 and then hand back a dead canvas that
 * renders black with no error. koala ships 13 renderable files, so "open them
 * all" is past the cap on one asset — before counting the big panel at the top of
 * the page or a context leaked by a previous navigation. Opening a second window
 * unmounts the first, which disposes its renderer and forces its context loss
 * (see Viewer's teardown), so the page holds at most two.
 *
 * Nothing here decides anything about a NUMBER. Verdicts arrive already computed
 * from the server (page.tsx owns fileVerdict) and the window is the same Viewer
 * component the big panel uses, so a row cannot disagree with the panel above it
 * about the same file.
 */

import { createContext, useContext, useMemo, useState } from "react";
import Viewer, { canRenderInline } from "./Viewer";
import { downloadHref, type Asset, type AssetFile } from "../../../lib/manifest";

/**
 * Which row window is open, for the WHOLE PAGE.
 *
 * This has to live above the panels. An asset renders one DownloadList per
 * target — koala has four — so holding "which is open" inside the list made the
 * limit per-panel: opening a row under Blender and another under CAD gave two
 * live WebGL contexts, which is the exact thing the single-window rule exists to
 * prevent. Caught by driving it; the state was correct and its SCOPE was wrong.
 */
const OpenRow = createContext<{
  open: string | null;
  setOpen: (k: string | null) => void;
} | null>(null);

export function ViewOneProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState<string | null>(null);
  const value = useMemo(() => ({ open, setOpen }), [open]);
  return <OpenRow.Provider value={value}>{children}</OpenRow.Provider>;
}

/** Server-computed verdict for one row, as plain data. */
export interface RowVerdict {
  noScale: boolean;
  alt: boolean;
  capture: boolean;
  isSource: boolean;
  flagged: boolean;
}

export interface Row {
  file: AssetFile;
  note: string;
  v: RowVerdict;
  /** Shown instead of the bare name when two rows in one panel share it. */
  label: string;
}

function mb(bytes: number): string {
  return bytes >= 1e6
    ? `${(bytes / 1e6).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

export default function DownloadList({
  asset,
  rows,
  declaredNonMetric,
}: {
  asset: Asset;
  rows: Row[];
  declaredNonMetric: boolean;
}) {
  // rel is not unique across an asset (object_asset/object_mesh.ply exists twice
  // in some manifests with different geometry), so the key is group + rel — the
  // same key the server uses for React's list identity.
  const shared = useContext(OpenRow);
  if (!shared) {
    // Failing loudly beats silently reverting to per-panel state, which is the
    // bug this context replaced and which looked fine in every static check.
    throw new Error("DownloadList must be rendered inside <ViewOneProvider>");
  }
  const { open, setOpen } = shared;
  const keyOf = (f: AssetFile) => `${f.group}/${f.rel}`;

  return (
    <ul className="dlist">
      {rows.map(({ file, note, v, label }) => {
        const k = keyOf(file);
        const canView = canRenderInline(file);
        const isOpen = open === k;
        return (
          <li key={k} className={v.isSource ? "isrc" : undefined}>
            <div className="dmeta">
              <code>{label}</code>
              <span className="dsize">{file.size}</span>
            </div>
            <p className="dnote">
              {v.isSource && <span className="rowsrc">measured from this file</span>}
              {v.noScale ? (
                <span className="rowwarn">
                  {declaredNonMetric
                    ? "no real-world size — this capture carried none"
                    : "no real-world size — generated proportions, not measured"}
                </span>
              ) : v.capture ? (
                <span className="rowwarn">the whole sweep, not just this object</span>
              ) : v.alt ? (
                <span className="rowwarn">comparison only — do not quote its size</span>
              ) : (
                <span className="muted">{note}</span>
              )}
            </p>
            <div className="dact">
              {/* Only for files this browser can actually draw. A .splat has its
                  own renderer and is shown whole-asset in the Photoreal panel,
                  so a per-row splat window would draw a different file than the
                  row it sits under. */}
              {canView && (
                <button
                  type="button"
                  className="vbtn"
                  aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : k)}
                >
                  {isOpen ? "✕ Close" : "◳ View"}
                </button>
              )}
              <a
                className="dl"
                href={downloadHref(file)}
                aria-label={`Download ${file.name}`}
              >
                ↓ Download
              </a>
            </div>
            {/* Mounted only while open, so closing it releases the WebGL
                context rather than parking it. `key` includes the file so
                switching rows cannot reuse a viewer still holding the old
                geometry. */}
            {isOpen && (
              <div className="dview">
                <Viewer key={k} asset={asset} only={file} compact />
                <p className="muted dviewfoot">
                  Showing <code>{file.name}</code> · {mb(file.bytes)}
                </p>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
