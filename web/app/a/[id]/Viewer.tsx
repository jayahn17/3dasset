"use client";

/**
 * In-browser viewer and tape measure for one asset.
 *
 * three.js plus its loaders is ~170 KB gzipped — several times the rest of this
 * app — so every three import happens inside the effect, never at module scope.
 * That keeps it out of the page's first-load JS and off the server render
 * entirely (an effect never runs there), which is the same thing next/dynamic's
 * `ssr: false` buys, without a second module. The loaders are imported per
 * extension for the same reason: a .ply asset never downloads GLTFLoader.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type * as T from "three";
import type { OrbitControls as OrbitControlsT } from "three/examples/jsm/controls/OrbitControls.js";
import {
  dimsClaim,
  downloadHref,
  fileIsMetric,
  hasMetricScale,
  inlineHref,
  isDeclaredNonMetric,
  parseDimsInches,
  type Asset,
  type AssetFile,
} from "../../../lib/manifest";
import { isAltReconstruction, ALT_RECON_NOTE } from "../../../lib/formats";

const IN = 0.0254; // metres per inch

// Above this the customer opts in with a click rather than watching a blank
// panel fill. Raised from 8 MB once KIRI exports began shipping: 3DModel.obj is
// a 28.5 MB single-mesh OBJ and it is the ONLY viewable geometry that asset has,
// so the old ceiling meant koala_kiri_web opened to a click-to-load prompt while
// every other asset opened to a model. Raw 3DGUT PLYs (~250 MB) are the case
// still worth gating, and publish_dashboard excludes those by default anyway.
const AUTOLOAD_LIMIT = 32 * 1024 * 1024;

const VIEWABLE = /\.(ply|glb|gltf|obj|stl)$/i;

// Files that look like the object but are not it. The room mesh is the biggest
// trap: demo_out/*/mesh_preview/object_mesh_m.ply is the raw ARKit LiDAR sweep
// of the whole room (90 x 68 x 11 in), and its name is one underscore away from
// the CAD meshes we do want.
const NEVER_PICK: RegExp[] = [
  /(^|\/)mesh_preview\//i,
  /(^|\/)collision_/i,
  /scale_check/i,
  /_cloud(_m|_mm)?\.(ply|obj)$/i,
];

type UpAxis = "y" | "z";

interface Pick {
  file: AssetFile;
  source: string;
  /** True when publish_dashboard told us which file the printed dims came from. */
  declared: boolean;
}

interface Stats {
  /**
   * Bounding-box extents along x, y, z of the loaded geometry — deliberately in
   * axis order, not sorted. Which of these is "length" depends on the frame, and
   * the mapping lives in AXIS_READOUT.
   *
   * Metres for metric assets; the file's own unitless numbers otherwise.
   */
  extent: [number, number, number];
  tris: number;
  verts: number;
  grid: string;
  points: boolean;
}

interface ScaleVerdict {
  /** False when the geometry carries no real-world scale at all. */
  metric: boolean;
  /** Why not, and what to do instead. Empty when metric. */
  why: string;
}

/**
 * One finished measurement, as React needs it for the row list. The three.js
 * objects that draw it live in a parallel array inside the effect — only the
 * number and its label reach the DOM.
 *
 * Plural, and each endpoint draggable, because that is how the object actually
 * gets checked: a crate is a width AND a height AND a diagonal, and the old
 * single-shot tape threw the previous answer away on the third tap, so the
 * customer had to write numbers down on paper to compare two edges.
 */
interface Measurement {
  /** 1-based, in creation order. Never renumbered after a delete. */
  n: number;
  dist: number;
}

/* ------------------------------------------------------------------ picking */

/**
 * Rank a file by how well it answers "what should the customer measure".
 *
 * The ordering is not aesthetic. `object_mesh.ply` and `object_mesh.glb` are two
 * independent Poisson solves of the same cloud — the GLB trims at density
 * quantile 0.08 against the PLY's 0.01 and comes out 8-29% smaller per axis. The
 * printed dims are measured from the PLY, so the GLB must never win by default
 * even though it is half the size and parses twenty times faster.
 */
function score(f: AssetFile): number {
  const n = f.name.toLowerCase();
  if (!VIEWABLE.test(n)) return -1;
  if (NEVER_PICK.some((re) => re.test(f.rel))) return -1;

  // TRELLIS output is a generative reconstruction: plausible geometry, but not
  // the geometry any dims.json was measured from. It only wins when the asset
  // ships no fused and no CAD mesh at all. This has to be tested before the
  // unit-suffix rules below, or asset_trellis_mm.stl reads as a CAD export and
  // outranks the fuse the dims actually came from.
  if (/trellis/.test(n)) return /\.(glb|gltf)$/.test(n) ? 12 : 10;

  // Metre-scale CAD mesh from the splat route. Whenever a cad_report.json
  // exists it is this mesh the printed dims were measured from.
  if (/_m\.ply$/.test(n)) return 100;
  if (/_m\.obj$/.test(n)) return 92;
  // The RGB-D fuse. dims.json records this exact path as its source.
  if (n === "object_mesh.ply") return 85;
  // Millimetre CAD exports: the same geometry as _m.*, in the other unit.
  if (/_mm\.(ply|stl|obj)$/.test(n)) return 80;
  // object_mesh.glb is a SECOND Poisson solve of the same cloud, trimmed at
  // density quantile 0.08 against the .ply's 0.01, so it comes out 8-29%
  // smaller per axis than the dims printed on this page. Tested here, ahead of
  // the generic .glb rule, so it can never win by falling through to it.
  if (n === "object_mesh.glb") return 20;
  if (/\.ply$/.test(n)) return 70;
  if (/\.(glb|gltf)$/.test(n)) return 50;
  if (/\.stl$/.test(n)) return 40;
  if (/\.obj$/.test(n)) return 30;
  return -1;
}

/**
 * What this file is, in the customer's terms.
 *
 * Takes the asset because one of the sentences compares this file against two
 * other things — a sibling .ply and the size printed at the top of the page —
 * and neither is guaranteed to exist. `runs` ships four object_mesh.glb, no
 * .ply and no dims, and was told it was "trimmed harder than the .ply the dims
 * came from": two references to nothing, in one clause.
 */
/**
 * Short name for the reconstruction a file belongs to — for the switcher.
 *
 * The switcher listed bare filenames, so choosing between reconstructions meant
 * knowing that `kiri_visual.glb` is KIRI Engine and `asset_trellis.glb` is the
 * generative one. A customer told us the KIRI meshes were missing when in fact
 * every one of them was in this menu, spelled as a filename.
 */
function reconLabel(f: AssetFile): string {
  const n = f.name.toLowerCase();
  // 3DModel.obj is KIRI Engine's own export name, so it is matched by name and
  // not by a "kiri" substring that isn't there.
  if (/kiri/.test(n) || /kiri/.test(f.rel.toLowerCase()) || n === "3dmodel.obj")
    return "KIRI Engine";
  if (/meshroom/.test(n)) return "Photo mesh";
  if (/trellis/.test(n)) return "AI mesh";
  if (/_m\.(ply|obj)$|_mm\.(stl|obj|ply)$/.test(n)) return "CAD mesh";
  if (/^object_mesh\./.test(n)) return "Scan";
  return "Other";
}

function describe(f: AssetFile, asset: Asset): string {
  const n = f.name.toLowerCase();
  // KIRI and Meshroom fell through to "published geometry", which named neither
  // what produced them nor why their numbers differ from the printed size.
  if (/kiri/.test(n) || /kiri/.test(f.rel.toLowerCase()) || n === "3dmodel.obj")
    return "KIRI Engine photogrammetry, a second reconstruction of this capture";
  if (/meshroom/.test(n))
    return "AliceVision photogrammetry, a second reconstruction of this capture";
  if (/trellis/.test(n)) return "TRELLIS generative reconstruction, not the measured fuse";
  if (/_m\.ply$|_m\.obj$|_mm\.(stl|obj|ply)$/.test(n))
    return "CAD mesh rebuilt from the Gaussian splat";
  if (n === "object_mesh.ply") return "RGB-D depth fuse, Poisson surface";
  if (n === "object_mesh.glb") {
    // Both halves of the parenthetical have to be true on this page: a .ply to
    // be trimmed harder than, and a printed size for it to have come from. The
    // printed-size test is the same one the readout below uses, so the two
    // cannot disagree.
    const hasPly = asset.files.some((g) => /\.ply$/i.test(g.name));
    const printsASize = hasMetricScale(asset) && !!asset.dims;
    return hasPly && printsASize
      ? "RGB-D depth fuse, GLB export (trimmed harder than the .ply the dims came from)"
      : "RGB-D depth fuse, GLB export";
  }
  return "published geometry";
}

