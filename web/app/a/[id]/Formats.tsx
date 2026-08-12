import Link from "next/link";
// Metric-ness is NOT decided here. lib/manifest.ts owns the one definition and
// the viewer on this same page reads the same functions, so the tape measure
// and the unit chips cannot disagree. A local copy of this logic is what put a
// "metres" chip on mouse_ewa_regen's unit-cube asset_trellis.glb while the
// viewer beside it said the model has no real-world scale.
import {
  fileIsMetric,
  isDeclaredNonMetric,
  type Asset,
} from "../../../lib/manifest";
import {
  FORMATS,
  FORMAT_ORDER,
  NON_METRIC_UNITS,
  PRECISION_NOTE,
  appsByTier,
  extOf,
  isCaptureScale,
  isAltReconstruction,
  ALT_RECON_NOTE,
  simFilesMissingMeshes,
  unitsForFile,
  type FormatInfo,
} from "../../../lib/formats";

// Only the formats THIS asset actually contains. Listing the full table on
// every asset page was the first version and it was worse than useless — a
// customer with three files had to scan eleven rows to find out that none of
// the simulation ones applied to them. The complete reference lives at /help.

interface Row {
  info: FormatInfo;
  count: number;
  names: string[];
  units: string[];
  /**
   * Names in this row that carry no real-world scale.
   *
   * Needed only because a row can hold both kinds at once: DC22F084 ships five
   * .glb, four of them metric and asset_trellis.glb not. Two chips over one
   * shared name list would leave the customer to guess which file each chip
   * belongs to, and guessing wrong here is the fabricated-size failure again.
   */
  noScale: string[];
  /**
   * How many files in this row carry no scale — a count, not the deduped name
   * list, so it can be compared against `count`. When every file of a format is
   * unmeasurable, the "measuring in it" cell must not print the measuring
   * recipe: on 76B73843 the only .stl is asset_trellis_mm.stl, and the column
   * beside a "no real-world scale" chip was telling the customer to import it
   * into Onshape and read the Measure tool.
   */
  noScaleCount: number;
  /**
   * Paths in this row that are the capture, not the object.
   *
   * Held as `rel`, not `name`, because the name is precisely what fails here:
   * BE53A423 ships mesh_preview/object_mesh_m.ply beside
   * object_asset/object_mesh.ply, the name list above shows both as
   * object_mesh*.ply, and both are chipped "metres" — correctly, since both are
   * in metres. The folder is the only thing that separates the object from the
   * room it was standing in, so the folder is what gets printed.
   */
  captureScale: string[];
  /** Second reconstructions of the same capture: metric, but not the object. */
  altRecon: string[];
  /** How many files in this row are capture-scale — a count, against `count`. */
  captureScaleCount: number;
  altReconCount: number;
  /** Files of this format that reference meshes this download does not have. */
  orphans: number;
}

const MAX_NAMES = 5;

function rowsFor(asset: Asset): Row[] {
  const byExt = new Map<string, Row>();
  const orphaned = new Set(simFilesMissingMeshes(asset.files).map((f) => f.url));

  for (const file of asset.files) {
    const ext = extOf(file.name);
    const info = FORMATS[ext];
    if (!info) continue; // an extension the table does not cover yet
    let row = byExt.get(ext);
    if (!row) {
      row = {
        info,
        count: 0,
        names: [],
        units: [],
        noScale: [],
        noScaleCount: 0,
        captureScale: [],
        captureScaleCount: 0,
        altRecon: [],
        altReconCount: 0,
        orphans: 0,
      };
      byExt.set(ext, row);
    }
    row.count += 1;
    if (!row.names.includes(file.name)) row.names.push(file.name);
    if (orphaned.has(file.url)) row.orphans += 1;
    // Independent of the unit chip, and it has to be: these files ARE metric.
    // lib/formats.ts owns the folder rule; lib/manifest.ts owns metric-ness;
    // neither answers the other's question.
    if (isCaptureScale(file)) {
      row.captureScaleCount += 1;
      if (!row.captureScale.includes(file.rel)) row.captureScale.push(file.rel);
    }
    // Same third state as captureScale, opposite direction: an alternate
    // reconstruction is metric AND smaller than the object rather than larger.
    if (isAltReconstruction(file)) {
      row.altReconCount += 1;
      if (!row.altRecon.includes(file.name)) row.altRecon.push(file.name);
    }
    // Per file, twice over. OBJ ships in both mm and metres in the same
    // download, and scale varies inside one asset too: a capture can ship a
    // measured RGB-D fuse alongside a generative rebuild of the same object,
    // and only one of those two has a size. Collect every unit present so the
    // .obj row can show "millimetres" and "none" side by side rather than
    // picking one of them for all of its files.
    const u = unitsForFile(file.name, fileIsMetric(asset, file));
    if (!row.units.includes(u)) row.units.push(u);
    if (u === NON_METRIC_UNITS) {
      row.noScaleCount += 1;
      if (!row.noScale.includes(file.name)) row.noScale.push(file.name);
    }
  }
  const order = new Map(FORMAT_ORDER.map((e, i) => [e, i]));
  return [...byExt.entries()]
    .sort((a, b) => (order.get(a[0]) ?? 99) - (order.get(b[0]) ?? 99))
    .map(([, row]) => row);
}

