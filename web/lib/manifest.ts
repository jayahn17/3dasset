// The manifest is written by tools/publish_dashboard.py and uploaded to blob
// storage alongside the assets. Fetching it at request time (rather than
// bundling it) is what lets a new scan appear with no redeploy.

export type TargetId = "cad" | "blender" | "mujoco" | "isaac" | "ros" | "web" | "generic";

export interface TargetInfo {
  name: string;
  icon: string;
  blurb: string;
}

export interface AssetFile {
  name: string;
  rel: string;
  url: string;
  size: string;
  bytes: number;
  group: string;
  targets: { target: TargetId; note: string }[];
}

/**
 * The single piece of geometry the customer should measure.
 *
 * `Asset.dims` is computed from exactly one file, and which file that is
 * changes with the route (the RGB-D fuse's object_mesh.ply for a scanned
 * object, the millimetre CAD mesh for anything that went through the splat
 * route). Picking the wrong one is not cosmetic: on the couch the two routes
 * differ by 25 inches. This field records the file the number actually came
 * from so the viewer can load that one and nothing else.
 */
export interface AssetMeasure {
  /** Manifest-relative path; matches an AssetFile.rel. */
  rel: string;
  /** Same as that file's url — the reliable key, since `rel` repeats across groups. */
  url: string;
  name: string;
  /** Human sentence naming the pipeline that produced the dims. */
  source: string;
  bytes: number;
}

export interface Asset {
  id: string;
  title: string;
  files: AssetFile[];
  targets: TargetId[];
  sources: string[];
  kinds: string[];
  updated_iso: string;
  preview?: string;
  dims?: string;
  /**
   * The same measurement, unrounded — "259.20 in × 262.01 in × 127.56 in".
   *
   * Absent when the source recorded no raw figure. Not a more accurate number
   * than `dims`: the two differ by at most half the rounding grid (0.125 in),
   * two orders below the ±1–2 in this pipeline actually resolves.
   */
  dims_raw?: string;
  /**
   * true  -> `dims` is a rounded restatement of `dims_raw`.
   * false -> `dims` IS the measurement (the two strings are equal).
   * ABSENT -> unknown, which is every manifest published before the field
   *           existed. Callers must say nothing rather than guess.
   *
   * Written by tools/publish_dashboard.py, which derives it by comparing the
   * two strings it is about to publish, so it cannot disagree with them. It
   * exists because the two routes quantise differently and the consumer cannot
   * see which one ran: the RGB-D fuse snaps to dims.json's 0.25 in crate grid
   * while the splat -> CAD route prints cad_report.json's raw AABB. Without
   * this, DC22F084's genuinely unrounded 85.74 in and 76B73843's snapped
   * 13.25 in are indistinguishable strings.
   */
  dims_quantized?: boolean;
  /**
   * The grid `dims` was snapped to, in inches (0.25). Present only when
   * dims_quantized is true.
   *
   * A ROUNDING CONVENTION, NOT AN ACCURACY CLAIM — real precision here is
   * ±1–2 in, an order coarser than the grid. lib/formats.ts PRECISION_NOTE is
   * the sentence that says so, and nothing may present this number as a
   * tolerance.
   */
  dims_quantum_in?: number;
  /**
   * Present when the publisher knows the printed figure cannot be stood
   * behind: one sentence saying why, printed verbatim.
   *
   * Absent means "no dispute known", NOT "verified" — no manifest published so
   * far carries it, and the server-side room-scale check in dimsClaim() is
   * what covers that gap. See DISPUTED_* below.
   */
  dims_disputed?: string;
  dims_mm?: Record<string, number>;
  frame?: string;
  mass_kg?: number;
  mass_source?: string;
  route?: string;
  has_render_check?: boolean;
  measure?: AssetMeasure;
  /**
   * False when this asset's geometry carries NO real-world scale.
   *
   * Written by tools/publish_dashboard.py when NO_METRIC_SCALE.txt is among the
   * published files, i.e. an RGB-only capture that TRELLIS reconstructed
   * normalized to roughly a unit cube. Undefined does NOT mean metric: every
   * manifest published so far predates this field, so it is one signal of three.
   * Ask hasMetricScale()/fileIsMetric() below, never this field directly.
   * publish_dashboard also withholds `measure` whenever this is false — there is
   * no file whose numbers mean anything.
   */
  metric?: boolean;
}