function pickFile(asset: Asset): Pick | null {
  const m = asset.measure;
  if (m?.url) {
    // rel is not unique inside an asset — object_asset/object_mesh.ply exists in
    // two groups for BE53A423 with different geometry — so match on url first.
    const exact =
      asset.files.find((f) => f.url === m.url) ||
      asset.files.find((f) => f.rel === m.rel && f.name === m.name);
    const file: AssetFile = exact ?? {
      name: m.name,
      rel: m.rel,
      url: m.url,
      size: `${(m.bytes / 1048576).toFixed(1)} MB`,
      bytes: m.bytes,
      group: "",
      targets: [],
    };
    if (VIEWABLE.test(file.name)) return { file, source: m.source, declared: true };
  }

  let best: AssetFile | null = null;
  let bestScore = 0;
  for (const f of asset.files) {
    const s = score(f);
    if (s <= 0) continue;
    if (
      s > bestScore ||
      // Deterministic tiebreak. Both BE53A423 groups ship the same rel; the
      // unsuffixed group is the run the printed dims came from, and it is
      // always the shorter name.
      (s === bestScore &&
        best !== null &&
        (f.group.length < best.group.length ||
          (f.group.length === best.group.length && f.rel < best.rel)))
    ) {
      best = f;
      bestScore = s;
    }
  }
  return best ? { file: best, source: describe(best, asset), declared: false } : null;
}

/* ------------------------------------------------------------------- frames */

/**
 * Which axis points up IN THIS FILE — the frame its numbers are written in.
 *
 * There are two conventions in the pipeline and no field that states which one a
 * given file uses. The RGB-D object meshes are in the ARKit world frame
 * (x = width, y = height, z = length), while anything from splat_to_cad is Z-up
 * with the floor at z = 0. Guess from the filename and fall back to the asset's
 * frame string.
 *
 * This is a property of the bytes on disk, so nothing the customer clicks may
 * change it. The camera-up control is a separate value (see viewUp): it decides
 * which way the scene is drawn, never which extent is called length.
 */
function guessUp(file: AssetFile, frame?: string): UpAxis {
  const n = file.name.toLowerCase();
  const rel = file.rel.toLowerCase();
  if (rel.includes("object_asset/") || n.startsWith("object_mesh.") || n.startsWith("object."))
    return "y";
  // TRELLIS emits glTF-convention geometry, and its millimetre STL inherits that
  // frame — so the _mm rule below would flip it onto its side.
  if (/trellis/.test(n)) return "y";
  if (/_m\.(ply|obj)$|_mm\.(ply|obj|stl)$|\.stl$/.test(n)) return "z";
  if (frame) {
    if (/z\s*=\s*height|floor at z\s*=\s*0/i.test(frame)) return "z";
    if (/y\s*=\s*height|floor at y\s*=\s*0/i.test(frame)) return "y";
  }
  if (/\.(glb|gltf)$/.test(n)) return "y"; // glTF's own convention
  return "z";
}

/**
 * How the pipeline names the three bounding-box figures, per FILE FRAME.
 *
 * Keyed on the frame the file is authored in (guessUp), never on the camera-up
 * control. Keying it on the camera used to mean one click relabelled physical
 * axes: 7C3DD25E (extents x, y, z = 35.82, 17.81, 22.33 in) read
 * "length 22 x width 36 x height 18" — matching its printed size to within
 * 0.08 in — and then "length 36 x depth 18 x height 22" after a click, which
 * misses the printed size by 17.94 in and fired the mismatch banner. The bytes
 * had not changed; only the camera had.
 *
 * These are NOT sorted by size, and that is the whole point. This viewer used to
 * call the larger horizontal extent "length" and the smaller one "width", which
 * transposes 11 of the 14 RGB-D assets against the figure printed at the top of
 * the same page: 7C3DD25E read "36 in x 22 in" on screen over "22.25 in x 35.75 in"
 * printed. A crate is ordered from the printed triple (crate_inches_0_25), so a
 * customer reading "length 36 in" off the viewer sizes the wrong axis.
 *
 *   y = up: the ARKit world frame of the RGB-D fuse.
 *           assetpipe/scene/measure.py builds its summary as
 *           "ARKit: x=width, y=height, z=length", printed L x W x H -> [z, x, y].
 *   z = up: the levelled frame of the splat -> CAD route.
 *           tools/splat_to_cad.py records "X=length, Y=depth, Z=height; floor at
 *           z=0" and prints summary_in in exactly that order -> [x, y, z].
 *
 * guessUp() is what decides which of the two frames a file is in, and reviewers
 * confirmed its verdicts; keep the two tables keyed off it rather than adding a
 * third guess here.
 */
interface AxisReadout {
  /** Index into Stats.extent / the measurement delta. */
  axis: 0 | 1 | 2;
  /** The pipeline's own name for this figure. */
  label: string;
  /** The axis letter, so the label is never ambiguous on screen. */
  letter: "X" | "Y" | "Z";
}

const AXIS_READOUT: Record<UpAxis, [AxisReadout, AxisReadout, AxisReadout]> = {
  y: [
    { axis: 2, label: "length", letter: "Z" },
    { axis: 0, label: "width", letter: "X" },
    { axis: 1, label: "height", letter: "Y" },
  ],
  z: [
    { axis: 0, label: "length", letter: "X" },
    { axis: 1, label: "depth", letter: "Y" },
    { axis: 2, label: "height", letter: "Z" },
  ],
};

/**
 * Does the geometry we are about to draw mean anything in inches?
 *
 * Answered from provenance, never from the numbers inside the file. The old code
 * asked "is the largest extent under 25?" and called anything smaller metres,
 * which is exactly backwards for a normalized mesh: a unit-cube TRELLIS scooter
 * is 0.998 x 0.973 x 0.408, sails under that threshold, and was published as
 * "39 in x 16 in x 38 in" with a working tape measure over it. No size test can
 * tell "1 metre" from "1 unit" — only the pipeline that wrote the file can.
 */
function scaleVerdict(asset: Asset, pick: Pick | null): ScaleVerdict {
  // Both questions are answered by lib/manifest.ts and nowhere else. This
  // function only chooses the sentence: the format table below reads the same
  // two functions, so the page cannot say "no metric scale" here and "metres"
  // there for one mesh, which is exactly what it used to do.
  if (!hasMetricScale(asset)) {
    return {
      metric: false,
      why: isDeclaredNonMetric(asset)
        ? // The marker file / the flag: the pipeline recorded that the capture
          // itself had no depth, so we can say so.
          "This capture was RGB only — no depth frames — so the geometry was reconstructed " +
          "generatively and comes out normalized to roughly a unit cube."
        : // No marker, no flag: every mesh published is simply a generative
          // rebuild (mouse_ewa_regen ships one bare asset_trellis.glb). We know
          // what the geometry is, not what the capture was, so claim only that.
          "Every mesh published for this asset is a TRELLIS generative reconstruction — " +
          "geometry invented from images and normalized to roughly a unit cube, not measured.",
    };
  }
  // The asset can be measured somewhere, but not necessarily in the file on
  // screen: score() lets a generative mesh win only when nothing better is
  // drawable, and a normalized mesh is a normalized mesh whatever it sits beside.
  if (pick && !fileIsMetric(asset, pick.file)) {
    return {
      metric: false,
      why:
        `The geometry drawn here (${pick.file.name}) is a TRELLIS generative reconstruction, ` +
        "which is normalized to roughly a unit cube rather than measured.",
    };
  }
  return { metric: true, why: "" };
}

/* ---------------------------------------------------------------- formatting */

/**
 * ONE rounding step, in inches. Every figure the viewer prints comes from it.
 *
 * Real accuracy here is +/-1-2 in with ~19 mm surface RMS. Printing a tenth of
 * an inch would read as a tenth of an inch of accuracy, which this capture
 * cannot support, so a whole inch is the finest quantum the data can defend.
 */
function roundedInches(m: number): number {
  return Math.round(m / IN);
}

/**
 * Below half an inch the whole-inch quantum has nothing left to say, and "0 in"
 * for a real 8 mm pick is a worse lie than admitting the floor.
 */
const SUB_INCH_IN = "< 1 in";
const SUB_INCH_MM = "< 25 mm";

function inchStr(m: number): string {
  const v = roundedInches(m);
  return v < 1 ? SUB_INCH_IN : `${v} in`;
}

/**
 * The same rounded inch figure, converted — NOT a second, independent rounding.
 *
 * This used to round the metres to 10 mm on its own, so one distance printed two
 * lengths: a 25.4 mm pick read "1 in · 30 mm" (18% apart) and a 12.7 mm pick
 * read "1 in · 10 mm" (a factor of 2.5). Each quantum was defensible against
 * +/-1-2 in on its own; side by side on one distance they read as an arithmetic
 * error, which costs more trust than the precision ever bought.
 *
 * The 5 mm display step is a fifth of the inch quantum: coarse enough not to
 * imply millimetre accuracy, fine enough that it always converts back to the
 * same whole inch (the worst case is 2.5 mm, i.e. 0.1 in, off an inch boundary).
 */
function mmStr(m: number): string {
  const v = roundedInches(m);
  if (v < 1) return SUB_INCH_MM;
  return `${Math.round((v * 25.4) / 5) * 5} mm`;
}
/**
 * A figure from geometry that has no real-world scale.
 *
 * Never carries a unit suffix, and callers must not add one: the whole failure
 * this replaces was a unitless number being printed with "in" after it.
 */
