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
          {/* Back link first, and always present. It used to be a single "← all
              scans" at the very bottom of a long page, so the only way back was
              the browser button. */}
          <p className="crumb">
            <Link href="/">← All scans</Link>
          </p>
          <h1>{t.name}</h1>

          {/* The size, and then nothing else. This header carried a five-item
              key/value row — mass, processed timestamp, file count — plus a
              rounding note. Only the size is why anyone opened the page; the
              rest is in the viewer's Details block and the downloads. */}
          {claim.state === "measured" && (
            <div className="hero">
              <span className="fig">{claim.dims}</span>
              <span className="tol">± 1–2 in</span>
            </div>
          )}
          {/* A figure the page itself disputes gets no tolerance chip and no big
              type. "±1–2 in" on a number nine times the object is not a wide
              tolerance, it is a different object — and this header is what a
              customer on a phone or with WebGL off reads instead of the viewer's
              banner. */}
          {claim.state === "disputed" && (
            <p className="hdrwarn">
              <b>Size not safe to quote.</b> {claim.why}
            </p>
          )}
          {claim.state === "not-measurable" && (
            <p className="sub">
              <span className="chip warn">no real size</span> Shape only — this
              capture carried no depth.
            </p>
          )}
          {/* dimsClaim's fourth state was unhandled on both surfaces: the row
              simply rendered nothing where every other asset shows a size, which
              reads as "still loading" rather than "none published". */}
          {claim.state === "silent" && (
            <p className="sub">
              <span className="chip">size not published</span>
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

        {/* The coordinate frame and the mass provenance were two grey lines of
            body text between the pictures and the downloads. The frame moved
            into the viewer's Details block, where the rest of the importer
            information already lives. */}
        {asset.mass_kg != null && (
          <p className="muted" style={{ marginTop: 18 }}>
            Mass <b style={{ color: "var(--fg)" }}>{asset.mass_kg.toFixed(1)} kg</b>
            {asset.mass_source ? ` — ${asset.mass_source}` : ""}
          </p>
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
                  Nothing here carries a real-world size — see each file below.
                </p>
              )}
              {/* Blocks, not a four-column table with a "file / what it is for /
                  size" header. The middle column carried up to two sentences per
                  row and on a phone the table's own header cost a third of the
                  width — so the filename wrapped mid-word and the download
                  button was off-screen. Same rows, same order, same verdicts. */}
              <div className="inner">
                <ul className="dlist">
                  {rows.map(({ file, note, v }) => (
                    <li
                      key={`${file.group}/${file.rel}`}
                      className={v.isSource ? "isrc" : undefined}
                    >
                      <div className="dmeta">
                        <code>{dupes.has(file.name) ? file.rel : file.name}</code>
                        <span className="dsize">{file.size}</span>
                      </div>
                      {/* Short form of the same verdict, and it still REPLACES
                          the glob note rather than sitting beside it: "The part
                          in millimetres — import as a Mesh" next to "this has no
                          real-world scale" is a contradiction the customer has to
                          resolve. The long wording lives in All formats below. */}
                      <p className="dnote">
                        {v.isSource && (
                          <span className="rowsrc">measured from this file</span>
                        )}
                        {v.noScale ? (
                          <span className="rowwarn">
                            {declaredNonMetric
                              ? "no real-world size — this capture carried none"
                              : "no real-world size — generated proportions, not measured"}
                          </span>
                        ) : v.capture ? (
                          <span className="rowwarn">
                            the whole sweep, not just this object
                          </span>
                        ) : v.alt ? (
                          <span className="rowwarn">comparison only — do not quote its size</span>
                        ) : (
                          <span className="muted">{note}</span>
                        )}
                      </p>
                      <a
                        className="dl"
                        href={downloadHref(file)}
                        aria-label={`Download ${file.name}`}
                      >
                        ↓ Download
                      </a>
                    </li>
                  ))}
                </ul>
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
            All formats, and which apps open them
          </summary>
          <Formats asset={asset} />
        </details>
      </div>
    </>
  );
}
