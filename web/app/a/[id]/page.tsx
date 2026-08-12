import Link from "next/link";
import { notFound } from "next/navigation";
import {
  getManifest,
  filesByTarget,
  dimsClaim,
  displayTitle,
  fileIsMetric,
  TARGET_ORDER,
  downloadHref,
  previewHref,
  type Asset,
  type AssetFile,
  type TargetId,
} from "../../../lib/manifest";
// Every verdict below is READ from these, never re-derived: lib/manifest.ts owns
// the one definition of metric-ness and a second copy is a regression (see its
// comment at the fileIsMetric declaration).
import {
  ALT_RECON_NOTE,
  NON_METRIC_UNITS,
  isAltReconstruction,
  isCaptureScale,
  unitsForFile,
} from "../../../lib/formats";
import { isDeclaredNonMetric } from "../../../lib/manifest";
import Viewer from "./Viewer";
import SplatEmbed from "./SplatEmbed";
import Formats from "./Formats";

export const dynamic = "force-dynamic";

/**
 * What is true about ONE downloadable file, for the row that carries its button.
 *
 * The page used to print the manifest's glob note beside every download while
 * the Formats table 30 lines above chipped the same file "no real-world scale":
 * on all five trellis assets the only CAD row was asset_trellis_mm.stl under
 * "Measure and dimension. Millimetres." Across the six live assets, 24 of ~100
 * target rows point at geometry the page condemns elsewhere.
 *
 * Gated on unitsForFile(), NOT on fileIsMetric() alone: fileIsMetric is
 * asset-level false for a declared non-metric asset, so the bare call would
 * stamp "no real size" on koala_kiri_web's .mtl, .jpg and on NO_METRIC_SCALE.txt
 * itself. unitsForFile restricts the question to geometry, and is the same call
 * Formats.tsx makes, so the two surfaces cannot drift.
 */
function fileVerdict(asset: Asset, file: AssetFile) {
  const noScale =
    unitsForFile(file.name, fileIsMetric(asset, file)) === NON_METRIC_UNITS;
  const alt = isAltReconstruction(file);
  const capture = isCaptureScale(file);
  const isSource = !!asset.measure && file.url === asset.measure.url;
  return { noScale, alt, capture, isSource, flagged: noScale || alt || capture };
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  // Every page in the site shared one tab title ("Scan assets"), so a customer
  // with three scans open could not tell the tabs apart.
  const { id } = await params;
  const manifest = await getManifest();
  const asset = manifest?.assets.find((a) => a.id === decodeURIComponent(id));
  if (!asset) return { title: "Scan not found" };
  const t = displayTitle(asset);
  const claim = dimsClaim(asset);
  return {
    title: claim.state === "measured" ? `${t.name} — ${claim.dims}` : t.name,
  };
}