function unitStr(v: number): string {
  return v >= 10 ? v.toFixed(1) : v.toFixed(2);
}
function mb(bytes: number): string {
  return bytes >= 1048576
    ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/* ------------------------------------------------------------------ transport */

async function fetchMesh(
  url: string,
  expected: number,
  signal: AbortSignal,
  onProgress: (loaded: number, total: number) => void,
): Promise<Uint8Array> {
  const res = await fetch(url, { signal });

  // Production returns a 307 to /login when the session has lapsed, and fetch
  // follows redirects by default — so a stale cookie yields res.ok === true and
  // an ArrayBuffer full of the login page. Without this check the loader throws
  // a parse error and the customer is told the mesh is corrupt.
  const ctype = (res.headers.get("content-type") || "").toLowerCase();
  if (res.redirected || ctype.startsWith("text/html")) {
    throw new Error("Your session expired. Reload the page to sign in again.");
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Download failed (HTTP ${res.status}). ${detail.slice(0, 160)}`.trim());
  }

  // The proxy forwards content-type, content-disposition and cache-control and
  // nothing else, so there is usually no content-length; the manifest's own
  // byte count is the only total available.
  const declared = Number(res.headers.get("content-length"));
  const total = Number.isFinite(declared) && declared > 0 ? declared : expected;

  if (!res.body) {
    const buf = new Uint8Array(await res.arrayBuffer());
    onProgress(buf.byteLength, total || buf.byteLength);
    return buf;
  }

  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) {
      chunks.push(value);
      loaded += value.byteLength;
      onProgress(loaded, total || loaded);
    }
  }
  const out = new Uint8Array(loaded);
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.byteLength;
  }
  return out;
}

/* -------------------------------------------------------------------- helpers */

/**
 * Open3D writes `property double x/y/z`, which PLYLoader faithfully turns into a
 * Float64BufferAttribute. WebGL has no float64 attribute type and three throws
 * outright ("Unsupported buffer data format") on the first render, so every
 * float64 attribute has to be rebuilt as float32. At these scales float32
 * resolves to ~1e-7 m, six orders of magnitude below the 19 mm surface RMS, so
 * the downcast costs nothing measurable.
 */
function downcast(THREE: typeof import("three"), geom: T.BufferGeometry): void {
  for (const key of ["position", "normal", "uv"]) {
    const a = geom.getAttribute(key);
    if (a && a.array instanceof Float64Array) {
      geom.setAttribute(
        key,
        new THREE.BufferAttribute(Float32Array.from(a.array), a.itemSize, a.normalized),
      );
    }
  }
}

function disposeTree(root: T.Object3D): void {
  root.traverse((o) => {
    const any = o as unknown as {
      geometry?: T.BufferGeometry;
      material?: T.Material | T.Material[];
    };
    any.geometry?.dispose();
    const mats = any.material
      ? Array.isArray(any.material)
        ? any.material
        : [any.material]
      : [];
    for (const m of mats) {
      for (const v of Object.values(m as unknown as Record<string, unknown>)) {
        const tex = v as { isTexture?: boolean; dispose?: () => void };
        if (tex && tex.isTexture && typeof tex.dispose === "function") tex.dispose();
      }
      m.dispose();
    }
  });
}

const GRID_LADDER: [number, string][] = [
  [1 * IN, "1 in"],
  [2 * IN, "2 in"],
  [3 * IN, "3 in"],
  [6 * IN, "6 in"],
  [12 * IN, "1 ft"],
  [24 * IN, "2 ft"],
  [60 * IN, "5 ft"],
  [120 * IN, "10 ft"],
];

// The same ladder for geometry with no real-world scale. A grid is still worth
// drawing — it is what makes the model read as sitting on a floor — but its
// squares are model units, so the legend must never say "1 in".
const UNITLESS_LADDER: [number, string][] = [
  [0.01, "0.01 units"],
  [0.02, "0.02 units"],
  [0.05, "0.05 units"],
  [0.1, "0.1 units"],
  [0.2, "0.2 units"],
  [0.5, "0.5 units"],
  [1, "1 unit"],
  [2, "2 units"],
  [5, "5 units"],
  [10, "10 units"],
];

/* ==================================================================== component */

export default function Viewer({ asset }: { asset: Asset }) {
  const rankedPick = useMemo(() => pickFile(asset), [asset]);
  // Every 3D file the asset ships, not only the ranked winner. The ranking
  // still chooses the DEFAULT (it encodes which file the printed dims were
  // measured on); this list is the "view any reconstruction" menu — an asset
  // now carries meshroom_visual / asset_trellis / kiri_visual / object_mesh
  // side by side and comparing them is the point.
  const viewables = useMemo(
    () =>
      asset.files.filter(
        (f) => VIEWABLE.test(f.name) && !NEVER_PICK.some((rx) => rx.test(f.rel))
      ),
    [asset]
  );
  const [chosenRel, setChosenRel] = useState<string | null>(null);
  const pick = useMemo(() => {
    if (chosenRel) {
      const f = viewables.find((x) => x.rel === chosenRel);
      if (f) return { file: f, source: describe(f, asset), declared: false };
    }
    return rankedPick;
  }, [chosenRel, viewables, rankedPick, asset]);
  const scaleInfo = useMemo(() => scaleVerdict(asset, pick), [asset, pick]);
  const metric = scaleInfo.metric;
  // What the header above and the card that linked here already say about the
  // printed figure. Read, not recomputed: this panel disputing a figure the
  // header vouches for (or vice versa) is the bug the shared call exists to
  // stop, and the server cannot load geometry so it can only ever know what
  // dimsClaim knows.
  const claim = useMemo(() => dimsClaim(asset), [asset]);
  const declaredDisputed = claim.state === "disputed";
  // No scale means no printed dims worth comparing against, even if a stale
  // manifest carried some: they were not measured off this geometry.
  const printed = useMemo(
    () => (metric ? parseDimsInches(asset.dims) : null),
    [asset.dims, metric],
  );
  const splat = useMemo(
    () => asset.files.find((f) => /\.splat$/i.test(f.name) && f.bytes > 5e6),
    [asset.files],
  );

  // Two different things, kept apart on purpose.
  //
  // frameUp is the frame the FILE is authored in. It decides which extent is
  // called length, width, depth or height, and it is the frame the printed dims
  // were measured in, so it is also what the mismatch comparison is done in.
  // Nothing in the UI may change it.
  //
  // viewUp is only where the camera puts "up". The button exists because a wrong
  // frame guess lays the model on its side — obvious on screen, impossible to fix
  // from here — but re-orienting the camera reconstructs nothing, so it must not
  // move a single number.
  const frameUp = useMemo<UpAxis>(
    () => (pick ? guessUp(pick.file, asset.frame) : "z"),
    [pick, asset.frame],
  );
  const [viewUpOverride, setViewUpOverride] = useState<UpAxis | null>(null);
  const viewUp = viewUpOverride ?? frameUp;

  const [webgl, setWebgl] = useState<boolean | null>(null);
  const [armed, setArmed] = useState(() => !!pick && pick.file.bytes <= AUTOLOAD_LIMIT);
  // Switching files re-evaluates autoload: a small mesh loads immediately, a
  // big one goes back to click-to-load rather than silently pulling 30 MB.
  useEffect(() => {
    setArmed(!!pick && pick.file.bytes <= AUTOLOAD_LIMIT);
  }, [pick]);
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [progress, setProgress] = useState<{ loaded: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [measures, setMeasures] = useState<Measurement[]>([]);
  const [pending, setPending] = useState(0);
  const [measureMode, setMeasureMode] = useState(true);
  // The tape measure reports distances, and a distance with no scale is a
  // fabricated number — so it is off, not merely unlabelled, on a normalized mesh.
  const measuring = measureMode && metric;

  const hostRef = useRef<HTMLDivElement | null>(null);
  // Container the per-measurement labels are appended into. One absolutely
  // positioned div per measurement, parented to the viewer box so a page scroll
  // moves them with the canvas.
  const labelRef = useRef<HTMLDivElement | null>(null);
  const apiRef = useRef<{
    clear: () => void;
    reset: () => void;
    remove: (n: number) => void;
  } | null>(null);
  const modeRef = useRef(true);
  // Cached bytes, so flipping the camera up axis re-frames the scene without
  // paying for the download again.
  const cacheRef = useRef<{ url: string; bytes: Uint8Array } | null>(null);

  useEffect(() => {
    modeRef.current = measuring;
  }, [measuring]);

  useEffect(() => {
    try {
      const c = document.createElement("canvas");
      setWebgl(!!(c.getContext("webgl2") || c.getContext("webgl")));
    } catch {
      setWebgl(false);
    }
  }, []);

  const href = pick ? inlineHref(pick.file) : "";

  useEffect(() => {
    if (!pick || !armed || webgl !== true) return;
    const host = hostRef.current;
    if (!host) return;

    const ac = new AbortController();
    let cancelled = false;
    let raf = 0;
    const teardown: (() => void)[] = [];

    setPhase("loading");
    setError(null);
    setMeasures([]);
    setPending(0);

    (async () => {
      let bytes: Uint8Array;
      try {
        const cached = cacheRef.current;
        if (cached && cached.url === href) {
          bytes = cached.bytes;
          setProgress({ loaded: bytes.byteLength, total: bytes.byteLength });
        } else {
          bytes = await fetchMesh(href, pick.file.bytes, ac.signal, (loaded, total) =>
            setProgress({ loaded, total }),
          );
          cacheRef.current = { url: href, bytes };
        }
      } catch (e) {
        if (cancelled || (e as Error).name === "AbortError") return;
        setError((e as Error).message || "Could not download the mesh.");
        setPhase("error");
        return;
      }
      if (cancelled) return;

      const THREE = await import("three");
      const { OrbitControls } = await import("three/examples/jsm/controls/OrbitControls.js");
      if (cancelled) return;

      // Parsers mutate/transfer their input in some paths, so hand each parse a
      // private copy and keep the cached bytes pristine for the next rebuild.
      const ab = bytes.slice().buffer as ArrayBuffer;
      const ext = (pick.file.name.split(".").pop() || "").toLowerCase();

      let model: T.Object3D;
      let isPoints = false;
      try {
        if (ext === "ply") {
          const { PLYLoader } = await import("three/examples/jsm/loaders/PLYLoader.js");
          const geom = new PLYLoader().parse(ab);
          downcast(THREE, geom);
          // PLYLoader only sets an index when the file declares faces, so a null
          // index here means the file is a bare point cloud.
          isPoints = geom.index === null;
          geom.computeBoundingBox();
          const scale = unitScale(pick.file.name, geom.boundingBox!, metric);
          if (scale !== 1) geom.scale(scale, scale, scale);
          geom.computeBoundingBox();
          const hasColor = !!geom.getAttribute("color");
          if (isPoints) {
            const size = geom.boundingBox!.getSize(new THREE.Vector3());
            model = new THREE.Points(
              geom,
              new THREE.PointsMaterial({
                size: Math.max(size.length() * 0.0015, 0.0005),
                sizeAttenuation: true,
                vertexColors: hasColor,
                color: hasColor ? 0xffffff : 0x9fb0c0,
              }),
            );
          } else {
            if (!geom.getAttribute("normal")) geom.computeVertexNormals();
            model = new THREE.Mesh(geom, surfaceMaterial(THREE, hasColor));
          }
        } else if (ext === "stl") {
          const { STLLoader } = await import("three/examples/jsm/loaders/STLLoader.js");
          const geom = new STLLoader().parse(ab);
          downcast(THREE, geom);
          geom.computeBoundingBox();
          const scale = unitScale(pick.file.name, geom.boundingBox!, metric);
          if (scale !== 1) geom.scale(scale, scale, scale);
          if (!geom.getAttribute("normal")) geom.computeVertexNormals();
          model = new THREE.Mesh(geom, surfaceMaterial(THREE, !!geom.getAttribute("color")));
        } else if (ext === "obj") {
          const { OBJLoader } = await import("three/examples/jsm/loaders/OBJLoader.js");
          const group = new OBJLoader().parse(new TextDecoder().decode(bytes));
          // Texture from the sibling .mtl when the pair is published; null falls
          // back to the plain surface, so an OBJ with no material is unchanged.
          const map = await objTextureMap(THREE, pick.file, asset.files, ac.signal);
          if (cancelled) {
            map?.dispose();
            return;
          }
          group.traverse((o) => {
            const mesh = o as T.Mesh;
            if (!mesh.isMesh) return;
            if (!mesh.geometry.getAttribute("normal")) mesh.geometry.computeVertexNormals();
            // Needs UVs to sample the map at all — an OBJ with a .mtl but no
            // `vt` lines would otherwise render as one flat texel of colour.
            mesh.material =
              map && mesh.geometry.getAttribute("uv")
                ? texturedMaterial(THREE, map)
                : surfaceMaterial(THREE, !!mesh.geometry.getAttribute("color"));
          });
          const box = new THREE.Box3().setFromObject(group);
          const scale = unitScale(pick.file.name, box, metric);
          if (scale !== 1) group.scale.setScalar(scale);
          model = group;
        } else {
          const { GLTFLoader } = await import("three/examples/jsm/loaders/GLTFLoader.js");
          const gltf = await new GLTFLoader().parseAsync(ab, "");
          gltf.scene.traverse((o) => {
            const mesh = o as T.Mesh;
            if (!mesh.isMesh) return;
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
            for (const raw of mats) {
              const m = raw as T.MeshStandardMaterial;
              // These GLBs carry "materials": null, so GLTFLoader falls back to
              // its default material — which hardcodes metalness 1. A fully
              // metallic surface with no environment map reflects nothing and
              // renders solid black no matter how many lights are added.
              if (m.isMeshStandardMaterial) {
                m.metalness = 0;
                m.roughness = 1;
              }
              m.side = THREE.DoubleSide;
            }
          });
          const box = new THREE.Box3().setFromObject(gltf.scene);
          const scale = unitScale(pick.file.name, box, metric);
          if (scale !== 1) gltf.scene.scale.setScalar(scale);
          model = gltf.scene;
        }
      } catch (e) {
        if (cancelled) return;
        setError(`Could not read ${pick.file.name}: ${(e as Error).message}`);
        setPhase("error");
        return;
      }
      if (cancelled) {
        disposeTree(model);
        return;
      }

      /* ---------------------------------------------------------- renderer */

      let renderer: T.WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
      } catch (e) {
        disposeTree(model);
        setError(`WebGL could not start: ${(e as Error).message}`);
        setPhase("error");
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setClearColor(0x05070a, 1);
      const canvas = renderer.domElement;
      canvas.style.display = "block";
      canvas.style.width = "100%";
      canvas.style.height = "100%";
      host.appendChild(canvas);

      const onLost = (ev: Event) => {
        ev.preventDefault();
        setError("The browser dropped the 3D context. Reload the page to try again.");
        setPhase("error");
      };
      canvas.addEventListener("webglcontextlost", onLost, false);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(45, 1, 0.005, 500);
      // OrbitControls snapshots object.up in its constructor, so this has to
      // happen before the controls exist or the orbit stays locked to +Y.
      camera.up.set(0, viewUp === "z" ? 0 : 1, viewUp === "z" ? 1 : 0);

      const root = new THREE.Group();
      root.add(model);
      scene.add(root);

      const box = new THREE.Box3().setFromObject(root);
      const size = box.getSize(new THREE.Vector3());
      const centre = box.getCenter(new THREE.Vector3());
      const upIdx = viewUp === "z" ? 2 : 1;
      const axes: ("x" | "y" | "z")[] = ["x", "y", "z"];
      const height = size[axes[upIdx]];
      // Framing only. These are max/min of the two horizontal extents, which is
      // the right thing for sizing a camera and a grid and the WRONG thing for a
      // readout: the pipeline's length and width are fixed axes, not sorted
      // figures. AXIS_READOUT does the readout; nothing here feeds it.
      const horiz = axes.filter((_, i) => i !== upIdx).map((a) => size[a]);
      const horizMax = Math.max(horiz[0], horiz[1]);
      const horizMin = Math.min(horiz[0], horiz[1]);

      // Sit the model on the grid plane and centre it horizontally, so the
      // reference grid reads as a floor rather than a slice through the object.
      const offset = centre.clone().negate();
      offset[axes[upIdx]] = -box.min[axes[upIdx]];
      root.position.copy(offset);

      const radius = Math.max(size.length() / 2, 0.02);

      /* ------------------------------------------------------------- grid */

      const span = Math.max(horizMax, horizMin) * 1.8;
      const ladder = metric ? GRID_LADDER : UNITLESS_LADDER;
      let spacing = ladder[ladder.length - 1][0];
      let gridLabel = ladder[ladder.length - 1][1];
      for (const [s, l] of ladder) {
        if (span / s <= 24) {
          spacing = s;
          gridLabel = l;
          break;
        }
      }
      const divisions = Math.max(2, Math.round(span / spacing));
      const grid = new THREE.GridHelper(divisions * spacing, divisions, 0x2b3540, 0x1a2029);
      // GridHelper is built in the XZ plane, i.e. for a Y-up world.
      if (viewUp === "z") grid.rotation.x = Math.PI / 2;
      scene.add(grid);

      /* ----------------------------------------------------------- lights */

      scene.add(new THREE.HemisphereLight(0xdce6f2, 0x0a0e14, 1.5));
      const key = new THREE.DirectionalLight(0xffffff, 1.7);
      key.position.set(1.2, 1.6, 1.0);
      const fill = new THREE.DirectionalLight(0x9fb6d0, 0.6);
      fill.position.set(-1.4, -0.4, -1.0);
      // Parented to the camera so the model is lit from wherever the customer is
      // looking; a world-fixed key light leaves the far side unreadable.
      camera.add(key, fill);
      scene.add(camera);

      /* ------------------------------------------------------------ frame */

      const target = new THREE.Vector3();
      target[axes[upIdx]] = height / 2;
      const dir =
        viewUp === "z"
          ? new THREE.Vector3(0.85, -1.0, 0.55).normalize()
          : new THREE.Vector3(0.85, 0.55, 1.0).normalize();
      const dist = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.35;
      const home = target.clone().addScaledVector(dir, dist);
      camera.position.copy(home);
      camera.lookAt(target);

      const controls: OrbitControlsT = new OrbitControls(camera, canvas);
      controls.target.copy(target);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.minDistance = radius * 0.15;
      controls.maxDistance = dist * 8;
      controls.listenToKeyEvents(host);
      controls.update();

      /* ------------------------------------------------------- measurement */

      // Red, and the same red as the splat viewer's tape — the two are the same
      // gesture on two different renderers, so they must not look like two
      // different tools.
      const RED = 0xff3b30;
      const markerGeom = new THREE.SphereGeometry(1, 16, 12);
      const markerMat = new THREE.MeshBasicMaterial({
        color: RED,
        // depthTest off: a picked point on the far side of the object still has
        // to be visible, otherwise the customer cannot tell what they clicked.
        depthTest: false,
        transparent: true,
        opacity: 0.95,
      });
      const barGeom = new THREE.CylinderGeometry(1, 1, 1, 12, 1, true);
      const barMat = new THREE.MeshBasicMaterial({
        color: RED,
        depthTest: false,
        transparent: true,
        opacity: 0.95,
      });
      const gizmo = new THREE.Group();
      gizmo.renderOrder = 999;
      scene.add(gizmo);

      // Picking tolerance for point clouds, in world units. Unrelated to how big
      // the handles LOOK — that is decided per frame in sizeGizmo() below.
      const markerR = Math.max(radius * 0.014, 0.0012);
      const ray = new THREE.Raycaster();
      ray.params.Points.threshold = markerR * 2;
      const ndc = new THREE.Vector2();
      const mid = new THREE.Vector3();
      const proj = new THREE.Vector3();
      const YUP = new THREE.Vector3(0, 1, 0);

      /* Handles are sized in SCREEN PIXELS, re-scaled every frame.
       *
       * They were a world-space sphere at 1.4% of the model radius, which is the
       * wrong primitive for a grab handle: it SWELLS as you zoom in — exactly
       * when you are trying to see the surface underneath it — and vanishes when
       * you zoom out. Shrinking the radius only moves the problem to a different
       * zoom level. Constant apparent size fixes it at every distance.
       *
       * 3.5 px radius, matching the splat viewer's handles: the two tapes are one
       * gesture on two renderers and must not look like two different tools. */
      const DOT_PX = 3.5;
      const BAR_PX = 1.1;

      /** World size that subtends one screen pixel at distance `d`. */
      const pxToWorld = (d: number) =>
        (2 * Math.tan((camera.fov * Math.PI) / 180 / 2) * d) / Math.max(vh, 1);

      /** A live measurement: two draggable ends, the bar between them, and the
       *  floating label. `n` ties it to its React row. */
      interface Live {
        n: number;
        a: T.Mesh;
        b: T.Mesh;
        bar: T.Mesh;
        /** Distance between the ends, in world units — the bar's LENGTH scale.
         *  Held here because the per-frame resize owns the whole scale vector
         *  and must not clobber the length it is not responsible for. */
        len: number;
        label: HTMLDivElement;
      }
      const live: Live[] = [];
      let seq = 0;
      let first: T.Vector3 | null = null;
      let firstDot: T.Mesh | null = null;

      const mkDot = (p: T.Vector3) => {
        const s = new THREE.Mesh(markerGeom, markerMat);
        s.position.copy(p);
        s.renderOrder = 999;
        gizmo.add(s);
        // Sized on the next frame; set something sane now so it cannot flash at
        // the model's full radius for one frame.
        s.scale.setScalar(DOT_PX * pxToWorld(camera.position.distanceTo(p)));
        return s;
      };

      /** Give every handle and bar its constant apparent size for THIS frame.
       *  Cheap: a distance and a multiply per object, no allocation. */
      const sizeGizmo = () => {
        for (const m of live) {
          const ra = DOT_PX * pxToWorld(camera.position.distanceTo(m.a.position));
          const rb = DOT_PX * pxToWorld(camera.position.distanceTo(m.b.position));
          m.a.scale.setScalar(ra);
          m.b.scale.setScalar(rb);
          const rbar = BAR_PX * pxToWorld(camera.position.distanceTo(m.bar.position));
          m.bar.scale.set(rbar, m.len, rbar);
        }
        // The in-progress first dot is not in `live` yet, and leaving it out is
        // how you get one fat dot sitting among correctly-sized ones.
        if (firstDot) {
          firstDot.scale.setScalar(
            DOT_PX * pxToWorld(camera.position.distanceTo(firstDot.position)),
          );
        }
      };

      /** Re-fit the bar and re-word the label. Called on create and on every
       *  drag frame, so it must not allocate. */
      const refresh = (m: Live) => {
        const d = m.a.position.distanceTo(m.b.position);
        mid.copy(m.b.position).sub(m.a.position);
        m.len = d;
        m.bar.position.copy(m.a.position).add(m.b.position).multiplyScalar(0.5);
        if (d > 1e-9) m.bar.quaternion.setFromUnitVectors(YUP, mid.normalize());
        m.label.textContent = inchStr(d);
        setMeasures((prev) =>
          prev.map((r) => (r.n === m.n ? { ...r, dist: d } : r)),
        );
        return d;
      };

      const destroy = (m: Live) => {
        gizmo.remove(m.a);
        gizmo.remove(m.b);
        gizmo.remove(m.bar);
        m.label.remove();
        live.splice(live.indexOf(m), 1);
      };

      function clearMeasure() {
        for (const m of live.slice()) destroy(m);
        if (firstDot) gizmo.remove(firstDot);
        firstDot = null;
        first = null;
        setMeasures([]);
        setPending(0);
      }

      function addMeasure(pa: T.Vector3, pb: T.Vector3) {
        const n = ++seq;
        const label = document.createElement("div");
        label.className = "vlabel";
        labelRef.current?.appendChild(label);
        const m: Live = {
          n,
          a: mkDot(pa),
          b: mkDot(pb),
          bar: new THREE.Mesh(barGeom, barMat),
          len: pa.distanceTo(pb),
          label,
        };
        m.bar.renderOrder = 999;
        gizmo.add(m.bar);
        live.push(m);
        // Seed the row before refresh(), which updates an existing row by n.
        setMeasures((prev) => [...prev, { n, dist: pa.distanceTo(pb) }]);
        refresh(m);
        // The bar has no scale until a frame runs, so size it now — otherwise it
        // renders once at scale (1, len, 1): a metre-wide cylinder.
        sizeGizmo();
      }

      /** Screen-space distance from an event to a point, in CSS pixels. */
      const screenDist = (p: T.Vector3, r: DOMRect, mx: number, my: number) => {
        proj.copy(p).project(camera);
        return Math.hypot(
          (proj.x * 0.5 + 0.5) * r.width - mx,
          (-proj.y * 0.5 + 0.5) * r.height - my,
        );
      };

      /** The endpoint under the cursor, if one is close enough to grab. */
      function handleAt(cx: number, cy: number) {
        const r = canvas.getBoundingClientRect();
        const mx = cx - r.left;
        const my = cy - r.top;
        // 14 px on a mouse; a fingertip needs more or the ends are ungrabbable.
        let bestD = window.matchMedia?.("(pointer: coarse)").matches ? 26 : 14;
        let best: { m: Live; end: T.Mesh } | null = null;
        for (const m of live) {
          for (const end of [m.a, m.b]) {
            const d = screenDist(end.position, r, mx, my);
            if (d < bestD) {
              bestD = d;
              best = { m, end };
            }
          }
        }
        return best;
      }

      function pickPoint(cx: number, cy: number) {
        const r = canvas.getBoundingClientRect();
        ndc.set(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
        ray.setFromCamera(ndc, camera);
        const hit = ray.intersectObject(root, true)[0];
        if (!hit) return;
        if (!first) {
          first = hit.point.clone();
          firstDot = mkDot(first);
          setPending(1);
        } else {
          if (firstDot) gizmo.remove(firstDot);
          firstDot = null;
          addMeasure(first, hit.point.clone());
          first = null;
          setPending(0);
        }
      }

      // Distinguish a tap from an orbit drag. Without this every rotation drops
      // a measurement point, which makes the viewer unusable on a phone.
      let down: { x: number; y: number; t: number; id: number } | null = null;
      let drag: { m: Live; end: T.Mesh } | null = null;
      const onDown = (e: PointerEvent) => {
        down = { x: e.clientX, y: e.clientY, t: performance.now(), id: e.pointerId };
        if (!modeRef.current) return;
        // Grabbing an endpoint must not also orbit the camera, so the controls
        // are switched off for the duration of the drag rather than fighting it.
        const grabbed = handleAt(e.clientX, e.clientY);
        if (grabbed) {
          drag = grabbed;
          controls.enabled = false;
          canvas.setPointerCapture?.(e.pointerId);
        }
      };
      const onMove = (e: PointerEvent) => {
        if (!drag) return;
        const r = canvas.getBoundingClientRect();
        ndc.set(
          ((e.clientX - r.left) / r.width) * 2 - 1,
          -((e.clientY - r.top) / r.height) * 2 + 1,
        );
        ray.setFromCamera(ndc, camera);
        const hit = ray.intersectObject(root, true)[0];
        // Off-surface moves are ignored rather than projected onto a plane: an
        // endpoint that slides into empty space would return a distance to
        // nothing, which is worse than an endpoint that simply does not follow.
        if (!hit) return;
        drag.end.position.copy(hit.point);
        refresh(drag.m);
      };
      const endDrag = (e?: PointerEvent) => {
        if (!drag) return;
        drag = null;
        controls.enabled = true;
        if (e) canvas.releasePointerCapture?.(e.pointerId);
      };
      const onUp = (e: PointerEvent) => {
        const d = down;
        down = null;
        if (drag) {
          endDrag(e);
          return;
        }
        if (!d || d.id !== e.pointerId || !modeRef.current) return;
        if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 8) return;
        if (performance.now() - d.t > 800) return;
        pickPoint(e.clientX, e.clientY);
      };
      const onCancel = (e: PointerEvent) => {
        down = null;
        endDrag(e);
      };
      canvas.addEventListener("pointerdown", onDown);
      canvas.addEventListener("pointermove", onMove);
      canvas.addEventListener("pointerup", onUp);
      canvas.addEventListener("pointercancel", onCancel);
      canvas.addEventListener("pointerleave", onCancel);

      apiRef.current = {
        clear: clearMeasure,
        reset: () => {
          camera.position.copy(home);
          controls.target.copy(target);
          controls.update();
        },
        remove: (n: number) => {
          const m = live.find((x) => x.n === n);
          if (m) destroy(m);
          setMeasures((prev) => prev.filter((r) => r.n !== n));
        },
      };

      /* ----------------------------------------------------------- resize */

      let vw = 1;
      let vh = 1;
      const resize = () => {
        const w = Math.max(1, host.clientWidth);
        const h = Math.max(1, host.clientHeight);
        vw = w;
        vh = h;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
      };
      resize();
      const ro = new ResizeObserver(resize);
      ro.observe(host);

      const tick = () => {
        raf = requestAnimationFrame(tick);
        controls.update();
        // BEFORE the draw: handles are sized from the camera distance, so doing
        // it after would show every frame's dots at the previous frame's zoom.
        sizeGizmo();
        renderer.render(scene, camera);
        // Each label tracks its own midpoint. Hidden when the midpoint falls
        // behind the camera (z > 1), which otherwise pins the label to an edge
        // of the box with a number for something nobody can see.
        for (const m of live) {
          mid
            .copy(m.a.position)
            .add(m.b.position)
            .multiplyScalar(0.5)
            .project(camera);
          if (mid.z > 1) {
            m.label.style.display = "none";
            continue;
          }
          m.label.style.display = "block";
          m.label.style.left = `${(mid.x * 0.5 + 0.5) * vw}px`;
          m.label.style.top = `${(-mid.y * 0.5 + 0.5) * vh}px`;
        }
      };
      raf = requestAnimationFrame(tick);

      // OrbitControls damping needs a continuous loop, but the detail page is
      // long and the panel is usually scrolled past. Rendering a 160k-triangle
      // mesh at 60 fps into a canvas nobody can see is a real battery cost on a
      // phone, so the loop stops while the panel is off-screen.
      let visible = true;
      const io = new IntersectionObserver(
        (entries) => {
          const now = entries[0]?.isIntersecting ?? true;
          if (now === visible) return;
          visible = now;
          if (visible && !raf) raf = requestAnimationFrame(tick);
          else if (!visible && raf) {
            cancelAnimationFrame(raf);
            raf = 0;
          }
        },
        { rootMargin: "200px" },
      );
      io.observe(host);

      let tris = 0;
      let verts = 0;
      root.traverse((o) => {
        const g = (o as T.Mesh).geometry as T.BufferGeometry | undefined;
        if (!g) return;
        const pos = g.getAttribute("position");
        if (!pos) return;
        verts += pos.count;
        tris += g.index ? g.index.count / 3 : 0;
      });

      setStats({
        extent: [size.x, size.y, size.z],
        tris: Math.round(tris),
        verts,
        grid: gridLabel,
        points: isPoints,
      });
      setPhase("ready");

      teardown.push(() => {
        canvas.removeEventListener("webglcontextlost", onLost);
        canvas.removeEventListener("pointerdown", onDown);
        canvas.removeEventListener("pointermove", onMove);
        canvas.removeEventListener("pointerup", onUp);
        canvas.removeEventListener("pointercancel", onCancel);
        canvas.removeEventListener("pointerleave", onCancel);
        // The labels are DOM children of the host, not of the canvas, so
        // removing the renderer does not take them with it.
        for (const m of live.slice()) destroy(m);
        ro.disconnect();
        io.disconnect();
        controls.dispose();
        disposeTree(scene);
        // The shared measurement geometry lives outside the scene graph when no
        // measurement is active, so disposeTree never reaches it.
        markerGeom.dispose();
        markerMat.dispose();
        barGeom.dispose();
        barMat.dispose();
        grid.dispose();
        renderer.dispose();
        // Without forceContextLoss the context survives the unmount. React's
        // StrictMode double-mount plus a few navigations is enough to hit the
        // browser's ~16 live context cap, after which the canvas goes black.
        renderer.forceContextLoss();
        canvas.remove();
        apiRef.current = null;
      });
    })();

    return () => {
      cancelled = true;
      ac.abort();
      if (raf) cancelAnimationFrame(raf);
      for (const fn of teardown) fn();
      teardown.length = 0;
    };
  }, [pick, href, armed, viewUp, webgl, metric, asset.files]);

  const clear = useCallback(() => apiRef.current?.clear(), []);
  const reset = useCallback(() => apiRef.current?.reset(), []);
  const removeMeasure = useCallback((n: number) => apiRef.current?.remove(n), []);

  /* ------------------------------------------------------------------- render */

  if (!pick) {
    return (
      <div className="panel">
        <h3>3D preview</h3>
        <p className="note">
          Nothing in this asset can be drawn in a browser
          {splat ? " — the reconstruction is a Gaussian splat, which needs a desktop viewer." : "."}{" "}
          The downloads below are unaffected.
        </p>
        <div className="inner" />
      </div>
    );
  }

  // The three figures in the pipeline's own order, each with the axis it is.
  // AXIS_READOUT is keyed on frameUp, so the camera-up button cannot touch this.
  const rows = stats
    ? AXIS_READOUT[frameUp].map((a) => ({ ...a, v: stats.extent[a.axis] }))
    : null;

  /**
   * How far the model on screen is from the size printed at the top of the page,
   * and — the part that matters — whether that gap is spread or a wrong file.
   */
  const printedMismatch = (() => {
    if (!printed || !rows) return null;
    // Component-wise, in the pipeline's axis order. This used to sort both
    // triples before comparing, which hides the exact bug it exists to catch: a
    // transposed length and width compare clean once each side is sorted, so 11
    // of 14 RGB-D assets printed a silently swapped readout with no warning.
    // The +/-2 in floor stays — that is the genuine spread between two
    // reconstructions of one capture, and it must not be reported as an error.
    const worst = Math.max(...rows.map((r, i) => Math.abs(r.v / IN - printed[i])));
    const rel = worst / Math.max(...printed);
    if (!(worst > 2 && rel > 0.05)) return null;

    // The RATIO decides the wording, not the inch gap. Reconstruction spread is
    // bounded: repeat runs of one capture move 3.6-7.4 in, and the two Poisson
    // solves of one cloud differ by 8-29% per axis. Nothing in that family
    // doubles a dimension. When a figure IS doubled, the two numbers are not two
    // takes on one object — bench_A7C9_fixed prints a 259 x 262 x 128 in ROOM
    // over a 28 x 28 x 25 in OBJECT, a factor of 9 — and calling 234 in of that
    // "the honest uncertainty of this scan" would itself be the dishonest claim.
    const ratio = Math.max(
      ...rows.map((r, i) => {
        const mine = Math.abs(r.v / IN);
        const hi = Math.max(mine, printed[i]);
        const lo = Math.min(mine, printed[i]);
        return lo > 0.05 ? hi / lo : Infinity;
      }),
    );
    return { worst, ratio, otherGeometry: ratio >= 2 };
  })();

  // One verdict for the whole page. Either source of doubt is enough: the
  // geometry comparison this panel can run, or the claim the header already
  // printed above the fold. Without the second half, a figure the publisher
  // flags — or that dimsClaim reads as room-scale — could be disputed in the
  // header and repeated here with no qualifier, which is the same
  // two-answers-one-file failure in a new place.
  const disputed = declaredDisputed || !!printedMismatch?.otherGeometry;

  // "± 1–2 in" is a claim about THE PRINTED FIGURE, not about the renderer, so
  // it cannot sit at the top of the panel beside a figure this same page has
  // just refused to stand behind. The header drops its tolerance chip for
  // exactly that reason (see page.tsx) — these two are on one screen, and a
  // tolerance here against "not safe to quote" there is the same
  // two-answers-one-figure failure the disputed state exists to prevent. Same
  // words as the header, so a customer reads one verdict twice rather than two.
  const tolChip = !metric
    ? { text: "no metric scale", cls: " warn" }
    : disputed
      ? { text: "not safe to quote", cls: " bad" }
      : { text: "± 1–2 in", cls: "" };

  // The printed size belongs to one specific file. Normally pickFile loads that
  // exact file, but it can only do that if the browser can draw it — a measure
  // pointing at, say, a .usd leaves us showing something else entirely, and the
  // customer has no way to tell from the picture.
  const measuredElsewhere =
    metric && asset.measure && !pick.declared ? asset.measure.name : null;
  // The switcher can load a SECOND reconstruction of the same capture. Those are
  // in real metres, so nothing above switches the tape measure off — but on this
  // repo's own assets they are 4-6x undersized partial-coverage fragments, so a
  // measurement taken here is wrong in a way the picture does not reveal.
  const altRecon = isAltReconstruction(pick.file);

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.loaded / progress.total) * 100))
      : 0;

  return (
    <div className="panel">
      <h3>
        <span aria-hidden>◳</span> {metric ? "View and measure" : "View"}
        <span className={`chip${tolChip.cls}`} style={{ marginLeft: "auto" }}>
          {tolChip.text}
        </span>
      </h3>
      <p className="note">
        Drag to spin · scroll to zoom
        {metric ? " · tap two points to measure" : " · no size to measure"}
      </p>

      <div
        className={`viewer${measuring && phase === "ready" ? " measuring" : ""}`}
        ref={hostRef}
        tabIndex={0}
        role="application"
        aria-label={`3D view of ${asset.title}`}
      >
        {/* Labels are created imperatively, one per measurement, and parented
            here rather than to the canvas so they scroll and resize with the
            box. Pointer events stay off them or a label over an endpoint would
            block the drag that moves it. */}
        <div className="vlabels" ref={labelRef} />

        {/* The verdict about what is on screen, IN the box with it. The full
            explanations stay in their paragraphs below, but those sit under a
            toolbar that wraps to three or four rows on a phone — two screens
            from the figure the customer is reading. Both flags are re-printed
            from values already computed above (`disputed` at the printedMismatch
            block, `altRecon` from isAltReconstruction) so this cannot become a
            second opinion. */}
        {disputed ? (
          <div className="vstatus bad">Printed size disputed — read below</div>
        ) : altRecon ? (
          <div className="vstatus">Comparison mesh — do not quote a size off it</div>
        ) : !metric ? (
          <div className="vstatus">Shape only — no real-world size</div>
        ) : null}

        {webgl === false && (
          <div className="center">
            <b>This browser cannot run WebGL.</b>
            <span>
              The 3D view needs it, but every file below still downloads normally — open{" "}
              <code>{pick.file.name}</code> in Blender, MeshLab or your CAD tool.
            </span>
          </div>
        )}
        {webgl !== false && !armed && (
          <div className="center">
            <span>
              <code>{pick.file.name}</code> is {mb(pick.file.bytes)}.
            </span>
            <button type="button" className="dl" onClick={() => setArmed(true)}>
              Load in browser ({mb(pick.file.bytes)})
            </button>
            <span>Nothing is downloaded until you ask.</span>
          </div>
        )}
        {/* webgl===null && armed && phase==="idle" is the SERVER render and the
            moment before the effect runs — it matched none of the branches, so
            the first thing a customer saw was a wordless 300px black rectangle,
            and a reader with JS disabled never saw anything else. */}
        {webgl !== false && armed && phase === "idle" && (
          <div className="center">
            <span>Preparing the 3D view of {pick.file.name}…</span>
            <span>
              {claim.state === "measured"
                ? "If it does not appear, the size above was still measured for you and every file below downloads normally."
                : "If it does not appear, every file below still downloads normally."}
            </span>
          </div>
        )}
        {webgl !== false && armed && phase === "loading" && (
          <div className="center">
            <span>Loading {pick.file.name}…</span>
            <span
              className="meter"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct}
            >
              <i style={{ width: `${pct}%` }} />
            </span>
            <span>
              {progress ? `${mb(progress.loaded)} of ${mb(progress.total)}` : "starting"}
              {pct ? ` · ${pct}%` : ""}
            </span>
          </div>
        )}
        {/* The one thing nobody works out unaided. Shown only while it is
            actionable — measuring armed, model ready, nothing placed yet — so it
            never nags over a finished measurement. */}
        {webgl !== false && phase === "ready" && measuring && !measures.length && !pending && (
          <div className="vhint">Tap two points on the model to measure</div>
        )}
        {/* Once one is placed the hint would nag, but the follow-up gesture —
            that an end can be dragged — is not discoverable at all, so it is
            said once, while the first measurement is the only one on screen. */}
        {webgl !== false && phase === "ready" && measuring && measures.length === 1 && (
          <div className="vhint">Drag either red dot to adjust · tap again for another</div>
        )}
        {webgl !== false && phase === "error" && (
          <div className="center">
            <b style={{ color: "var(--bad)" }}>The 3D view failed to load.</b>
            <span>{error}</span>
            <a className="dl" href={downloadHref(pick.file)}>
              ↓ Download {pick.file.name} instead
            </a>
          </div>
        )}
      </div>

      <div className="vbar">
        <button
          type="button"
          className="vbtn primary"
          onClick={() => setMeasureMode((v) => !v)}
          aria-pressed={measuring}
          disabled={phase !== "ready" || !metric}
          title={metric ? undefined : "This mesh has no real-world scale, so there is nothing to measure"}
        >
          {!metric ? "Nothing to measure" : measuring ? "📏 Measuring" : "📏 Measure"}
        </button>
        {/* View and measure ANY reconstruction the asset ships — KIRI Engine,
            AliceVision, TRELLIS, the RGB-D fuse. Promoted out of the utility
            cluster and labelled by RECONSTRUCTION rather than by filename: a
            customer reported the KIRI meshes were missing when every one of them
            was already in this menu, spelled `kiri_visual.glb` in 12px grey
            among four other files. The ranked default still opens first, because
            it is the file the printed size was measured on. */}
        {viewables.length > 1 && (
          <label className="vpick">
            <span>Showing</span>
            <select
              value={chosenRel ?? pick?.file.rel ?? ""}
              onChange={(e) => setChosenRel(e.target.value)}
              title="Choose which reconstruction to view and measure"
            >
              {viewables.map((f) => (
                <option key={`${f.group}/${f.rel}`} value={f.rel}>
                  {reconLabel(f)} — {f.name} ({f.size})
                </option>
              ))}
            </select>
          </label>
        )}
        {/* Utilities, visually quieter and pushed right, so the bar reads as one
            action plus some controls rather than five equal choices. */}
        <span className="util">
        <button
          type="button"
          className="vbtn"
          onClick={clear}
          disabled={phase !== "ready" || (!pending && !measures.length) || !metric}
        >
          Clear{measures.length > 1 ? ` all (${measures.length})` : ""}
        </button>
        <button type="button" className="vbtn" onClick={reset} disabled={phase !== "ready"}>
          Reset view
        </button>
        {/* Camera only. The label says "Camera up" and not "Up axis" because the
            button changes where the camera stands, not what the file is: the
            names below (length, width, height) and the comparison against the
            printed size stay in the file's own frame either way. */}
        <button
          type="button"
          className="vbtn"
          onClick={() => setViewUpOverride(viewUp === "z" ? "y" : "z")}
          disabled={phase !== "ready"}
          title={
            "Rotates the camera if the model looks like it is lying on its side. " +
            "It re-orients the view only — the length/width/height names below are fixed by " +
            `the file's own frame (${frameUp.toUpperCase()}-up) and do not change.`
          }
        >
          Camera up: {viewUp.toUpperCase()}
        </button>
        </span>
        {viewUp !== frameUp && (
          <span className="vflag">view rotated · file frame unchanged</span>
        )}
        {stats && (
          <span className="vlegend">
            grid = {stats.grid} · file frame = {frameUp.toUpperCase()}-up
          </span>
        )}
      </div>

      <div className="inner">
        {/* The measurements, newest last, in the same shape as the splat
            viewer's list so the two tapes read as one tool. Inches lead because
            that is what the rest of the page quotes; metres follow in the same
            row rather than in a second table, which is how one distance used to
            print two figures a customer had to reconcile. */}
        {metric && (measures.length > 0 || pending === 1) && (
          <div className="mlist" aria-live="polite">
            {measures.map((m) => (
              <div className="mrow" key={m.n}>
                <b>{m.n}</b>
                <span>
                  {inchStr(m.dist)} <em>{mmStr(m.dist)}</em>
                </span>
                <button
                  type="button"
                  onClick={() => removeMeasure(m.n)}
                  aria-label={`Delete measurement ${m.n}`}
                  title="Delete"
                >
                  ×
                </button>
              </div>
            ))}
            {pending === 1 && <div className="mrow wait">tap the second point</div>}
          </div>
        )}

        {/* Every figure here is suppressed rather than relabelled when the
            geometry has no scale: an inch reading off a unit-cube mesh is not
            imprecise, it is invented. */}
        {rows && (
          <p className="vsize">
            {metric ? (
              <>
                <span className="k">Overall</span>{" "}
                <b>{rows.map((r) => inchStr(r.v)).join(" × ")}</b>{" "}
                <em>{rows.map((r) => r.label).join(" × ")}</em>
              </>
            ) : (
              <>
                <span className="k">Overall</span>{" "}
                <b>{rows.map((r) => unitStr(r.v)).join(" × ")}</b>{" "}
                <em>model units — not a size</em>
              </>
            )}
          </p>
        )}

        {/* Short, and in the customer's terms. The full reasoning moved into the
            details block below — it was four to six sentences of pipeline
            explanation sitting between the customer and the download button. */}
        {printedMismatch?.otherGeometry ? (
          <p className="vwarn bad">
            <b>Do not quote either size.</b> The model here and the size at the top of
            the page differ {printedMismatch.ratio.toFixed(1)}× — they describe
            different things. Ask us to re-run this scan.
          </p>
        ) : declaredDisputed ? (
          <p className="vwarn bad">
            <b>Do not quote the size at the top of the page.</b> Measure in the file
            you will actually use, or ask us to re-run this scan.
          </p>
        ) : printedMismatch !== null ? (
          <p className="vwarn">
            This model and the size at the top of the page differ by up to{" "}
            {Math.round(printedMismatch.worst)} in — normal spread between two
            reconstructions of one capture. Trust the file you will use.
          </p>
        ) : null}

        {altRecon && (
          <p className="vwarn">{ALT_RECON_NOTE}</p>
        )}
        {measuredElsewhere && (
          <p className="vwarn">
            The size at the top of the page was measured on{" "}
            <code>{measuredElsewhere}</code>, which this browser cannot draw. You are
            looking at a different file.
          </p>
        )}
        {!metric && (
          <p className="vwarn nometric">
            <b>Shape only — this model has no real-world size.</b> {scaleInfo.why}
          </p>
        )}

        {/* Everything a customer does not need in order to use the scan. It was
            all full-width body text: the provenance line, the triangle count,
            and a seven-line paragraph about rounding. Kept verbatim, one click
            away, because importers and anyone checking our figures do need it. */}
        <details className="vmore">
          <summary>Details</summary>
          <p>
            Showing <code>{pick.file.name}</code>
            {pick.file.group ? ` from ${pick.file.group}` : ""} — {pick.source}.
            {stats
              ? ` ${stats.points ? `${stats.verts.toLocaleString()} points` : `${stats.tris.toLocaleString()} triangles`}.`
              : ""}{" "}
            File frame {frameUp.toUpperCase()}-up
            {stats ? `, grid = ${stats.grid}` : ""}.
            {asset.frame ? (
              <>
                {" "}
                Coordinate frame <code>{asset.frame}</code>.
              </>
            ) : null}
          </p>
          {metric ? (
            <p>
              <b>Accuracy: about ± 1–2 inches.</b> Repeat reconstructions of the same
              capture move by 3.6–7.4 in and the surface sits about 19 mm off true, so
              figures are rounded to the inch on purpose — a finer digit would claim
              precision this scan does not have. The millimetre figures are that same
              rounded inch converted, not a second measurement. Quarter-inch values in
              the downloads (<code>inches_0_25</code> in <code>dims.json</code>) are a
              rounding convention, not a tolerance. Good for fit and clearance, not for
              cutting parts.
            </p>
          ) : (
            <p>
              No figure here is given in inches or millimetres and the tape measure is
              off, because the geometry carries no scale — a measure tool would still
              return a number, and it would describe the generator&rsquo;s proportions.
              To get a measurable asset, re-capture with depth (an iPhone or iPad Pro
              with LiDAR) or metricize this mesh against a printed marker of known size.
            </p>
          )}
          {printedMismatch?.otherGeometry && (
            <p>
              The two figures differ by up to {Math.round(printedMismatch.worst)} in on
              one axis, a factor of {printedMismatch.ratio.toFixed(1)}. A gap that large
              is not the spread between two reconstructions; at that size they describe
              different geometry, typically a whole-room mesh where the object was
              expected. We cannot tell from here which one is the object you scanned.
            </p>
          )}
          {declaredDisputed && claim.state === "disputed" && <p>{claim.why}</p>}
        </details>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ material */

/**
 * The base-colour map a Wavefront OBJ points at, or null when there isn't one.
 *
 * OBJ keeps colour outside the geometry: the .obj names a .mtl, and the .mtl
 * names an image in `map_Kd`. KIRI's export is exactly this shape — 3DModel.obj
 * carries UVs and no vertex colours at all — so rendering it with the default
 * surface material produces a flat grey model beside a 4K texture the customer
 * can only see by downloading all three files.
 *
 * The chain is resolved against the manifest rather than by handing MTLLoader a
 * relative path: published files live at opaque blob URLs behind /api/download,
 * so `map_Kd 3DModel.jpg` resolved relative to the .obj's URL would 404.
 */
async function objTextureMap(
  THREE: typeof import("three"),
  obj: AssetFile,
  files: AssetFile[],
  signal: AbortSignal,
): Promise<T.Texture | null> {
  const dir = obj.rel.slice(0, obj.rel.length - obj.name.length);
  // Siblings only. An asset can ship several OBJ/MTL pairs in different
  // subdirectories, and pairing an .obj with a .mtl from elsewhere would paint
  // one reconstruction with another's texture.
  const beside = (pred: (f: AssetFile) => boolean) =>
    files.find(
      (f) =>
        f.rel.startsWith(dir) &&
        !f.rel.slice(dir.length).includes("/") &&
        pred(f),
    );

  const mtl = beside((f) => /\.mtl$/i.test(f.name));
  if (!mtl) return null;

  let mapName: string | null = null;
  try {
    const res = await fetch(inlineHref(mtl), { signal });
    if (!res.ok) return null;
    const hit = /^[ \t]*map_Kd[ \t]+(.+?)[ \t]*$/im.exec(await res.text());
    // map_Kd may carry options (-s, -o) or a path; take the last token's basename.
    mapName = hit ? hit[1].split(/[\s]+/).pop()!.split(/[\\/]/).pop()! : null;
  } catch {
    return null;
  }
  if (!mapName) return null;

  const img = beside((f) => f.name.toLowerCase() === mapName!.toLowerCase());
  if (!img) return null;
  try {
    const tex = await new THREE.TextureLoader().loadAsync(inlineHref(img));
    // A base-colour map is authored in sRGB. Left linear it renders visibly
    // dark and desaturated, which reads as a bad reconstruction rather than a
    // colour-space mistake.
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  } catch {
    return null;
  }
}

function texturedMaterial(
  THREE: typeof import("three"),
  map: T.Texture,
): T.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    map,
    roughness: 1,
    metalness: 0,
    side: THREE.DoubleSide,
  });
}