/**
 * The URDF/MJCF-without-its-mesh case, stated plainly.
 *
 * The format panel's normal advice ("measure the meshes it points at",
 * "download the whole folder") is impossible to follow for these, so saying it
 * anyway sends the customer looking for files that were never published.
 */
function OrphanNote({ info, orphans, count }: Row) {
  const one = orphans === 1;
  const subject =
    orphans === count
      ? one
        ? "this file"
        : `all ${count} of these files`
      : `${orphans} of these ${count} files`;
  return (
    <p style={{ margin: "8px 0 0", color: "var(--warn)", fontSize: 13 }}>
      In this download: {subject} {one ? "ships" : "ship"} with no mesh beside{" "}
      {one ? "it" : "them"}. A <code>{info.ext}</code> is a description — the
      shape lives in a separate mesh file it names by relative path, and that
      mesh is not part of this download, so there is nothing here to load,
      render or measure. The mass and inertia values inside the file are still
      readable. Ask for the meshes to be published rather than hunting for a
      copy.
    </p>
  );
}

function AppList({ info }: { info: FormatInfo }) {
  const free = appsByTier(info, "free");
  const paid = appsByTier(info, "paid");
  return (
    <>
      {free.length > 0 && (
        <p style={{ margin: "0 0 6px" }}>
          <b style={{ color: "var(--good)", fontSize: 12 }}>FREE</b>{" "}
          {free.map((a, i) => (
            <span key={a.name}>
              {i > 0 && <span className="muted"> · </span>}
              {a.name}
              {a.note && <span className="muted"> ({a.note})</span>}
            </span>
          ))}
        </p>
      )}
      {paid.length > 0 && (
        <p style={{ margin: 0 }}>
          <b style={{ color: "var(--dim)", fontSize: 12 }}>PAID</b>{" "}
          {paid.map((a, i) => (
            <span key={a.name}>
              {i > 0 && <span className="muted"> · </span>}
              {a.name}
              {a.note && <span className="muted"> ({a.note})</span>}
            </span>
          ))}
        </p>
      )}
    </>
  );
}

