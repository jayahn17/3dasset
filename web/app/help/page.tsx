import Link from "next/link";
import type { Metadata } from "next";
import {
  DECISION_GUIDE,
  FORMATS,
  FORMAT_ORDER,
  PRECISION_NOTE,
  appsByTier,
  type FormatInfo,
} from "../../lib/formats";

export const metadata: Metadata = {
  title: "File format guide",
  description:
    "Which application opens each file this pipeline produces, what units it is in, and where the measurement is.",
};

// A standalone reference, deliberately static: nothing here reads the
// manifest, so it stays valid when there are no scans published yet and it is
// the same page for every customer. The per-asset version of this table (which
// shows only the formats that asset actually contains) is the Formats panel on
// an asset page.

function Apps({ info }: { info: FormatInfo }) {
  const free = appsByTier(info, "free");
  const paid = appsByTier(info, "paid");
  return (
    <>
      {free.length > 0 && (
        <p style={{ margin: "0 0 8px" }}>
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

export default function HelpPage() {
  const formats = FORMAT_ORDER.map((ext) => FORMATS[ext]).filter(
    (f): f is FormatInfo => Boolean(f),
  );

  return (
    <>
      <header className="site">
        <div className="wrap">
          <h1>File format guide</h1>
          <p className="sub">
            Every scan is exported into several formats because no single one
            opens everywhere. This page says which application opens which file,
            what units it is written in, and where the measurement actually
            lives. Free tools are listed first — you do not need a CAD licence
            to check a dimension.
          </p>
        </div>
      </header>

      <div className="wrap">
        <div className="panel">
          <h3>
            <span aria-hidden>⚠️</span> How accurate these numbers are
          </h3>
          <div className="inner">
            <p style={{ marginTop: 10 }}>{PRECISION_NOTE}</p>
            <p className="muted">
              Where that comes from: the same capture reconstructed three
              defensible ways — the fused point cloud, the meshed surface over
              it, and the CAD route from the splat — disagrees by roughly an
              inch on a shoebox-sized object and by several inches on furniture.
              All three are honest reconstructions. That spread, not the
              rounding step, is the real tolerance.
            </p>
            <p className="muted">
              Practical consequence: treat a printed size as a good working
              figure, verify anything load-bearing against the geometry
              yourself, and do not assume a 2 in packing margin is safe on the
              strength of these files alone.
            </p>
          </div>
        </div>

        <h2>Which file do I want?</h2>
        <div className="panel">
          <div className="inner">
            <table>
              <thead>
                <tr>
                  <th style={{ width: "22%" }}>I want to</th>
                  <th style={{ width: "28%" }}>download</th>
                  <th>why that one</th>
                </tr>
              </thead>
              <tbody>
                {DECISION_GUIDE.map((d) => (
                  <tr key={d.want}>
                    <td>
                      <b>{d.want}</b>
                    </td>
                    <td>
                      <code>{d.use}</code>
                    </td>
                    <td className="muted">{d.why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <h2>Units, at a glance</h2>
        <div className="panel">
          <p className="note">
            None of STL, OBJ or DXF records a unit inside the file — the
            importer guesses, and the filename is the only place the answer
            exists. This is the single most common way a measurement goes wrong
            by a factor of 25.4 or 1000. Rows are read top to bottom: the last
            two override everything above them, because a mesh with no
            real-world scale has no units to get right. Units are only half the
            question, though — the <code>mesh_preview/</code> row is in correct
            metres and is still not your object, which is a thing no unit can
            say. Your own asset page shows the unit against each file it
            actually shipped — that is the authority; this table is the general
            rule.
          </p>
          <div className="inner">
            <table>
              <thead>
                <tr>
                  <th style={{ width: "34%" }}>filename pattern</th>
                  <th style={{ width: "20%" }}>units</th>
                  <th>note</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>
                    <code>*_mm.stl</code>, <code>*_mm.obj</code>,{" "}
                    <code>*.dxf</code>
                  </td>
                  <td>millimetres</td>
                  <td className="muted">
                    The CAD copies off a scan route. Set the importer to
                    millimeters explicitly. <code>asset_trellis_mm.stl</code>{" "}
                    and <code>asset_trellis_mm.obj</code> are the exception —
                    millimetre vertices over a shape a generator invented; see
                    the generative row below, which overrides this one for them.
                  </td>
                </tr>
                <tr>
                  <td>
                    <code>scale_check_100mm.stl</code>
                  </td>
                  <td>millimetres</td>
                  <td className="muted">
                    A 100 mm cube. Import it once alongside the part and measure
                    it — if it does not read 100 mm, your import units are wrong
                    and nothing else you measure is trustworthy. Only the CAD
                    route writes one, so not every download has it.
                  </td>
                </tr>
                <tr>
                  <td>
                    <code>*_m.ply</code>, <code>*_m.obj</code>,{" "}
                    <code>*_cloud_m.ply</code>, <code>*_visual.glb</code>,{" "}
                    <code>*.usda</code>, <code>*.splat</code>
                  </td>
                  <td>metres</td>
                  <td className="muted">
                    The metric copies. Blender reads these correctly with Units
                    ▸ Length set to either Meters or Millimeters. Metres is
                    not the same as &ldquo;this is your object&rdquo;: match the
                    folder too, because{" "}
                    <code>mesh_preview/object_mesh_m.ply</code> also ends in{" "}
                    <code>_m.ply</code> and is a different thing — see the{" "}
                    <code>mesh_preview/</code> row below.
                  </td>
                </tr>
                <tr>
                  <td>
                    <code>collision_NN.obj</code>, <code>*_visual.obj</code>
                  </td>
                  <td>metres</td>
                  <td className="muted">
                    Simulation geometry. Note these are OBJ files in metres
                    while <code>*_mm.obj</code> is the same format in
                    millimetres — check the name, not the extension.
                  </td>
                </tr>
                <tr>
                  <td>
                    <code>object_mesh.ply</code>, <code>object_mesh.glb</code>,{" "}
                    <code>object.splat</code>
                  </td>
                  <td>metres</td>
                  <td className="muted">
                    Straight from the RGB-D fuse, in the{" "}
                    <code>object_asset/</code> folder.{" "}
                    <code>object_asset/object_mesh.ply</code> is the geometry
                    the printed dimensions were measured from.
                  </td>
                </tr>
                {/*
                  The third state, and the reason this table needs a row that
                  is not about units at all. On BE53A423,
                  mesh_preview/object_mesh_m.ply is the only *_m.ply in the
                  download, so every "use a *_m.ply" instruction on this site
                  resolved to it. Measured with trimesh: 1.7197 x 0.2883 x
                  2.2886 m; in the ARKit y-up frame (x=width, y=height,
                  z=length) that is length 90.10 / width 67.71 / height 11.35 in
                  against a printed 9.00 x 5.75 x 11.00. The download's own
                  CrateScan-BE53A423_cratescan/dims.json says the same thing
                  ("source": "cratescanner", "measurement": null, 90.00 x 67.75
                  x 11.25 in).

                  It is genuinely in metres, so fileIsMetric chips it "metres"
                  and is right to. Nothing about metric-ness is being restated
                  here: this row answers a different question, and
                  isCaptureScale() in lib/formats.ts is the one place that
                  answers it. Viewer.tsx's NEVER_PICK excludes the same folder
                  so the tape measure never loads it.
                */}
                <tr>
                  <td>
                    <code>mesh_preview/</code> — anything inside it
                  </td>
                  <td>metres</td>
                  <td className="muted">
                    In metres, and not your object. This is the raw on-device
                    sweep the scan was cut out of — the floor and whatever else
                    was in frame — kept beside the result for reference. Its
                    filename is two characters from the mesh you want (
                    <code>mesh_preview/object_mesh_m.ply</code> against{" "}
                    <code>object_asset/object_mesh.ply</code>) and its unit is
                    the same, because both really are metric, so no unit chip
                    can separate them and only the folder does. Measured it
                    returns the size of the sweep, feet across whatever the
                    object is. Nothing here is wrong with the file; it is simply
                    a different subject. The viewer on your asset page never
                    loads it, and your asset page names it under the format
                    table when your download has one.
                  </td>
                </tr>
                {/*
                  This row exists because the table used to cover only one of
                  the three files an RGB-only capture ships. Verified on
                  demo_out/crate_20260730_scooter/rgb_only_trellis/: it contains
                  asset_trellis.glb, .obj and .ply. Only the .glb matched a
                  pattern (the metres row), so the customer was told "metres"
                  for a unit-cube mesh and told nothing at all for the other
                  two. Naming the stem, not the extension, is what covers all
                  three — the same reason unitsForFile keys on the filename.

                  The *_mm exports are named here for the same reason: they
                  matched the "*_mm.stl, *_mm.obj → millimetres" row at the top
                  of this table and nothing else, so this page said millimetres
                  where the asset page one click away chips them "none — no
                  real-world scale". They belong to the override, not to the
                  CAD-copies rule.

                  Numbers measured with trimesh against the manifest's printed
                  dims (raw .glb read off the glTF POSITION accessor bounds).
                  A trellis export is y-up (Viewer.tsx guessUp) and the page
                  reads a y-up file as length = Z, width = X, height = Y
                  (AXIS_READOUT, matching measure.py's "ARKit: x=width,
                  y=height, z=length"), so the mm triples below are converted
                  into that order before being compared with a printed L x W x H.
                  Lining up raw x,y,z against a printed L x W x H instead is the
                  cross-axis mistake this block used to make.
                    crate_20260730_scooter  0.998 x 0.973 x 0.408 model units,
                      i.e. "39.29 x 38.29 x 16.04 in" if anything reads it as
                      metres. No RGB-D pass, no _mm export.
                    76B73843  asset_trellis_mm.stl 184.7 x 355.6 x 185.6 mm
                      (x,y,z) -> L 7.31 / W 7.27 / H 14.00 in vs a printed
                      13.25 x 10.75 x 14.00. The longest edge is the height and
                      it carries the fit: L is 5.94 in short, W 3.48 in short.
                    1B38880A  157.7 x 266.7 x 78.9 mm -> L 3.11 / W 6.21 /
                      H 10.50 in vs a printed 7.75 x 10.50 x 9.50. The mesh's
                      longest edge is its height and it was scaled to the
                      printed WIDTH, so no axis matches its own: L 4.64 in
                      short, W 4.29 in short, H 1.00 in OVER.
                    AB12CD34  273.1 x 355.6 x 336.6 mm -> L 13.25 / W 10.75 /
                      H 14.00 in vs a printed 13.25 x 10.75 x 14.00 — all three
                      axes, the only genuinely per-axis pair published.
                  Worst single-axis miss across the three: 5.94 in, which is the
                  figure the copy below quotes. It used to say "up to 7 in",
                  from a 7.39 in that paired printed width (10.50) against mesh
                  length (3.11) — two different axes. Conservative, but a page
                  about measurement honesty cannot cite a number its own files
                  do not support.
                  The per-axis code in assetpipe/scene/trellis_metric.py:145-165
                  postdates two of those three exports and is not even committed
                  yet; the committed path is a uniform longest-edge fit followed
                  by apply_scale(1000.0). This row describes the files a
                  customer can download, not the ones a rerun would write.
                */}
                <tr>
                  <td>
                    <code>asset_trellis.glb</code>,{" "}
                    <code>asset_trellis.obj</code>,{" "}
                    <code>asset_trellis.ply</code>,{" "}
                    <code>asset_trellis_mm.stl</code>,{" "}
                    <code>asset_trellis_mm.obj</code> — and anything inside an{" "}
                    <code>rgb_only_trellis/</code> folder
                  </td>
                  <td>none</td>
                  <td className="muted">
                    The generator&rsquo;s own output. TRELLIS reconstructs a
                    shape from photographs and normalizes it to roughly a unit
                    cube, so it has proportions but no size — and the{" "}
                    <code>.glb</code>, <code>.obj</code> and <code>.ply</code>{" "}
                    are that same geometry written three ways. Opened in Blender
                    they read as tens of inches whatever the real object was.
                    Where a depth pass <em>did</em> rescale one, it matched a
                    single edge — the longest — and the millimetre exports
                    beside it (<code>asset_trellis_mm.stl</code>,{" "}
                    <code>asset_trellis_mm.obj</code>, with a{" "}
                    <code>dims_mm.json</code>) inherit that fit. Those hold real
                    millimetres and are still not a measurement: the shape is
                    the generator&rsquo;s, sized to the scan&rsquo;s bounding
                    box. Of the pairs published here only one has all three axes
                    matched. On the others a single edge carries the fit, and it
                    is not necessarily laid on the axis it belongs to: one of
                    them has the mesh&rsquo;s longest edge, its height, scaled
                    to the printed width, so all three of its axes are wrong.
                    Against the size printed on the same asset page these files
                    run as much as 5.9 in under on one axis, and 1.0 in over on
                    another. Your asset page chips every one of them
                    “none — no real-world scale”. Measure the RGB-D fuse
                    instead where the capture has one —{" "}
                    <code>object_mesh.ply</code>, in metres — and quote the
                    dimensions the asset page prints, within the tolerance
                    above. Where it has none, nothing in that download has a
                    size to quote.
                  </td>
                </tr>
                <tr>
                  <td>
                    <code>NO_METRIC_SCALE.txt</code> present
                  </td>
                  <td>none</td>
                  <td className="muted">
                    That capture had no depth data at all, so every mesh in that
                    folder is normalized rather than metric — whatever it is
                    named, and whether or not it matches any pattern above.
                    This row overrides every row above it. The reverse does not
                    follow: the file&rsquo;s <em>absence</em> is not a promise of
                    metric scale, because a bare generative mesh (the row above)
                    can ship with no marker beside it. When the two disagree,
                    the per-file unit on your asset page is the answer.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <h2>Every format</h2>
        {formats.map((info) => (
          <div className="panel" key={info.ext}>
            <h3>
              <code>{info.ext}</code> {info.label}
            </h3>
            <p className="note">{info.units}</p>
            <div className="inner">
              <p style={{ marginTop: 10 }}>{info.what}</p>

              <p
                className="muted"
                style={{ margin: "16px 0 6px", textTransform: "uppercase", fontSize: 11, letterSpacing: ".05em" }}
              >
                opens in
              </p>
              <div style={{ fontSize: 14 }}>
                <Apps info={info} />
              </div>

              {info.measure && (
                <>
                  <p
                    className="muted"
                    style={{ margin: "16px 0 6px", textTransform: "uppercase", fontSize: 11, letterSpacing: ".05em" }}
                  >
                    how to measure in it
                  </p>
                  <p style={{ margin: 0, fontSize: 14 }}>{info.measure}</p>
                </>
              )}

              {info.trap && (
                <p style={{ margin: "16px 0 0", color: "var(--warn)", fontSize: 14 }}>
                  Watch out: {info.trap}
                </p>
              )}
            </div>
          </div>
        ))}

        <h2>If a file will not open</h2>
        <div className="panel">
          <div className="inner">
            <table>
              <thead>
                <tr>
                  <th style={{ width: "40%" }}>symptom</th>
                  <th>cause</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>The model is 1000× too big or too small</td>
                  <td className="muted">
                    Millimetre file imported as metres, or the reverse. Where
                    your download has a <code>scale_check_100mm.stl</code>,
                    import it and measure it to find out which way round it is;
                    only the CAD route ships one. Where it does not, do not use
                    the printed dimensions as the check instead — they were
                    measured off a different file, so a mismatch cannot tell a
                    units error apart from a file that never held a measurement.
                    Set the importer to millimeters for any{" "}
                    <code>.stl</code>, <code>.dxf</code> or{" "}
                    <code>*_mm.obj</code> and to meters for everything else, and
                    read the unit chip on your asset page to confirm.
                  </td>
                </tr>
                <tr>
                  <td>The model is 25.4× off</td>
                  <td className="muted">
                    The importer assumed inches. Set it to millimeters and
                    reimport.
                  </td>
                </tr>
                <tr>
                  <td>The mesh imports untextured or flat grey</td>
                  <td className="muted">
                    The <code>.mtl</code> and its <code>.png</code> texture need
                    to sit in the same folder as the <code>.obj</code>. Vertex
                    colours in OBJ and PLY are also dropped by most CAD tools —
                    Blender, MeshLab and CloudCompare keep them.
                  </td>
                </tr>
                <tr>
                  <td>The MJCF or URDF will not compile</td>
                  <td className="muted">
                    Both reference the <code>collision_NN.obj</code> and{" "}
                    <code>*_visual.obj</code> meshes by relative path, so
                    download the whole folder into one place, not just the
                    description file. If those meshes are not in the download at
                    all, no path fixes it — the asset page says so on the format
                    row. The <code>.usda</code> is not in this category: every
                    mesh is written into the file itself, so it never looks for
                    an OBJ.
                  </td>
                </tr>
                <tr>
                  <td>A PLY opens but appears empty</td>
                  <td className="muted">
                    <code>*_cloud_m.ply</code> is vertices only, no faces. A
                    surface renderer shows nothing. Open it in CloudCompare, or
                    switch the viewport to point rendering.
                  </td>
                </tr>
                <tr>
                  <td>The splat looks sharp but will not measure</td>
                  <td className="muted">
                    By design — a gaussian splat has no surface to pick against.
                    Take any number you intend to quote off the metric mesh
                    instead: <code>object_asset/object_mesh.ply</code>, or a{" "}
                    <code>*_m.ply</code> or <code>*_mm.stl</code> from the CAD
                    route — not <code>asset_trellis_mm.*</code>, which the asset
                    page chips as having no real-world scale, and not{" "}
                    <code>mesh_preview/object_mesh_m.ply</code>, which is in
                    honest metres and is the room rather than the object.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <Link href="/" className="back">
          ← all scans
        </Link>
      </div>
    </>
  );
}