function surfaceMaterial(
  THREE: typeof import("three"),
  vertexColors: boolean,
): T.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    vertexColors,
    color: vertexColors ? 0xffffff : 0xaebac7,
    roughness: 1,
    metalness: 0,
    // Poisson surfaces come out of the fuse with inconsistent winding in
    // places; single-sided rendering punches holes in them.
    side: THREE.DoubleSide,
  });
}

/**
 * Metres or millimetres — for geometry that is in one of the two to begin with.
 *
 * `metric` is not an optimisation, it is the precondition. Every branch below
 * answers "which metric unit is this file in", and none of them can answer
 * "is this file in a metric unit at all" — size cannot tell a 1 m object from a
 * 1-unit normalized one. That is precisely how a unit-cube TRELLIS mesh
 * (0.998 x 0.973 x 0.408) fell through the old `maxDim > 25 ? mm : metres` line,
 * was declared to be in metres, and got published as a 39-inch kick scooter with
 * a working tape measure over it. Provenance decides (see scaleVerdict); when it
 * says there is no scale the geometry is left exactly as authored and the UI
 * prints no unit for it, so the value returned here is only ever a render scale.
 *
 * With that settled: the `*_mm.*` / `*_m.*` suffixes are the pipeline's own and
 * are authoritative. Plain `.stl`/`.obj` carry no hint, so an implausibly large
 * extent falls back to millimetres — nothing published here is 25 m across, but
 * plenty of it is 2500 mm.
 */
function unitScale(name: string, box: T.Box3, metric: boolean): number {
  if (!metric) return 1;
  const n = name.toLowerCase();
  if (/_mm\.[a-z0-9]+$/.test(n)) return 0.001;
  if (/_m\.[a-z0-9]+$/.test(n)) return 1;
  const maxDim = Math.max(box.max.x - box.min.x, box.max.y - box.min.y, box.max.z - box.min.z);
  return maxDim > 25 ? 0.001 : 1;
}
