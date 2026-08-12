import Link from "next/link";
import {
  getManifest,
  dimsClaim,
  displayTitle,
  previewHref,
} from "../lib/manifest";

// Always render fresh: a publish from the GPU box must show up without a
// redeploy, so nothing here may be cached at build time.
export const dynamic = "force-dynamic";

/**
 * One surface, one kind of thing on it.
 *
 * There used to be a second section above the grid: a strip of links to
 * standalone comparison pages from blob storage. It looked like a peer of the
 * scan blocks and behaved nothing like one — a different page, a different
 * viewer, its own rounding, and a second place to measure that could disagree
 * with the scan's own page about the same object. Two sections that both looked
 * like "your stuff" is the confusion; the comparison pages are an internal
 * pipeline-evaluation tool, not a customer deliverable, so they are simply not
 * linked here. They remain reachable by URL for us.
 */

export default async function Home() {
  const manifest = await getManifest();

  if (!manifest) {
    return (
      <>
        <header className="site">
          <div className="wrap">
            <h1>CrateScanner <span className="qual">(Mesh &amp; 3DGS)</span></h1>
            <p className="sub">Nothing published yet.</p>
          </div>
        </header>
        <div className="wrap">
          {/* A paying customer behind the passphrase used to read a Python
              command and an environment variable name here, with no retry and
              nobody to contact. The operator detail is still one click away,
              because this same screen appears for a misconfiguration. */}
          <div className="empty">
            <p style={{ color: "var(--fg)", fontSize: 15 }}>
              Your scans could not be loaded just now.
            </p>
            <p>This is on our side, not yours. Reload in a moment, and if it
              keeps happening tell us — nothing you uploaded is lost.</p>
            <p style={{ marginTop: 18 }}>
              <a className="dl" href="/">Reload</a>
            </p>
            <details style={{ marginTop: 22, textAlign: "left" }}>
              <summary className="muted" style={{ cursor: "pointer" }}>
                Operator details
              </summary>
              <p className="muted" style={{ marginTop: 10 }}>
                No manifest at <code>NEXT_PUBLIC_MANIFEST_URL</code>. Publish with{" "}
                <code>
                  python tools/publish_dashboard.py --uploader vercel --access private
                </code>
                , then set that variable to the URL it prints.
              </p>
            </details>
          </div>
        </div>
      </>
    );
  }

  const { assets } = manifest;

  return (
    <>
      <header className="site">
        <div className="wrap">
          <h1>CrateScanner <span className="qual">(Mesh &amp; 3DGS)</span></h1>
          {/* One line. This held a three-step numbered strip and a sub-heading
              that between them said "pick a scan, spin it, measure it, download
              it" — instructions for a grid of pictures that needs none. */}
          <p className="sub">Tap a scan to spin it, measure it, download it.</p>
        </div>
      </header>

      <div className="wrap">
        <div className="grid">
          {assets.map((a) => {
            // The same call the asset header and the viewer make, so the card
            // cannot print a size the page it links to disputes. It used to
            // print a.dims raw: no metric check, no mismatch check, so a
            // room-scale figure and a normalized mesh's fabricated size would
            // both have shown here as a plain measurement.
            const claim = dimsClaim(a);
            const t = displayTitle(a);
            return (
            <Link key={a.id} href={`/a/${encodeURIComponent(a.id)}`} className="card">
              <div className="thumb">
                {a.preview ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={previewHref(a.preview)}
                    alt={`Preview image of ${t.name}`}
                    loading="lazy"
                  />
                ) : (
                  <span className="none">no photo</span>
                )}
              </div>
              {/* Name, size, date. The row of target chips below this ("🧊 Blender,
                  📐 CAD, 🤖 Isaac Sim, 🌐 Web") repeated on every card and named
                  our export targets rather than telling anyone which scan this
                  is — and the file count is a fact about our packaging. Which
                  formats a scan carries is a question you ask once you have
                  opened it, and the scan's own page answers it. */}
              <div className="body">
                <h3>{t.name}</h3>
                {/* The size IS the product, so on the card it is the figure, not
                    a grey caption. Only the measured state gets it: a disputed
                    number is one a customer copies, and there is no room here
                    for the sentence that explains why not. */}
                {claim.state === "measured" && (
                  <p className="csize">
                    {claim.dims} <span className="tol">± 1–2 in</span>
                  </p>
                )}
                {claim.state === "disputed" && (
                  <p className="muted">
                    <span className="chip bad">size not safe to quote</span>
                  </p>
                )}
                {claim.state === "not-measurable" && (
                  <p className="muted">
                    <span className="chip warn">no real size</span>
                  </p>
                )}
                {/* dimsClaim's fourth state. It was unhandled here and on the
                    asset page, so an asset with no dims string at all rendered a
                    card with a heading and nothing where every sibling shows a
                    size — indistinguishable from a card still loading. */}
                {claim.state === "silent" && (
                  <p className="muted">
                    <span className="chip">size not published</span>
                  </p>
                )}
                {t.scannedOn && <p className="muted">Scanned {t.scannedOn}</p>}
              </div>
            </Link>
            );
          })}
        </div>

        {/* The "Which file do I need?" link used to sit in the header competing
            with the scans. It is a real page and has to stay reachable, but it is
            a question you have after picking a scan, not before. */}
        <p className="foot muted">
          <Link href="/help">Which file do I need?</Link>
          <span>updated {manifest.generated.slice(0, 16).replace("T", " ")}</span>
        </p>
      </div>
    </>
  );
}