export interface Manifest {
  generated: string;
  targets: Record<TargetId, TargetInfo>;
  assets: Asset[];
  stats: { assets: number; files: number; size: string };
}

const FALLBACK = "/manifest.json";

/** Absolute blob URL in production; the local publish output in development. */
export function manifestUrl(): string {
  return process.env.NEXT_PUBLIC_MANIFEST_URL || FALLBACK;
}

export async function getManifest(): Promise<Manifest | null> {
  const url = manifestUrl();
  try {
    // Relative URLs cannot be fetched server-side, so read those from disk.
    if (url.startsWith("/")) {
      const { readFile } = await import("node:fs/promises");
      const { join } = await import("node:path");
      const raw = await readFile(join(process.cwd(), "public", url.slice(1)), "utf8");
      return JSON.parse(raw) as Manifest;
    }
    // The store is private, so the manifest needs store credentials too — and
    // fetching it here, on the server, keeps the token off the client.
    // no-store: the whole point is that a fresh publish shows up immediately.
    const { fetchBlob } = await import("./blob");
    const res = await fetchBlob(url, undefined, /* bypassCache */ true);
    if (!res.ok) return null;
    return (await res.json()) as Manifest;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------- metric-ness
 *
 * THE definition of "can this be measured", for the whole app.
 *
 * It lives here, alone, because the page used to answer the question twice: the
 * viewer decided from provenance (and switched its tape measure off) while the
 * format table decided from the marker file (and printed "metres" on the unit
 * chip for the very same mesh, on the same screen). Two answers to one question
 * is the bug; a second copy of this logic anywhere is a regression.
 *
 * Three signals, in order of how much we trust them:
 *
 *   1. `metric === false` — publish_dashboard said so outright.
 *   2. NO_METRIC_SCALE.txt in the file list — tools/rgb_only_trellis.py drops it
 *      beside every normalized mesh, and it ships as a normal deliverable. This
 *      is what keeps the answer right against a manifest written before the flag
 *      existed, which is this app's normal state: it renders whatever was last
 *      published rather than a bundled copy, and none of the 23 currently
 *      published assets carries `metric` at all.
 *   3. The geometry is a generative reconstruction. TRELLIS invents plausible
 *      geometry from images and normalizes it to roughly a unit cube; nothing
 *      measured it. mouse_ewa_regen is the case that needs this rule and only
 *      this rule — one bare asset_trellis.glb, no marker file, no flag — and
 *      without it the app offers a tape measure over a unit cube.
 *
 * Getting this wrong is not cosmetic: read as metres, a normalized kick scooter
 * publishes as "39 x 16 x 38 in" and every tape-measure click returns a
 * fabricated number.
 */

/** The marker tools/rgb_only_trellis.py writes beside a normalized mesh. */
const NO_METRIC_SCALE = /(^|\/)NO_METRIC_SCALE\.txt$/i;

/**
 * Extensions that carry geometry, i.e. the files a scan's scale applies to.
 * .json/.png/.mtl/.txt have no size to be wrong about.
 */
const GEOMETRY_EXT = /\.(ply|glb|gltf|obj|stl|dxf|splat|usd|usda|usdc|usdz)$/i;

/** TRELLIS names its own outputs asset_trellis.*; the name is the signal. */
const GENERATIVE_NAME = /trellis/i;

/**
 * tools/rgb_only_trellis.py puts a whole run in its own folder, and a rerun goes
 * to a sibling (crate_20260730_scooter ships rgb_only_trellis/ and
 * rgb_only_trellis_v2/, and only the first has the marker file). Anything under
 * either folder came out of the generative route whatever it is called.
 */
const GENERATIVE_PATH = /(^|\/)rgb_only_trellis[^/]*(\/|$)/i;

function isGeometry(file: AssetFile): boolean {
  return GEOMETRY_EXT.test(file.name);
}

function isGenerative(file: AssetFile): boolean {
  return GENERATIVE_NAME.test(file.name) || GENERATIVE_PATH.test(`${file.group}/${file.rel}`);
}

/**
 * Is this the exact file publish_dashboard measured the printed dims from?
 *
 * `rel` is not unique inside an asset — BE53A423 ships object_asset/object_mesh.ply
 * in two groups with different geometry — so the url is the key, and the rel+name
 * pair is only a fallback for a manifest that predates the url field.
 */
function isDeclaredMeasure(asset: Asset, file: AssetFile): boolean {
  const m = asset.measure;
  if (!m) return false;
  if (m.url) return m.url === file.url;
  return m.rel === file.rel && m.name === file.name;
}

/**
 * The pipeline explicitly stated this asset has no real-world scale.
 *
 * Separate from hasMetricScale so callers can say WHY: "this capture had no
 * depth frames" is a claim only the marker file and the flag support. An asset
 * that is non-metric merely because every mesh in it is generative gets a
 * different sentence, and must not be told a story about its capture.
 */
export function isDeclaredNonMetric(asset: Asset): boolean {
  if (asset.metric === false) return true;
  return asset.files.some((f) => NO_METRIC_SCALE.test(f.rel) || NO_METRIC_SCALE.test(f.name));
}

/**
 * Per-file: is THIS file's geometry in real units?
 *
 * Drives the per-format unit chips, where the answer genuinely varies inside one
 * asset: a scan can ship a measured fuse next to a generative rebuild of the
 * same object, and printing "metres" on the second is how a customer quotes a
 * size that was never measured.
 *
 * A metric UNIT is not a metric MEASUREMENT, and asset_trellis_mm.stl is the
 * case that proves it. assetpipe/scene/trellis_metric.py writes it in real
 * millimetres, but all it did was scale the generative mesh uniformly until its
 * LONGEST edge matched the RGB-D fuse — the other two axes are still whatever
 * TRELLIS invented. On 76B73843 that file measures 7.27 x 14.00 x 7.31 in where
 * the scan says 13.25 x 10.75 x 14.00: one axis right, two wrong by six inches.
 * So "it is in mm" is not the question this function answers.
 *
 * The declared-measure exception is not a loophole: if publish_dashboard says
 * the printed dims were measured off this exact file, then the pipeline
 * metricized it against something real and its numbers are the numbers on the
 * page. That is the only thing that can rescue a generative mesh — and it never
 * overrides signals 1 and 2, which are about the capture, not the file.
 */
export function fileIsMetric(asset: Asset, file: AssetFile): boolean {
  if (isDeclaredNonMetric(asset)) return false;
  if (isDeclaredMeasure(asset, file)) return true;
  return !isGenerative(file);
}

/**
 * Asset-level: can anything in this asset be measured in real units?
 *
 * True exactly when some geometry file in it is metric, so the asset-level
 * answer can never contradict the per-file chips beside it.
 *
 * An asset with no geometry at all (box_demo is six model.urdf files whose
 * meshes were never published) returns true — deliberately. "No metric scale" is
 * a claim about a capture that produced unmeasurable geometry; a URDF's masses
 * and link offsets are in metres like any other, and stamping "not measurable"
 * on it would be a different falsehood.
 */
export function hasMetricScale(asset: Asset): boolean {
  if (isDeclaredNonMetric(asset)) return false;
  const geometry = asset.files.filter(isGeometry);
  if (geometry.length === 0) return true;
  return geometry.some((f) => fileIsMetric(asset, f));
}

/* ------------------------------------------------------------- the printed size
 *
 * What every SERVER-RENDERED surface is allowed to say about `dims`.
 *
 * This is not a second answer to "can this be measured" — that question has
 * exactly one answer, hasMetricScale() above, and this function asks it rather
 * than re-deciding it. It answers the next question along: given that the asset
 * can be measured, is the string in `dims` a figure about THIS OBJECT, and is
 * it rounded?
 *
 * It exists because the index card and the asset header used to answer
 * differently from the viewer on the same screen. bench_A7C9_fixed printed
 * "size 259.25 in × 262.00 in × 127.50 in ± 1–2 in" in a server-rendered header
 * while the viewer below it printed "Do not use either of these numbers yet";
 * the index card printed the same triple with no qualifier at all. A customer
 * on a phone, with WebGL off, or who reads only the top of the page never saw
 * the contradiction — they saw a room quoted as an object, to the inch.
 */

/** Pull the three inch figures back out of a preformatted dims string. */
export function parseDimsInches(s?: string): [number, number, number] | null {
  if (!s) return null;
  const nums = Array.from(s.matchAll(/([0-9]*\.?[0-9]+)\s*in/gi), (m) => Number(m[1]));
  return nums.length === 3 && nums.every((n) => n > 0)
    ? [nums[0], nums[1], nums[2]]
    : null;
}

/**
 * Inches. Above this on its SHORTEST side, a box is a space, not an object.
 *
 * The server cannot load geometry, so it cannot run the viewer's comparison
 * (model on screen vs printed figure). This is the one check it can run on the
 * string alone, and it is a claim about the capture route rather than a taste
 * threshold: assetpipe measures an object someone carried into a session, and
 * the competing figure in the same dims.json is the on-device ARKit sweep of
 * the whole room. A room's shortest dimension is its ceiling; an object's
 * shortest dimension fits through a door. 8 ft clears a standard 6'8" door and
 * a residential ceiling, and it sits 10 in above the largest side of any
 * honestly published asset here (DC22F084's couch, 85.74 in).
 *
 * On the 23 currently published assets this flags exactly the two that the
 * viewer independently disputes — bench_A7C9_fixed (259.25 × 262.00 × 127.50,
 * whose object is 28.50 × 27.50 × 24.75) and bench_4179_fixed (332.00 × 410.50
 * × 119.25) — and nothing else.
 *
 * A genuine object with a 8 ft minimum side would be flagged wrongly. That
 * costs a "we cannot confirm this" on a page that also links the geometry;
 * being wrong the other way costs a customer a crate built to the size of the
 * room. `dims_disputed` from the publisher is the better signal when it starts
 * shipping — this is the floor under it, not a replacement.
 */
const ROOM_SCALE_MIN_SIDE_IN = 96;

const ROOM_SCALE_REASON =
  "Its shortest side is over 8 ft, which is the size of the space the capture " +
  "was taken in rather than of an object — at that scale this is the " +
  "whole-room sweep, not the item you scanned. Nothing on this page can tell " +
  "you how big this object is until the scan is re-published.";

/**
 * The one thing to print about `dims`, wherever it is printed.
 *
 *   "silent"         say nothing: the asset publishes no size.
 *   "not-measurable" the geometry carries no real-world scale (hasMetricScale).
 *   "disputed"       there is a figure, and it must not be quoted. `why` says
 *                    why, in a sentence that agrees with the viewer's banner.
 *   "measured"       print it, with the tolerance, plus how it was rounded when
 *                    the manifest says.
 */
export type DimsClaim =
  | { state: "silent" }
  | { state: "not-measurable" }
  | { state: "disputed"; dims: string; why: string }
  | { state: "measured"; dims: string; rounding: RoundingNote | null };

export interface RoundingNote {
  /** true: `dims` is the rounded restatement. false: `dims` is the measurement. */
  quantized: boolean;
  /** The grid, in inches. Only ever set when quantized. */
  quantum_in?: number;
  /** The unrounded figure, when the manifest carries one. */
  raw?: string;
}

/**
 * Absent dims_quantized means "unknown", so this returns null and the caller
 * prints nothing — which is what every manifest published so far gets. Typed
 * fields are not trusted to be typed: this manifest is fetched at runtime from
 * blob storage and may have been written by an older publisher.
 */
function roundingNote(asset: Asset): RoundingNote | null {
  if (typeof asset.dims_quantized !== "boolean") return null;
  const q = asset.dims_quantum_in;
  const raw = asset.dims_raw;
  return {
    quantized: asset.dims_quantized,
    quantum_in:
      asset.dims_quantized && typeof q === "number" && Number.isFinite(q) && q > 0
        ? q
        : undefined,
    raw: typeof raw === "string" && raw.trim() ? raw.trim() : undefined,
  };
}

export function dimsClaim(asset: Asset): DimsClaim {
  if (!hasMetricScale(asset)) return { state: "not-measurable" };
  const dims = asset.dims;
  if (typeof dims !== "string" || !dims.trim()) return { state: "silent" };

  // The publisher's own verdict wins: it can see the files this app only has
  // names for. Defensive because nothing emits it yet — a boolean or a stray
  // empty string must not turn into an empty explanation on the page.
  const declared = asset.dims_disputed;
  if (typeof declared === "string" && declared.trim()) {
    return { state: "disputed", dims, why: declared.trim() };
  }

  const sides = parseDimsInches(dims);
  if (sides && Math.min(...sides) > ROOM_SCALE_MIN_SIDE_IN) {
    return { state: "disputed", dims, why: ROOM_SCALE_REASON };
  }

  return { state: "measured", dims, rounding: roundingNote(asset) };
}

/** Files grouped by the app they open in — the download list the customer wants. */
export function filesByTarget(asset: Asset): Map<TargetId, { file: AssetFile; note: string }[]> {
  const out = new Map<TargetId, { file: AssetFile; note: string }[]>();
  for (const file of asset.files) {
    for (const { target, note } of file.targets) {
      if (!out.has(target)) out.set(target, []);
      out.get(target)!.push({ file, note });
    }
  }
  return out;
}

export const TARGET_ORDER: TargetId[] = ["cad", "blender", "mujoco", "isaac", "ros", "web", "generic"];

/**
 * Private-store blobs 403 if linked directly, so every download goes through
 * the authorised proxy. Local-publish paths (`/assets/...`) are served by
 * Next itself and pass through untouched.
 */
export function downloadHref(file: { url: string; name: string }): string {
  if (!/^https?:\/\//.test(file.url)) return file.url;
  return `/api/download?url=${encodeURIComponent(file.url)}&name=${encodeURIComponent(file.name)}`;
}

/** Same proxy, but rendered in the page instead of downloaded. */
/**
 * A human name and a scan date, for DISPLAY ONLY.
 *
 * Every asset published so far has `title === id`, so the customer reads
 * "coffee_table_20260811" as the card heading, again as the page's h1, and again
 * as the alt text a screen reader announces. The id stays the identity — routes,
 * blob paths and the manifest key are untouched; this only changes what is
 * rendered.
 *
 * Falls back to the id verbatim whenever nothing parses, which is the right
 * answer for the hash-style ids (`CrateScan-0D2F9333`): a wrong guess at a
 * human name is worse than the id the customer can match against their upload.
 */
export function displayTitle(asset: { id: string; title: string }): {
  name: string;
  scannedOn: string | null;
} {
  const raw = asset.title || asset.id;
  // <words>_YYYYMMDD, optionally followed by _HH_MM_SS — the pipeline's two
  // naming conventions. Anchored so a bare hash id cannot half-match.
  const m = /^(.+?)_(\d{4})(\d{2})(\d{2})(?:_\d{2}_\d{2}_\d{2})?$/.exec(raw);
  if (!m) return { name: raw, scannedOn: null };
  const words = m[1].replace(/[_-]+/g, " ").trim();
  if (!words) return { name: raw, scannedOn: null };
  return {
    name: words.charAt(0).toUpperCase() + words.slice(1),
    scannedOn: `${m[2]}-${m[3]}-${m[4]}`,
  };
}

export function previewHref(url: string): string {
  if (!/^https?:\/\//.test(url)) return url;
  return `/api/download?inline=1&name=preview.jpg&url=${encodeURIComponent(url)}`;
}

/**
 * Same proxy again, for meshes the viewer fetches itself.
 *
 * `inline=1` is not cosmetic here: the route sends `cache-control: private,
 * no-store` without it, so a 6 MB mesh is re-downloaded on every mount — twice
 * per mount in development, because reactStrictMode double-invokes effects.
 * With it the browser holds the mesh for five minutes.
 */
export function inlineHref(file: { url: string; name: string }): string {
  if (!/^https?:\/\//.test(file.url)) return file.url;
  return `/api/download?inline=1&url=${encodeURIComponent(file.url)}&name=${encodeURIComponent(file.name)}`;
}