export default async function AssetPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const manifest = await getManifest();
  const asset = manifest?.assets.find((a) => a.id === decodeURIComponent(id));
  if (!manifest || !asset) notFound();

  const grouped = filesByTarget(asset);
  const targets = manifest.targets;
  // One call, shared with the index card and the viewer, so the three surfaces
  // cannot give three answers about one figure. See dimsClaim in lib/manifest.
  const claim = dimsClaim(asset);
  const t = displayTitle(asset);
  // Which KIND of "no scale" this asset has: declared by the capture (no depth
  // frames) or a generative mesh's normalized output. lib/manifest.ts keeps the
  // two apart precisely so neither is told the other's story.
  const declaredNonMetric = isDeclaredNonMetric(asset);

  return (
    <>
      <header className="site">
        <div className="wrap">
          <h1>{t.name}</h1>
          <p className="sub">
            Spin it, tap two points to measure, or download the file for your app.
          </p>

          {/* The measurement IS the product, so it is the biggest thing here. It
              used to be 13px grey styled identically to "route trellis" beside
              it, while the largest text on the page was the asset id. Only the
              measured state is enlarged: a disputed figure and a missing one keep
              their existing coloured treatments below, because making those big
              would amplify a number the page is telling you not to trust.
              No axis labels — which extent is length vs depth depends on the
              file's frame, and guessing here would be a third answer to a
              question the viewer's AXIS_READOUT already answers. */}
          {claim.state === "measured" && (
            <div className="hero">
              <span className="fig">{claim.dims}</span>
              <span className="tol">± 1–2 in</span>
            </div>
          )}
          <p className="kv" style={{ marginTop: 14 }}>
            {/* The measured figure moved to the .hero block above; printing it
                here as well would put the same number on the page twice. */}
            {/* A figure the page itself disputes gets no tolerance chip. "±1–2 in"
                on a number that is nine times the object is not a wide tolerance,
                it is a different object, and this header is what a customer on a
                phone or with WebGL off reads instead of the viewer's banner. */}
            {claim.state === "disputed" && (
              <span>
                size <b>{claim.dims}</b>{" "}
                <span className="tol bad">not safe to quote</span>
              </span>
            )}
            {/* An RGB-only capture reconstructs to a normalized mesh, so the
                asset has a shape and no size. Saying so up here, not only in the
                viewer, keeps anyone from quoting a dimension off the download. */}
            {claim.state === "not-measurable" && (
              <span>
                size <b>not measurable</b>{" "}
                <span className="tol warn">no metric scale</span>
              </span>
            )}
            {/* dimsClaim's fourth state was unhandled on both surfaces: the row
                simply rendered nothing where every other asset shows a size,
                which reads as "still loading" rather than "none published". */}
            {claim.state === "silent" && (
              <span>
                size <b>not published</b>
              </span>
            )}
            {asset.mass_kg != null && (
              <span>
                mass <b>{asset.mass_kg.toFixed(1)} kg</b>
              </span>
            )}
            {/* "route trellis" was published jargon: it names which internal
                pipeline ran, which tells a customer nothing and is not a claim
                about what shipped. The three reconstructions are already listed
                by name in the viewer's file switcher. */}
            <span>
              processed <b>{asset.updated_iso.slice(0, 16).replace("T", " ")}</b>
            </span>
            <span>
              <b>{asset.files.length}</b> files
            </span>
          </p>

          {/* Above the fold, in the server render, in the same words the
              viewer's red banner uses. The whole point is that it survives no
              JavaScript, no WebGL and a reader who never scrolls. */}
          {claim.state === "disputed" && (
            <p className="hdrwarn">
              <b>Do not quote that size.</b> {claim.why}
            </p>
          )}

          {/* Which of the two things the printed figure is: the measurement, or
              a rounded restatement of it. Absent from every manifest published
              so far, so this whole block simply does not render for those. */}
          {claim.state === "measured" && claim.rounding && (
            <p className="muted hdrnote">
              {claim.rounding.quantized ? (
                <>
                  Rounded to the nearest{" "}
                  {claim.rounding.quantum_in ? `${claim.rounding.quantum_in} in` : "step"}
                  {claim.rounding.raw ? <> (measured {claim.rounding.raw})</> : null}.
                </>
              ) : (
                <>Shown as measured.</>
              )}
            </p>
          )}
        </div>
      </header>

      <div className="wrap">
        <Viewer asset={asset} />

        {/* Viewer above renders meshes; a .splat needs its own renderer, so it
            gets its own panel. Renders nothing when the asset has no splat. */}
        <SplatEmbed asset={asset} />

        {asset.preview && (
          <div className="panel">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {/* A reference photo, not a third hero: the viewer and the splat
                panel above are already showing this object full width. */}
            <img
              src={previewHref(asset.preview)}
              alt={`Preview image of ${t.name}`}
              style={{
                width: "100%", display: "block",
                maxHeight: 300, objectFit: "contain",
              }}
            />
          </div>
        )}

        {asset.frame && (
          <p className="muted" style={{ marginTop: 18 }}>
            For importers — coordinate frame: <code>{asset.frame}</code>
          </p>
        )}
        {asset.mass_source && (
          <p className="muted">Mass: {asset.mass_source}</p>
        )}

        {TARGET_ORDER.filter((t) => grouped.has(t)).map((t: TargetId) => {
          const info = targets[t];
          // Decide each row's verdict once, then order by it: the file the
          // printed size was actually measured from first, then ordinary files,
          // then the ones this page warns about. Previously the authoritative
          // file was simply wherever the glob put it — on coffee_table the
          // first Blender button was asset_trellis.glb and object_mesh.ply,
          // the file the 46.75 x 52.75 x 17.25 in came from, was fourth.
          const rows = grouped.get(t)!.map((r) => ({
            ...r,
            v: fileVerdict(asset, r.file),
          }));
          const rank = (v: ReturnType<typeof fileVerdict>) =>
            v.isSource ? 0 : v.flagged ? 2 : 1;
          rows.sort((a, b) => rank(a.v) - rank(b.v));
          // Two rows in one panel can share a name (dims.json beside
          // object_asset/dims.json, live on four of six assets), so show the
          // path whenever the bare name would be ambiguous.
          const dupes = new Set(
            rows
              .map((r) => r.file.name)
              .filter((n, i, all) => all.indexOf(n) !== i),
          );
          const allFlagged = rows.length > 0 && rows.every((r) => r.v.flagged);
          return (
            <div className="panel" key={t}>
              <h3>
                <span aria-hidden>{info?.icon}</span> {info?.name}
              </h3>
              <p className="note">{info?.blurb}</p>
              {/* The blurb string itself is not rewritten — it describes the
                  TARGET, and the same string is right for an asset whose files
                  are fine. This adds the asset-specific caveat beside it. */}
              {allFlagged && (
                <p className="panelwarn">
                  Nothing in this section carries a real-world size — see the
                  notes beside each file.
                </p>
              )}
              <div className="inner">
                <table>
                  <thead>
                    <tr>
                      <th>file</th>
                      <th>what it is for</th>
                      <th className="num">size</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(({ file, note, v }) => (
                      <tr
                        key={`${file.group}/${file.rel}`}
                        className={v.isSource ? "isrc" : undefined}
                      >
                        <td>
                          <code>{dupes.has(file.name) ? file.rel : file.name}</code>
                        </td>
                        {/* The verdict REPLACES the glob note when there is one,
                            rather than sitting beside it: "The part in
                            millimetres — import as a Mesh" next to "this has no
                            real-world scale" is a contradiction the customer has
                            to resolve. Wording is reused verbatim from the
                            Formats table and ALT_RECON_NOTE so the two surfaces
                            say the same words. */}
                        <td className="muted">
                          {v.isSource && (
                            <>
                              <span className="rowsrc">
                                The printed size was measured from this file.
                              </span>{" "}
                            </>
                          )}
                          {v.noScale ? (
                            <span className="rowwarn">
                              {declaredNonMetric
                                ? "No real-world scale — this capture carried none, so a measure tool's number is not a size."
                                : "Not a measurement — no real-world scale. A measure tool still returns a number; it describes the generator's proportions, not the object."}
                            </span>
                          ) : v.capture ? (
                            <span className="rowwarn">
                              Measurable, but not this object: it is the raw
                              capture the object was cut out of, so a measure tool
                              returns the size of the sweep.
                            </span>
                          ) : v.alt ? (
                            <span className="rowwarn">{ALT_RECON_NOTE}</span>
                          ) : (
                            note
                          )}
                        </td>
                        <td className="num muted">{file.size}</td>
                        <td style={{ textAlign: "right" }}>
                          <a
                            className="dl"
                            href={downloadHref(file)}
                            aria-label={`Download ${file.name}`}
                          >
                            ↓ Download
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}

        {/* The same files were listed twice, by two different keys: Formats
            groups by file FORMAT and carries the caveats but no download links,
            while the panels above group by TARGET and carry every button. Those
            caveats now sit on the rows themselves (see fileVerdict), so this is
            reference material — kept in full, one click away, instead of a
            second full-width table the customer has to reconcile with the first. */}
        <details className="panel" style={{ padding: "0 0 4px" }}>
          <summary
            style={{
              cursor: "pointer", padding: "14px 18px", fontSize: 15,
              fontWeight: 600, listStyle: "revert",
            }}
          >
            All formats in this scan, and which apps open them
          </summary>
          <Formats asset={asset} />
        </details>

        <Link href="/" className="back">
          ← all scans
        </Link>
      </div>
    </>
  );
}
