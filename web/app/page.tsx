import Link from "next/link";
import {
  getManifest,
  dimsClaim,
  displayTitle,
  TARGET_ORDER,
  previewHref,
  type TargetId,
} from "../lib/manifest";

// Always render fresh: a publish from the GPU box must show up without a
// redeploy, so nothing here may be cached at build time.
export const dynamic = "force-dynamic";

// Standalone comparison pages live in blob storage beside the assets, not in
// the manifest — they compare pipelines rather than describing one capture.
// They are served through the same authorised proxy as every other blob, and
// ?cache=0 because the blob CDN will otherwise hand back a previous version of
// an overwritten stable pathname.
// The list lives in blob storage (benchmark/index.json), written by recon3's
// `page` stage — a freshly processed capture links itself here with NO
// redeploy, exactly like assets appearing via the manifest. Fetched privately
// server-side; failure degrades to "no links", never a broken page.
const BENCHMARK_INDEX =
  "https://sf4pvvi6x7hevyhw.private.blob.vercel-storage.com/dashboard/benchmark/index.json?cache=0";

async function getBenchmarks(): Promise<{ label: string; href: string }[]> {
  try {
    const { fetchBlob } = await import("../lib/blob");
    const res = await fetchBlob(BENCHMARK_INDEX);
    if (!res.ok) return [];
    const idx = (await res.json()) as { pages?: { label: string; blob: string }[] };
    return (idx.pages ?? []).map((b) => ({
      label: b.label,
      href: `/api/download?inline=1&name=benchmark.html&url=${encodeURIComponent(b.blob)}`,
    }));
  } catch {
    return [];
  }
}

export default async function Home() {
  const manifest = await getManifest();
  const BENCHMARKS = await getBenchmarks();

  if (!manifest) {
    return (
      <>
        <header className="site">
          <div className="wrap">
            <h1>Scan assets</h1>
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

  const { assets, targets } = manifest;

  return (
    <>
      <header className="site">
        <div className="wrap">
          <h1>Your scans</h1>
          <p className="sub">
            Open a scan to spin it, read its size, and download the file your app
            needs.
          </p>
          {/* What the customer can do, in place of the store telemetry that used
              to hold the first screen — "6 scans · 100 files · 425.6 MB" was
              three facts about our storage and one that restated the six visible
              cards. No tolerance figure here on purpose: ±1–2 in is a property
              of a scan that WAS measured, not a promise the site can make about
              every card (some carry no scale at all). */}
          <ol className="steps">
            <li>
              <b>1</b> Pick a scan
            </li>
            <li>
              <b>2</b> Spin it, tap two points to measure
            </li>
            <li>
              <b>3</b> Download for Blender, CAD, a simulator or the web
            </li>
          </ol>
          <p className="muted" style={{ marginTop: 10 }}>
            <Link href="/help">Which file do I need? →</Link>
            <span style={{ marginLeft: 18 }}>
              updated {manifest.generated.slice(0, 16).replace("T", " ")}
            </span>
          </p>

          {/* Comparison pages are whole pages, not assets, so they have no card
              in the grid and were previously reachable only by pasting a URL.
              Labelled rather than left as bare pills: "▤ Sofa" beside a download
              button gave no clue it was a different KIND of thing. Described as
              a comparison, NOT as somewhere to measure — those pages are a
              second measuring surface that does not yet share this app's
              rounding or its per-file guards. */}
          {BENCHMARKS.length > 0 && (
            <div className="strip">
              <h2>Compare the reconstructions</h2>
              <p className="muted" style={{ marginBottom: 8 }}>
                Photo mesh, AI mesh and splat side by side for one scan. For
                sizes, use the scan&rsquo;s own page.
              </p>
              <div className="row">
                {BENCHMARKS.map((b) => (
                  <a key={b.label} className="dl" href={b.href}>
                    {b.label.replace(/^▤\s*/, "")}
                  </a>
                ))}
              </div>
            </div>
          )}
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
              <div className="body">
                <h3>{t.name}</h3>
                {claim.state === "measured" && (
                  <p className="muted">
                    {claim.dims} <span className="tol">± 1–2 in</span>
                  </p>
                )}
                {/* No figure on the card for a disputed one. There is no room
                    here for the sentence that explains it, and a number with no
                    explanation is the thing a customer copies. */}
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
                <p className="muted">
                  {t.scannedOn
                    ? `Scanned ${t.scannedOn} · ${a.files.length} files`
                    : `${a.files.length} files · ${a.updated_iso.slice(0, 10)}`}
                </p>
                <div className="chips">
                  {TARGET_ORDER.filter((t) => a.targets.includes(t)).map((t) => (
                    <span key={t} className="chip on" title={targets[t]?.name}>
                      {targets[t]?.icon} {targets[t]?.name.split(" / ")[0]}
                    </span>
                  ))}
                </div>
              </div>
            </Link>
            );
          })}
        </div>
      </div>
    </>
  );
}