export default function Formats({ asset }: { asset: Asset }) {
  const rows = rowsFor(asset);
  if (rows.length === 0) return null;
  // A "none" chip on its own reads like missing data. Say what it means once,
  // under the table, rather than in a chip four words wide.
  const anyNonMetric = rows.some((r) => r.units.includes(NON_METRIC_UNITS));
  // Unmeasurable files that nonetheless open at a believable size, because a
  // depth pass rescaled them: asset_trellis_mm.stl / .obj. "Normalized to
  // roughly a unit cube, reads as tens of inches" is the right warning for a
  // raw asset_trellis.glb and the wrong one for these — 76B73843's mm STL
  // opens at length 7.31 / width 7.27 / height 14.00 in (raw x,y,z
  // 184.7 x 355.6 x 185.6 mm, read in the y-up frame the file is authored in),
  // which looks like a measurement and is not: the scan says
  // 13.25 x 10.75 x 14.00, and only the longest edge — here the height — was
  // matched. Nor is the fitted edge always laid on the axis it belongs to: on
  // 1B38880A the mesh's longest edge is its height and it was scaled to the
  // printed WIDTH, leaving all three axes wrong.
  // Named only when the download actually contains one; a customer whose asset
  // is a bare asset_trellis.glb has no such file to be warned about.
  const rescaled = rows
    .flatMap((r) => r.noScale)
    .filter((n) => /_mm\.(stl|obj)$/i.test(n));
  // The third state: in true metres, and not this object. No chip can carry it
  // — the chip says "metres" and is right — so the page has to say it in words,
  // naming folders, and only when the download actually contains one.
  const captureScale = rows.flatMap((r) => r.captureScale);

  return (
    <div className="panel">
      <h3>
        <span aria-hidden>🧰</span> What opens these files
      </h3>
      <p className="note">
        Every format in this download, the units it is written in, and where the
        measurement lives. Full reference for all formats:{" "}
        <Link href="/help">file format guide</Link>.
      </p>
      <div className="inner">
        <table>
          <thead>
            <tr>
              <th style={{ width: "17%" }}>format</th>
              <th style={{ width: "31%" }}>what it is</th>
              <th style={{ width: "27%" }}>opens in</th>
              <th style={{ width: "25%" }}>measuring in it</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.info.ext}>
                <td style={{ verticalAlign: "top" }}>
                  <code>{row.info.ext}</code>
                  <p className="muted" style={{ marginTop: 6 }}>
                    {row.count} file{row.count === 1 ? "" : "s"}
                  </p>
                  <div className="chips" style={{ marginTop: 6 }}>
                    {row.units.map((u) => (
                      <span className="chip" key={u}>
                        {u}
                      </span>
                    ))}
                  </div>
                  <p className="muted" style={{ marginTop: 8, wordBreak: "break-all" }}>
                    {row.names.slice(0, MAX_NAMES).join(", ")}
                    {row.names.length > MAX_NAMES &&
                      ` +${row.names.length - MAX_NAMES} more`}
                  </p>
                  {row.units.length > 1 && row.noScale.length > 0 && (
                    <p
                      style={{
                        margin: "6px 0 0",
                        color: "var(--warn)",
                        fontSize: 12,
                        wordBreak: "break-all",
                      }}
                    >
                      no scale: {row.noScale.join(", ")}
                    </p>
                  )}
                  {/* Unconditional where present, unlike the "no scale" line
                      above: that one is redundant when the chip already says
                      "none", but no chip ever says this. */}
                  {row.captureScale.length > 0 && (
                    <p
                      style={{
                        margin: "6px 0 0",
                        color: "var(--warn)",
                        fontSize: 12,
                        wordBreak: "break-all",
                      }}
                    >
                      not this object: {row.captureScale.join(", ")}
                    </p>
                  )}
                  {row.altRecon.length > 0 && (
                    <p
                      style={{
                        margin: "6px 0 0",
                        color: "var(--warn)",
                        fontSize: 12,
                        wordBreak: "break-all",
                      }}
                    >
                      second reconstruction, do not measure:{" "}
                      {row.altRecon.join(", ")}
                    </p>
                  )}
                </td>
                <td style={{ verticalAlign: "top" }}>
                  <p style={{ margin: 0 }}>{row.info.what}</p>
                  {row.orphans > 0 && <OrphanNote {...row} />}
                  {row.info.trap && (
                    <p style={{ margin: "8px 0 0", color: "var(--warn)", fontSize: 13 }}>
                      Watch out: {row.info.trap}
                    </p>
                  )}
                </td>
                <td style={{ verticalAlign: "top", fontSize: 13 }}>
                  <AppList info={row.info} />
                </td>
                <td className="muted" style={{ verticalAlign: "top" }}>
                  {row.orphans > 0 && row.orphans === row.count
                    ? "Nothing to measure here — the meshes these files point at are not in this download."
                    : row.noScaleCount === row.count
                      ? `Not a measurement. Every ${row.info.ext} in this download carries the “${NON_METRIC_UNITS}” chip beside it. A measure tool will still return a number for one of these; it describes the generator's proportions, not the object. See the note under this table.`
                      : row.captureScaleCount === row.count
                        ? `Measurable, but not this object. Every ${row.info.ext} in this download is the raw capture the object was cut out of, so a measure tool returns the size of the sweep. See the note under this table.`
                        : (row.info.measure ?? "Not a measuring format.")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {anyNonMetric && (
          <p className="muted" style={{ marginTop: 14 }}>
            <b style={{ color: "var(--warn)" }}>
              {NON_METRIC_UNITS}:
            </b>{" "}
            {/*
              Two different facts, and only one of them is ever provable. The
              marker file and the published flag are claims about the CAPTURE
              ("no depth frames"); a generative mesh with neither is only a
              claim about that FILE. Telling the second customer a story about
              their capture would be inventing a cause.
            */}
            {isDeclaredNonMetric(asset)
              ? "this capture had no depth data — NO_METRIC_SCALE.txt in the download is the pipeline saying so — and every mesh it exported was reconstructed from the photographs alone."
              : // Not "no depth pass ever sized it": on a *_trellis asset a depth
                // pass did size the _mm exports, and the paragraph goes on to say
                // so. What is true of all of them is that nothing measured the
                // shape itself.
                "the files chipped that way above are generative reconstructions: the geometry was invented from photographs by a model, and nothing measured it."}{" "}
            A generator normalizes its output to roughly a unit cube, so{" "}
            {rescaled.length > 0 ? "the raw meshes above carry" : "those files carry"}{" "}
            proportions and no dimensions at all. Any viewer will still print a
            number for them — in Blender it typically lands in the tens of
            inches whatever the real object was. That number is a fabrication.
            Do not quote it, do not scale from it, and do not crate to it.
            {rescaled.length > 0 && (
              <>
                {" "}
                <b>{rescaled.join(" and ")}</b>{" "}
                {rescaled.length === 1 ? "is" : "are"} the same invented
                geometry after a rescale against the RGB-D scan, which makes{" "}
                {rescaled.length === 1 ? "it" : "them"} more dangerous rather
                than less: {rescaled.length === 1 ? "it opens" : "they open"} at
                a believable size, in honest millimetres. Only the bounding box
                was ever fitted — on some exports along a single edge, the
                longest, and not necessarily onto the axis it belongs to, which
                leaves axes several inches from the size printed at the top of
                this page — and the shape inside that box is still the
                generator&rsquo;s. What the printed size was
                measured off is the RGB-D fuse those exports were scaled from
                (<code>object_mesh.ply</code>), not the exports themselves.
              </>
            )}
          </p>
        )}
        {rows.flatMap((r) => r.altRecon).length > 0 && (
          <p className="muted" style={{ marginTop: 14 }}>
            <b style={{ color: "var(--warn)" }}>
              {rows.flatMap((r) => r.altRecon).join(", ")}
            </b>{" "}
            {ALT_RECON_NOTE}
          </p>
        )}
        {captureScale.length > 0 && (
          <p className="muted" style={{ marginTop: 14 }}>
            <b style={{ color: "var(--warn)" }}>
              In metres, and still not this object:
            </b>{" "}
            <b>{captureScale.join(", ")}</b>{" "}
            {captureScale.length === 1 ? "is" : "are"} the raw on-device sweep
            this scan was cut out of — the floor and whatever else was in frame
            — kept beside the result for reference. The unit chip above is
            correct and that is the problem:{" "}
            {captureScale.length === 1 ? "this file is" : "these files are"} in
            real metres, so no chip can warn you off{" "}
            {captureScale.length === 1 ? "it" : "them"}. Measure{" "}
            {captureScale.length === 1 ? "it" : "one"} and you get the extent of
            the sweep, floor included, which is feet across whatever the object
            is. The filename is nearly the one you want, so go by the folder:
            the printed size at the top of this page was measured from the mesh
            under <code>object_asset/</code>. The viewer at the top of this page
            never loads {captureScale.length === 1 ? "this file" : "these files"}
            , for the same reason.
          </p>
        )}
        <p className="muted" style={{ marginTop: 14 }}>
          <b style={{ color: "var(--warn)" }}>Precision:</b> {PRECISION_NOTE}
        </p>
      </div>
    </div>
  );
}
