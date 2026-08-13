// Which application opens which extension, and how to measure in it.
//
// The customer's actual question is never "what is a .usda" — it is "I have
// this file, what do I open it with, and where is the number". So every entry
// carries: what the format is, the units THIS pipeline exports it in, named
// applications split free/paid, and the click path to a dimension.
//
// Units are the trap that keeps biting. Nothing here is inferable from the
// extension alone: .obj ships in both mm (`*_mm.obj`, the CAD copy) and metres
// (`collision_*.obj`, `*_visual.obj`, `*_m.obj`, the sim copies), and neither
// OBJ nor STL nor DXF has any field in the file that declares a unit. That is
// why `unitsForFile()` keys on the filename, not the extension, and why
// `scale_check_100mm.stl` exists at all.
//
// And one fact outranks all of that: some geometry has no real-world scale in
// any format it is written to. An RGB-only capture is one case; a raw
// generative (TRELLIS) mesh that no depth pass ever sized is the other, and it
// can ship with no marker file to say so. That is a property of the capture and
// of the file's provenance, never of its extension, so this module does not
// decide it — it arrives as an argument.
//
// A file written in millimetres is not thereby a measured file, and the copy
// here must never imply it is. asset_trellis_mm.stl has honest millimetre
// vertices over a shape a generator invented: on 76B73843 it measures, in the
// length/width/height order the page prints, 7.31 x 7.27 x 14.00 in where the
// scan says 13.25 x 10.75 x 14.00. Sentences
// about a format family sit directly above per-file unit chips on the asset
// page, so a family-level "lands at the right size" reads as a claim about the
// one file underneath it. Hedge the family, let the chip speak for the file.
//
// The single definition lives in lib/manifest.ts (hasMetricScale for the asset,
// fileIsMetric for one file) and every surface on the page reads it from there.
// This module used to own a second copy, and that is exactly what let the
// viewer print "no metric scale" while the format table beside it showed a
// "metres" chip for the same .glb. One definition, one answer per screen.
//
// There is a THIRD state, and it is the one a unit chip structurally cannot
// express: true metres over the wrong geometry. A scan is saved beside the raw
// on-device sweep it was cut out of (mesh_preview/object_mesh_m.ply — floor and
// surroundings, 90 x 68 x 11 in on the one published example, against a 9.00 x
// 5.75 x 11.00 in object), and that file IS in metres, so fileIsMetric says
// "metres" and is right to. "Metric" and "is this your object" are two
// questions; only the first belongs to lib/manifest.ts. This module answers the
// second, by folder, in isCaptureScale() below — and every sentence here that
// sends a customer to a metric mesh has to name the folder, not just the
// extension, or it sends them to the room.
//
// Claims are deliberately hedged where they should be. A viewer that "probably
// still opens it" is written as probably; an app that needs a plugin says so.
// A confident wrong answer here costs the customer an afternoon.

export type Tier = "free" | "paid";

export interface AppRef {
  name: string;
  tier: Tier;
  /** Platform, plugin requirement, licence caveat — anything that bites. */
  note?: string;
}

export interface FormatInfo {
  ext: string;
  label: string;
  /** What the format actually is, in one sentence. */
  what: string;
  /** Units as this pipeline writes it. Filenames can override; see unitsForFile. */
  units: string;
  apps: AppRef[];
  /** The click path to a dimension, where the format supports being measured. */
  measure?: string;
  /** The thing that goes wrong the first time. */
  trap?: string;
}

/**
 * Display order: the formats a customer measures in first, then simulation,
 * then the reference material.
 */
export const FORMAT_ORDER = [
  ".stl", ".dxf", ".ply", ".glb", ".obj", ".usda", ".urdf", ".xml",
  ".splat", ".json", ".png", ".mtl", ".txt",
];

export const FORMATS: Record<string, FormatInfo> = {
  ".stl": {
    ext: ".stl",
    label: "STL",
    what:
      "Triangle mesh, no colour, no units field. The universal CAD import " +
      "format — every STL this pipeline writes puts its vertices in " +
      "millimetres, so millimeter is always the right import setting. That " +
      "says what the numbers in the file are, not where they came from: a " +
      "generated mesh exported to millimetres is still generated. The unit " +
      "chip against each filename is what says whether a given STL was ever " +
      "measured.",
    units:
      "millimetres — but a millimetre file is not automatically a measured " +
      "one: asset_trellis_mm.stl holds real millimetres over a shape a " +
      "generator invented. The asset page marks those files individually",
    apps: [
      { name: "Onshape", tier: "paid", note: "free plan works, but its documents are public" },
      { name: "Fusion 360", tier: "paid", note: "free personal-use (non-commercial) tier" },
      { name: "SolidWorks", tier: "paid" },
      { name: "FreeCAD", tier: "free", note: "open source, Windows/macOS/Linux" },
      { name: "Blender", tier: "free" },
      { name: "MeshLab", tier: "free" },
      { name: "PrusaSlicer / Bambu Studio / Cura", tier: "free", note: "slicers; each has a measure tool" },
      { name: "Microsoft 3D Viewer", tier: "free", note: "Windows, free from the Microsoft Store" },
    ],
    measure:
      "Onshape: Create ▸ Import the STL with units set to millimeter, insert " +
      "it into a Part Studio as a Mesh, then use the Measure tool — or " +
      "right-click the mesh ▸ Properties for the bounding box. Blender: " +
      "select the object and read N-panel ▸ Item ▸ Dimensions, with " +
      "Scene Properties ▸ Units ▸ Length set to Millimeters.",
    // scale_check_100mm.stl is written by the CAD/splat route only — of the 23
    // assets published, one ships it. The three *_trellis assets whose sole
    // .stl is asset_trellis_mm.stl do not, so the old unconditional "import it
    // once alongside" sent those customers looking for a file nobody uploaded.
    // The old last sentence — "if the cube is not in yours, check the imported
    // size against the dimensions printed on the asset page instead" — produces
    // a FALSE diagnosis on exactly the downloads that lack the cube. On the
    // three *_trellis assets the only .stl is asset_trellis_mm.stl, which the
    // same row chips "none — no real-world scale"; imported correctly at
    // millimetres it reads 7.31 x 7.27 x 14.00 in against a printed
    // 13.25 x 10.75 x 14.00 (76B73843), so the customer "confirms" broken import
    // units that were never broken. A row must not hand out a units check for a
    // file it calls unmeasurable two columns away.
    trap:
      "STL carries no unit declaration at all — the importer guesses. Where " +
      "scale_check_100mm.stl is in the download, import it once alongside and " +
      "measure it: if it is not 100 mm, the import units are wrong and every " +
      "other number is wrong too. Only the CAD route writes that cube, and " +
      "without it there is no self-check inside the download. Do not " +
      "substitute the dimensions printed on the asset page for one: those were " +
      "measured off a different file, so a mismatch cannot tell you whether " +
      "your import units are wrong or whether this STL never held a " +
      "measurement in the first place. Read the unit chip beside the filename " +
      "first — a chip reading “none” settles it — and otherwise set the " +
      "importer to millimeters, which is correct for every STL this pipeline " +
      "writes.",
  },

  ".dxf": {
    ext: ".dxf",
    label: "DXF",
    what:
      "2D section profiles cut through the mesh — plan (horizontal), " +
      "elevation and profile (vertical). Curves, not a mesh, which is what " +
      "makes them snappable and dimensionable in a CAD sketch.",
    units: "millimetres",
    apps: [
      { name: "Onshape", tier: "paid", note: "insert into a sketch" },
      { name: "Fusion 360", tier: "paid", note: "Insert ▸ Insert DXF into a sketch" },
      { name: "AutoCAD", tier: "paid" },
      { name: "LibreCAD", tier: "free", note: "open source, 2D only — the lightest option" },
      { name: "QCAD", tier: "free", note: "community edition is free; the Pro add-ons are paid" },
      { name: "FreeCAD", tier: "free", note: "Draft workbench" },
      { name: "Inkscape", tier: "free", note: "DXF import, for tracing rather than dimensioning" },
    ],
    measure:
      "Onshape: create a sketch on the plane matching the section — profile " +
      "▸ Right, elevation ▸ Front, plan ▸ Top — then Insert DXF into " +
      "that sketch and dimension the curve directly. The coordinates are " +
      "model coordinates, so on a profile the Y ordinate is height above the " +
      "floor. LibreCAD or QCAD: the aligned-dimension tool on any two picked " +
      "points.",
    trap:
      "These DXFs are written without an $INSUNITS header, so nothing in the " +
      "file says “millimetres”. Choose millimeters explicitly at import " +
      "or the profile comes in 25.4× wrong. The value in the filename " +
      "(couch_plan_00245.4mm.dxf) is where that slice was cut, in mm.",
  },

  ".ply": {
    ext: ".ply",
    label: "PLY",
    // Was "This is the metric geometry the printed dimensions were measured
    // from" flat out, which on crate_20260730_scooter sat above a "none — no
    // real-world scale" chip: every .ply in that download is asset_trellis.ply.
    //
    // "The unit chip beside each filename tells them apart" was the next
    // version, and it is false on BE53A423: that download holds
    // object_asset/object_mesh.ply (the fuse, 5.86 x 11.02 x 9.02 in) and
    // mesh_preview/object_mesh_m.ply (the on-device sweep, 90 x 68 x 11 in),
    // and BOTH chip "metres", because both genuinely are in metres. The chip
    // separates measured from invented; only the folder separates the object
    // from the room it was standing in.
    what:
      "Binary point cloud or triangle mesh with per-vertex colour. Where a " +
      "capture produced metric geometry this is the file the printed " +
      "dimensions were measured from — object_asset/object_mesh.ply — and so " +
      "the one to check a number against. Not every PLY here is that file, and " +
      "two different things can be wrong with the others. A generator writes " +
      "one, and the unit chip catches that: it reads “none”. A scan can also " +
      "keep the raw on-device sweep it cut the object out of, under " +
      "mesh_preview/, and no chip can catch that one — it is in true metres, " +
      "so it is chipped “metres” exactly like the fuse. Go by the folder as " +
      "well as the name.",
    units:
      "metres — except where the mesh has no real-world scale at all: a raw " +
      "generative mesh (asset_trellis.ply), or any capture shipping " +
      "NO_METRIC_SCALE.txt. The asset page marks those files individually",
    apps: [
      { name: "CloudCompare", tier: "free", note: "open source — the best tool here for measuring a point cloud" },
      { name: "MeshLab", tier: "free", note: "open source" },
      { name: "Blender", tier: "free" },
      { name: "Microsoft 3D Viewer", tier: "free", note: "Windows, view only" },
      { name: "This site's viewer", tier: "free", note: "no install" },
      { name: "Open3D / trimesh (Python)", tier: "free", note: "for scripted measurement" },
      { name: "Geomagic / Cyclone", tier: "paid", note: "if you already own a scan-data suite" },
    ],
    measure:
      "CloudCompare: load the file, select it, and read Box dimensions in the " +
      "Properties panel for the bounding box; for a specific span use the " +
      "point-picking tool and click two points to get the distance. MeshLab: " +
      "the Measuring Tool picks two points, and Filters ▸ Quality Measure " +
      "and Computations ▸ Compute Geometric Measures prints the bounding " +
      "box. Blender: N-panel ▸ Item ▸ Dimensions with Units ▸ Length " +
      "set to Millimeters.",
    // Hedged the same way the .stl trap hedges scale_check_100mm.stl: one
    // published asset ships a mesh_preview/ folder and the other twenty-two do
    // not, and an unconditional warning sends those customers hunting for a
    // file nobody uploaded. The per-asset panel names the file when it is
    // actually there.
    trap:
      "Two PLYs in one download can differ by a whole room. Where a " +
      "mesh_preview/ folder is in the download, the object_mesh_m.ply inside " +
      "it is the raw on-device sweep — the floor and whatever else was in " +
      "frame — written in real metres, and its name differs by two characters " +
      "from the object_mesh.ply the printed dimensions came from. Measure it " +
      "and you get the size of the sweep, feet across, whatever the object " +
      "was. Neither the file nor its unit says so; the folder does, and your " +
      "asset page names it under the format table. Separately, a " +
      "*_cloud_m.ply is vertices only " +
      "— no faces. It will look empty in a viewer that renders surfaces, and " +
      "it is a different thing from the mesh: measuring a cloud gives you the " +
      "observed points, measuring the mesh gives you a reconstructed surface " +
      "fitted over them, and those two numbers differ by inches.",
  },

  ".glb": {
    ext: ".glb",
    label: "GLB (glTF binary)",
    what:
      "A self-contained glTF: geometry, colour and textures in one file. The " +
      "format to hand to anything that renders — game engines, the web, a " +
      "quick look on a laptop.",
    units:
      "metres — except where the mesh has no real-world scale at all: a raw " +
      "generative mesh (asset_trellis.glb), or any capture shipping " +
      "NO_METRIC_SCALE.txt. The asset page marks those files individually",
    apps: [
      { name: "Blender", tier: "free", note: "File ▸ Import ▸ glTF 2.0" },
      { name: "Godot", tier: "free", note: "native glTF import" },
      { name: "Microsoft 3D Viewer", tier: "free", note: "Windows; free Store download, no longer preinstalled" },
      { name: "three.js / <model-viewer>", tier: "free", note: "GLTFLoader; what this site uses" },
      { name: "Khronos glTF Sample Viewer, gltf.report", tier: "free", note: "browser, nothing to install" },
      { name: "Unity", tier: "free", note: "free tier under the revenue threshold; needs glTFast or UnityGLTF — GLB is not imported out of the box" },
      { name: "Unreal Engine", tier: "free", note: "royalty above a revenue threshold; built-in glTF importer" },
      { name: "Maya / 3ds Max / Cinema 4D", tier: "paid", note: "via each vendor's glTF plugin" },
    ],
    measure:
      "Blender: import, select the object, read N-panel ▸ Item ▸ " +
      "Dimensions. Set Scene Properties ▸ Units ▸ Length to Millimeters " +
      "to read the same units as the CAD files.",
    trap:
      "Blender's glTF importer rotates the model on the way in (glTF is Y-up, " +
      "Blender is Z-up), so the axis a dimension appears under changes even " +
      "though the size does not. On macOS, Quick Look and Preview open USDZ " +
      "natively but not GLB — use Blender or a browser viewer instead.",
  },

  ".obj": {
    ext: ".obj",
    label: "OBJ (Wavefront)",
    what:
      "Plain-text mesh. Used here for four different jobs: the millimetre " +
      "CAD copy (*_mm.obj), the visual mesh a simulator renders " +
      "(*_visual.obj), the convex collision hulls it collides with " +
      "(collision_NN.obj), and the generator's own output (asset_trellis.obj " +
      "and its millimetre twin), which was never measured at all.",
    // Not "metres in every other OBJ": asset_trellis.obj is a generative mesh
    // and is neither, yet it is an OBJ like any other. The scale question is
    // answered by the file's provenance before the filename gets a say — and
    // the marker file is only one of the two signals for that, since a bare
    // asset_trellis.obj can ship with no marker beside it.
    //
    // The `_mm` suffix does not rescue it either. asset_trellis_mm.obj is
    // millimetres AND unmeasured at once: the units are real, the size is the
    // generator's, and the asset page chips it "none — no real-world scale"
    // like every other trellis file. Listing it under the mm rule without
    // saying so is how this row came to contradict the chip beneath it.
    units:
      "millimetres in *_mm.obj, metres in every other OBJ here — except " +
      "where the mesh has no real-world scale at all: a generative mesh " +
      "(asset_trellis.obj, and asset_trellis_mm.obj, which is that same " +
      "invented shape written in millimetres), or any capture shipping " +
      "NO_METRIC_SCALE.txt",
    apps: [
      { name: "Blender", tier: "free" },
      { name: "MeshLab", tier: "free" },
      { name: "CloudCompare", tier: "free" },
      { name: "MuJoCo / PyBullet / Isaac Sim", tier: "free", note: "these are the OBJs the sim files reference" },
      { name: "Microsoft 3D Viewer", tier: "free", note: "Windows" },
      { name: "Fusion 360 / SolidWorks", tier: "paid", note: "mesh import; prefer the STL for CAD" },
    ],
    measure:
      "Same as PLY — Blender N-panel ▸ Item ▸ Dimensions, or MeshLab's " +
      "Compute Geometric Measures. Confirm which unit you are in from the " +
      "filename, and whether the file was measured at all from the chip " +
      "beside it, before you write the number down.",
    trap:
      "OBJ has no unit field, and this pipeline ships both mm and metre OBJs " +
      "in the same download. A 1000× error is one wrong file away. Also, " +
      "the vertex colours are the non-standard `v x y z r g b` extension: " +
      "Blender, MeshLab and CloudCompare read them, most CAD tools silently " +
      "drop them.",
  },

  ".usda": {
    ext: ".usda",
    label: "USDA (OpenUSD, ASCII)",
    what:
      "A USD stage in its text flavour — readable in any editor. Authored " +
      "Z-up at 1 metre per unit with a RigidBody, a MassAPI mass and centre " +
      "of mass, convex-hull colliders and a friction material, so it drops " +
      "into a physics scene rather than just a render.",
    units: "metres (metersPerUnit 1.0), Z-up",
    apps: [
      { name: "NVIDIA Isaac Sim / Omniverse", tier: "free", note: "free to download; this is the target the physics schemas were written for" },
      { name: "Blender 3.5+", tier: "free", note: "USD import; reads the geometry, ignores the physics — colour only when the export baked vertex colours, which the older ones here did not" },
      { name: "usdview / usdcat", tier: "free", note: "ships with an OpenUSD build" },
      { name: "Any text editor", tier: "free", note: ".usda is ASCII — you can read the mass and the units yourself" },
      { name: "Houdini", tier: "paid", note: "Solaris; Apprentice is free for non-commercial use" },
      { name: "Maya / 3ds Max", tier: "paid", note: "via the Autodesk USD plugins" },
    ],
    measure:
      "Isaac Sim / Omniverse: the Measure tool in the viewport. Or open the " +
      "file in a text editor and read `metersPerUnit`, the `physics:mass` " +
      "attribute and the mesh extents straight out of the text.",
    // Checked in Blender 5.2 headless on scanned_crate_object.usda (1 visual
    // mesh + 24 hulls): the default import yields exactly 1 mesh object,
    // import_guide=True yields 25. The hulls are authored with
    // `purpose = guide` (sim_export.py) and Blender's Guide toggle is off by
    // default. The panel used to promise the mess and tell people to clean it
    // up, which taught them to distrust a correct import.
    trap:
      "What arrives in Blender is one mesh, not the mess you might expect — " +
      "the collision hulls are authored as USD guide geometry and Blender's " +
      "importer leaves Guide switched off, so they are skipped. Turn Guide on " +
      "in the import options if you actually want to see them. Colour is the " +
      "part that can go missing: the visual mesh carries vertex colour only " +
      "if the export baked it, and the older exports here did not — those " +
      "arrive flat grey, with colour on the collider prims and none on the " +
      "mesh you can see. When that happens, look at the *_visual.glb or " +
      "*_visual.obj beside it: sometimes the colour survived there, and " +
      "sometimes the source mesh never had any to begin with.",
  },

  ".urdf": {
    ext: ".urdf",
    label: "URDF",
    what:
      "The robotics description format — here a single-link rigid prop with " +
      "mass, centre of mass and an inertia tensor, a visual mesh and the " +
      "convex collision hulls.",
    units: "metres and kilograms (URDF is SI by definition)",
    apps: [
      { name: "PyBullet", tier: "free", note: "p.loadURDF — the quickest way to see it fall over" },
      { name: "ROS 2 / RViz2", tier: "free" },
      { name: "Gazebo", tier: "free" },
      { name: "Isaac Sim", tier: "free", note: "URDF importer extension" },
      { name: "Drake, Webots", tier: "free" },
      { name: "MuJoCo", tier: "free", note: "MuJoCo's compiler can load URDF directly, with limitations — but the .xml MJCF beside it is the native, fully-featured file" },
    ],
    measure:
      "URDF is a description, not geometry — measure the meshes it points at " +
      "(the OBJ files, when they are in the download), or read the " +
      "`<mass value=…>` and `<inertia …>` values straight out of the XML.",
    trap:
      "The mesh filenames are written as plain relative names, not " +
      "`package://…` URIs. Loaders that resolve relative paths (PyBullet) " +
      "are fine as long as the OBJ files sit in the same folder; a ROS setup " +
      "that expects `package://` will need the paths rewritten. Download the " +
      "whole folder, not just the URDF — and check the mesh is in it. A URDF " +
      "on its own carries a mass and an inertia tensor and no shape at all.",
  },

  ".xml": {
    ext: ".xml",
    label: "XML (MuJoCo MJCF)",
    what:
      "Every .xml this pipeline ships is a MuJoCo model — you can confirm it " +
      "by the `<mujoco>` root element. Free joint, principal inertia, visual " +
      "geometry in group 2 and collision hulls in group 3.",
    units: "metres and kilograms",
    apps: [
      { name: "MuJoCo", tier: "free", note: "Apache-2.0; drag the file onto the `simulate` binary, or run `python -m mujoco.viewer --mjcf=<file>`" },
      { name: "dm_control", tier: "free" },
      { name: "Gymnasium / robosuite / MuJoCo Playground", tier: "free", note: "MuJoCo-based RL stacks" },
      { name: "MuJoCo Warp / Newton", tier: "free", note: "the GPU-accelerated successors" },
    ],
    measure:
      "Not a measuring format. In the MuJoCo viewer you can read contact and " +
      "body frames, but for a dimension use the PLY or the STL.",
    trap:
      "The MJCF declares `meshdir=\".\"`, so it only compiles with the " +
      "collision_NN.obj and *_visual.obj files sitting beside it — if they " +
      "are not in the download, the compile fails and no setting fixes it. " +
      "`mujoco-py` is deprecated — use the official `mujoco` Python bindings.",
  },

  ".splat": {
    ext: ".splat",
    label: "SPLAT (antimatter15 gaussian splat)",
    what:
      "A gaussian splat: 32 bytes per splat — position, scale, colour and a " +
      "rotation quaternion. It renders, it does not have a surface. " +
      "scene_gaussians.splat is the trained radiance field; object.splat is " +
      "the fused point cloud written out as tiny isotropic gaussians so any " +
      "splat viewer will show it.",
    units: "metres",
    apps: [
      { name: "This site's viewer", tier: "free", note: "no install" },
      { name: "SuperSplat", tier: "free", note: "open source, runs in the browser (PlayCanvas)" },
      { name: "antimatter15 splat viewer", tier: "free", note: "the original web viewer this format comes from" },
      { name: "PlayCanvas Engine", tier: "free" },
      { name: "Babylon.js", tier: "free", note: "gaussian-splat support since v7" },
    ],
    // "or the millimetre STL" used to be unqualified, and on 76B73843 the only
    // millimetre STL in the download is asset_trellis_mm.stl — the file this
    // same page chips as having no real-world scale. "*_m.ply in metres" was
    // unqualified for the same reason and had the same fault: on BE53A423 the
    // only *_m.ply is mesh_preview/object_mesh_m.ply, the room sweep.
    measure:
      "Do not measure a splat. There is no surface to pick against — the " +
      "gaussians sit in a noisy shell a few centimetres thick. Take any " +
      "number you intend to quote off the metric mesh instead: " +
      "object_asset/object_mesh.ply, or a *_m.ply or *_mm.stl from the CAD " +
      "route — but not asset_trellis_mm.*, which the asset page chips as " +
      "having no real-world scale, and not mesh_preview/object_mesh_m.ply, " +
      "which is in honest metres and is the room rather than the object.",
    trap:
      "Nerfstudio is not a .splat importer — its gsplat pipeline reads and " +
      "writes PLY. If a tool asks for a gaussian PLY rather than a .splat, " +
      "that is the file it wants.",
  },

  ".json": {
    ext: ".json",
    label: "JSON",
    what:
      "The numbers and the provenance. dims.json and dims_mm.json hold the " +
      "measured bounding box, cad_report.json the CAD route's dimensions and " +
      "frame, sim_export.json the mass and inertia, asset.json and " +
      "route_decision.json which frames and which pipeline produced the " +
      "result.",
    units: "stated inside each file (`units_in`, `units`)",
    apps: [
      { name: "Any text editor / VS Code", tier: "free" },
      { name: "A web browser", tier: "free", note: "Chrome and Firefox both pretty-print JSON" },
      { name: "jq", tier: "free", note: "command line" },
    ],
    // Checked against the three dims_mm.json on this box: every one's `inches`
    // block equals its sibling dims.json `aabb.inches_0_25` exactly, never
    // `raw_inches` (1B38880A: 7.75/10.5/9.5 vs raw 7.8694/10.6234/9.4973). The
    // file says so itself — "source": "rgbd_dims_inches" — and
    // trellis_metric.aabb_inches_from_dims reads `inches_0_25` first, then
    // multiplies by 25.4. The panel used to call summary_mm unrounded, which
    // turned a quarter-inch rounding into a millimetre reading.
    measure:
      "This is where the printed dimensions come from — but only one of these " +
      "three files carries an unrounded number, so which field you read " +
      "matters. dims.json: read `aabb.summary_raw` (or `aabb.raw_inches`) " +
      "under the entity named by `primary`, usually `object` — that is the " +
      "measurement. `aabb.summary` and `aabb.inches_0_25` are the same number " +
      "rounded to the nearest quarter inch, and so are the top-level " +
      "`rawLengthInches` / `rawWidthInches` / `rawHeightInches` despite the " +
      "name. cad_report.json: `dims.summary_in` and `dims.bbox_mm` are " +
      "genuinely unrounded. dims_mm.json: `summary_mm`, `summary_inches`, " +
      "`mm` and `inches` are NOT — that whole file is computed from " +
      "dims.json's quarter-inch values (it declares `\"source\": " +
      "\"rgbd_dims_inches\"`), so a summary_mm of 196.8 mm is 7.75 in × 25.4, " +
      "not a measured millimetre. There is no unrounded figure anywhere in " +
      "dims_mm.json; take it from dims.json's `raw_inches` instead. Where a " +
      "download holds more than one dims.json, check which one you opened " +
      "first: the object's has a `primary` key, and one that opens instead " +
      "with `\"source\": \"cratescanner\"` and `\"measurement\": null` is the " +
      "bounding box of the raw on-device sweep rather than of your object — " +
      "its numbers run to feet.",
    // "fitted per axis" was true of one published pair and no more. Measured
    // with trimesh, then read in the frame the page uses. A trellis export is
    // y-up (Viewer.tsx guessUp) and AXIS_READOUT maps y-up to
    // length = Z, width = X, height = Y, matching measure.py's "ARKit: x=width,
    // y=height, z=length". Comparing raw x,y,z against a printed L x W x H is
    // the cross-axis mistake this comment used to make.
    //   AB12CD34  273.1 x 355.6 x 336.6 mm (x,y,z) -> L 13.25 / W 10.75 /
    //             H 14.00 in against a printed 13.25 x 10.75 x 14.00: all three
    //             axes, the only genuinely per-axis pair published.
    //   76B73843  184.7 x 355.6 x 185.6 mm -> L 7.31 / W 7.27 / H 14.00 in
    //             against a printed 13.25 x 10.75 x 14.00: the longest edge is
    //             the height and it carries the whole fit. L is 5.94 in short,
    //             W 3.48 in short.
    //   1B38880A  157.7 x 266.7 x 78.9 mm -> L 3.11 / W 6.21 / H 10.50 in
    //             against a printed 7.75 x 10.50 x 9.50: the mesh's longest
    //             edge is its height and it was scaled to the printed WIDTH, so
    //             not one axis matches its own — L 4.64 in short, W 4.29 in
    //             short, H 1.00 in OVER.
    // Worst single-axis miss across the three: 5.94 in. The 7.39 in this block
    // used to quote was printed width (10.50) minus mesh length (3.11) — two
    // different axes — and no published file supports it.
    // The per-axis block in assetpipe/scene/trellis_metric.py:145-165 is an
    // uncommitted working-tree change; the committed code does
    // mesh_mm.apply_scale(1000.0) after a uniform longest-edge fit, and that
    // is the code that wrote two of the three files a customer can download
    // today. Copy describes what shipped, not what the next run would do.
    trap:
      "`inches_0_25` is a rounding convention, not an accuracy claim, and it " +
      "propagates: dims_mm.json is derived from it, and in a *_trellis folder " +
      "so is whatever size asset_trellis_mm.stl and asset_trellis_mm.obj were " +
      "fitted to — on some of those exports only the single longest edge was " +
      "matched, leaving the other two axes at the generator's proportions. A " +
      "number that ends in .00, .25, .50 or .75 in — or in its exact mm twin " +
      "— has been through that step. See the precision note: the real " +
      "tolerance is roughly ±1–2 in either way, which is several times the " +
      "rounding.",
  },

  ".png": {
    ext: ".png",
    label: "PNG",
    what:
      "Renders and textures: preview images, the shaded elevations you read " +
      "a crop box off, contact sheets comparing the reconstruction against " +
      "the captured photos, and material_0.png — the texture atlas belonging " +
      "to a generated mesh.",
    units: "pixels",
    apps: [
      { name: "Any image viewer", tier: "free", note: "Preview, Photos, a browser" },
      { name: "GIMP / Krita", tier: "free" },
      { name: "Photoshop", tier: "paid" },
    ],
    trap:
      "material_0.png is not a picture of the object — it is the UV texture " +
      "atlas, and it only means anything loaded with the mesh and .mtl beside it.",
  },

  ".mtl": {
    ext: ".mtl",
    label: "MTL",
    what:
      "The material sidecar for a Wavefront OBJ: which texture image goes " +
      "with which surface. Never opened on its own.",
    units: "n/a",
    apps: [
      { name: "Blender / MeshLab", tier: "free", note: "picked up automatically when it sits beside its .obj" },
      { name: "Any text editor", tier: "free" },
    ],
    trap:
      "Download the .obj, the .mtl and the .png together or the mesh arrives " +
      "untextured, usually flat grey.",
  },

  ".txt": {
    ext: ".txt",
    label: "TXT",
    what:
      "A marker file. NO_METRIC_SCALE.txt means the capture had no depth " +
      "data, so the mesh beside it is normalized, not metric.",
    units: "n/a",
    apps: [{ name: "Any text editor", tier: "free" }],
    trap:
      "If NO_METRIC_SCALE.txt is in the download, do not quote a size off " +
      "that mesh — it has no real-world scale until it is metricized from a " +
      "marker or from an RGB-D scan. Its absence proves nothing, though: a " +
      "generative mesh can ship without the marker and still have no size. " +
      "Trust the unit shown against each file on the asset page, not the " +
      "presence of this file.",
  },
};

/** Lowercased extension including the dot, or "" if the name has none. */
export function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
}

export function formatFor(name: string): FormatInfo | undefined {
  return FORMATS[extOf(name)];
}

/**
 * What a unit chip says when the geometry carries no real-world scale.
 *
 * Deliberately names no marker file. The non-metric case is not always an
 * RGB-only capture that shipped NO_METRIC_SCALE.txt: mouse_ewa_regen publishes a
 * bare asset_trellis.glb with no marker at all, and pointing that customer at a
 * file which is not in their download is a dead end. The reason belongs in the
 * note under the table, not in a chip four words wide.
 */
export const NON_METRIC_UNITS = "none — no real-world scale";

/**
 * Extensions whose contents are geometry, i.e. the files a scan's scale
 * applies to. .json/.png/.mtl/.txt are unaffected by it.
 */
const GEOMETRY_EXTS = new Set([
  ".stl", ".dxf", ".ply", ".glb", ".obj", ".usda", ".splat",
]);

/**
 * Units for a specific file, not just its extension.
 *
 * Two separate things decide this and neither is inside the file: the filename
 * (OBJ ships in both mm and metres here) and whether that geometry has any
 * real-world scale at all. This module answers the first and only the first.
 *
 * `metric` MUST come from lib/manifest.ts's fileIsMetric(asset, file) — the one
 * definition on the page. This module used to carry a second one that tested
 * only the NO_METRIC_SCALE.txt marker, which returned true for a bare
 * asset_trellis.glb and put a "metres" chip on a unit-cube TRELLIS mesh
 * (mouse_ewa_regen: 0.93 x 0.41 x 0.79 model units, which Blender then reads as
 * 36.61 x 15.96 x 31.21 in) directly beside the viewer's "no metric scale".
 * Two answers on one screen is worse than either answer alone.
 *
 * The version before that was worse still: a hardcoded list of TRELLIS
 * filenames tested inside the .glb/.ply/.splat branch, which .obj returned
 * before ever reaching — so one mesh read "not metric" as .ply and "metres" as
 * .obj. Provenance decides this, and provenance is not knowable from a name
 * alone, which is why it is an argument and not a lookup.
 */
export function unitsForFile(name: string, metric = true): string {
  const n = name.toLowerCase();
  const ext = extOf(n);

  // Outranks every filename rule below: with no scale, the numbers in the file
  // are not millimetres or metres either, they are nothing.
  if (!metric && GEOMETRY_EXTS.has(ext)) return NON_METRIC_UNITS;

  if (ext === ".obj") return /_mm\.obj$/.test(n) ? "millimetres" : "metres";
  if (ext === ".stl") return "millimetres";
  if (ext === ".dxf") return "millimetres";
  if (ext === ".glb" || ext === ".ply" || ext === ".splat") return "metres";

  return FORMATS[ext]?.units ?? "—";
}

export function appsByTier(f: FormatInfo, tier: Tier): AppRef[] {
  return f.apps.filter((a) => a.tier === tier);
}

/** The manifest fields this module needs to reason about a download. */
export interface FileRef {
  name: string;
  /** Path relative to the group folder — the folder a mesh ref resolves in. */
  rel: string;
  /** Capture folder the file came from; `rel` repeats across groups. */
  group: string;
}

/** Description formats that carry no geometry of their own. */
const MESH_REFERENCING_EXTS = new Set([".urdf", ".xml"]);

/** What a `<mesh filename=…>` can point at. */
const REFERENCED_MESH_EXTS = new Set([".obj", ".stl", ".dae", ".ply", ".msh"]);

/**
 * The URDF/MJCF files in this download that ship with no mesh beside them.
 *
 * URDF and MJCF are descriptions: the shape lives in a separate OBJ they name
 * by relative path (MJCF via `meshdir="."`, URDF via `<mesh filename=…>`), so
 * both resolve against the file's own folder and neither loads without it.
 * Not every published asset has those meshes — box_demo is six model.urdf
 * files and nothing else, because model.obj matches no pattern in
 * publish_dashboard's DELIVERABLES list and is never uploaded. Telling that
 * customer to "measure the meshes it points at" is advice they cannot follow,
 * so the panel says what is actually true of their download instead.
 *
 * .usda is deliberately not in this check: it is self-contained. _write_usd
 * authors every mesh inline with CreatePointsAttr, and a generated .usda has
 * no `references`, `payload`, `subLayers` or `.obj` anywhere in it.
 */
export function simFilesMissingMeshes<T extends FileRef>(files: T[]): T[] {
  const folder = (f: FileRef) => {
    const slash = f.rel.lastIndexOf("/");
    return `${f.group}/${slash < 0 ? "" : f.rel.slice(0, slash)}`;
  };
  const withMesh = new Set(
    files.filter((f) => REFERENCED_MESH_EXTS.has(extOf(f.name))).map(folder),
  );
  return files.filter(
    (f) => MESH_REFERENCING_EXTS.has(extOf(f.name)) && !withMesh.has(folder(f)),
  );
}

/**
 * Geometry that is the CAPTURE, not the object cut out of it.
 *
 * The third state, and the only one a unit chip cannot express. A scan session
 * is published beside the raw on-device ARKit sweep it came from:
 * mesh_preview/object_mesh_m.ply. Measured with trimesh on the one published
 * example it is 1.720 x 0.288 x 2.289 m — 90 x 68 x 11 in of floor and
 * surroundings — against a 9.00 x 5.75 x 11.00 in object, and the download's own
 * CrateScan-*_cratescan/dims.json agrees ("source": "cratescanner",
 * "measurement": null, 90.00 x 67.75 x 11.25 in). Both PLYs in that download are
 * named object_mesh*.ply and both chip "metres", so the folder is the only thing
 * that separates them.
 *
 * This is NOT a second answer to "is it metric". lib/manifest.ts owns that
 * question and answers "metres" for these files, correctly — they are in
 * metres. This answers the other question, "is it your object", and callers must
 * keep the two apart: a capture-scale file is measurable and still the wrong
 * thing to measure.
 *
 * Viewer.tsx's NEVER_PICK holds the same fact for a different job (which file
 * the tape measure auto-loads) and lists mesh_preview/ for exactly this reason;
 * the two must not drift. Its other entries are deliberately absent here:
 * collision_NN.obj and *_cloud_m.ply ARE the object in another form, and
 * scale_check_100mm.stl is described as a calibration cube everywhere it
 * appears. If a genuinely capture-scale folder joins that list, add it here too.
 */
const CAPTURE_SCALE_PATH = /(^|\/)mesh_preview\//i;

export function isCaptureScale(file: { rel: string }): boolean {
  return CAPTURE_SCALE_PATH.test(file.rel);
}

/**
 * A SECOND reconstruction of the same capture, shipped for comparison.
 *
 * The same third state as isCaptureScale — genuinely metric, genuinely not the
 * thing the printed size was measured from — but arrived at from the other
 * direction, and by NAME rather than by folder because these sit in the
 * capture root beside the real deliverables.
 *
 * This is not hypothetical hedging. Measured against each asset's own
 * object_asset/dims.json on 2026-08-07:
 *
 *   koala      kiri_visual.glb   4.24 x 4.11 x 0.83 in   vs   6.39 x 6.01 x 4.08 in
 *   robot_arm  kiri_visual.glb  10.54 x 4.85 x 4.60 in   vs  42.10 x 34.97 x 25.16 in
 *
 * Both are partial-coverage fragments, 4-6x short on every axis, and both are in
 * real metres — so lib/manifest.ts's fileIsMetric() says "metres" and is RIGHT
 * to. Widening isGenerative() to catch them would be a provenance lie: they are
 * photogrammetry, not generative. Dropping a NO_METRIC_SCALE.txt beside them
 * would be worse, because that marker is asset-level in both consumers and
 * would erase the koala's one genuine measurement.
 *
 * "Is it in metres" and "is it your object, completely" are different questions.
 * manifest.ts owns the first; this owns the second.
 *
 * meshroom_visual.* matches too, and errs the other way — it reconstructs the
 * surroundings as well, so it reads LARGER than the object. Either direction is
 * a number a customer must not quote.
 *
 * nvblox_visual.* is the interesting case: on the sofa it is the BEST geometry on
 * the page (74.9 x 34.4 x 32.5 against a ~79 x 35 x 34 truth, worst axis -4.1 in,
 * integrating 356 of 356 frames where the in-house ICP fuse merged 2-4). It is
 * still flagged, because its depth axis moves 5 in (29.4 -> 34.4) on a 5.8 deg
 * yaw choice and its 7.5 mm voxels are already 0.30 in. "Better than everything
 * else here" and "safe to quote" are different claims; this guard is about the
 * second one, and nvblox has not earned it until it drives a published dimension.
 */
/*
 * Matched by the CONVENTION, not by a list of pipeline names.
 *
 * This was `(kiri|kiri_oss|meshroom)_visual\.` and had to be edited every time a
 * route was added — so `nvblox_visual.glb` and `instantngp_visual.glb` shipped
 * with no caveat at all, and instant-ngp is RGB-only and reads +14.8 in over
 * truth on the sofa. A publisher adding a route must not have to remember to
 * come here.
 *
 * `<pipeline>_visual.<ext>` is the publisher's own name for "another
 * reconstruction of this capture". Verified across all nine live assets: every
 * *_visual.* file is one of these (kiri, kiri_oss, meshroom, nvblox, instantngp)
 * and NO asset ever declares one as its measure source — that is always
 * object_mesh.ply. So the generic rule cannot swallow the measured file.
 *
 * It also fails SAFE: an unrecognised future route gets "do not quote a size off
 * it" until someone deliberately promotes it, which is the right default for a
 * reconstruction nobody has measured yet.
 */
const ALT_RECON_NAME = /(^|\/)[a-z0-9]+(?:_[a-z0-9]+)*_visual\.(glb|gltf|obj|ply)$/i;

export function isAltReconstruction(file: { rel: string; name: string }): boolean {
  return ALT_RECON_NAME.test(file.rel) || ALT_RECON_NAME.test(file.name);
}

/** One sentence, printed wherever an alternate reconstruction is listed. */
// Deliberately states no DIRECTION of error. "It does not cover the whole
// object" was written for the KIRI fragments, which run 4-6x short, and it was
// already wrong for meshroom_visual (which reconstructs the surroundings too and
// reads LARGER). It is wronger now that nvblox_visual matches this rule: nvblox
// integrates every frame and is the closest geometry on the sofa. What is true
// of all of them is that they disagree with the measured file, which is the only
// thing a customer needs in order not to quote one.
export const ALT_RECON_NOTE =
  "A second reconstruction of the same capture, kept for comparison. It is in " +
  "metres, but it is not the file the printed size was measured from and its " +
  "dimensions disagree with it — do not quote a dimension off it.";

/**
 * The honesty note. Repeated verbatim anywhere a dimension is shown, because
 * the quarter-inch figure in dims.json reads like precision and is not.
 */
export const PRECISION_NOTE =
  "Real tolerance on these reconstructions is about ±1–2 inches. " +
  "Surface RMS is around 19 mm, and the same capture reconstructed with " +
  "different settings moves 3.6–7.4 in. The 0.25 in figure in dims.json " +
  "(`inches_0_25`) is a rounding convention, not an accuracy claim. Do not " +
  "quote quarter-inch precision off these files.";

/** "I want to X" → the file to download. */
export const DECISION_GUIDE: { want: string; use: string; why: string }[] = [
  // "*_mm.stl" on its own resolved to asset_trellis_mm.stl for all three
  // *_trellis assets — the only millimetre STL any of them ships, and the file
  // the asset page calls a fabrication. A guide row cannot send a customer to
  // a file the rest of the site tells them not to quote.
  {
    want: "Measure it in CAD",
    use: "*_mm.stl — but not asset_trellis_mm.*",
    why:
      "A millimetre STL off a scan route (couch_mm.stl and the like) imports " +
      "as a mesh into Onshape, Fusion, SolidWorks or FreeCAD at the size it " +
      "was measured. Where scale_check_100mm.stl is in the download, import " +
      "that 100 mm cube once first — it proves the import units before you " +
      "trust anything else; only the CAD route ships one. asset_trellis_mm.stl " +
      "and asset_trellis_mm.obj are the exception: real millimetres over a " +
      "shape a generator invented, sized to the scan's bounding box, and on " +
      "some exports only along the single longest edge. The asset page chips " +
      "them “none — no real-world scale”. If one of those is the only " +
      "*_mm.stl in your download, it holds no CAD-ready measured mesh — " +
      "measure object_asset/object_mesh.ply in metres instead.",
  },
  {
    want: "Dimension a specific curve or profile",
    use: "sections/*.dxf",
    why:
      "Drops into a CAD sketch where every curve is snappable. On a profile " +
      "section the Y ordinate is height above the floor, so heights measure " +
      "from a datum rather than a picked mesh vertex.",
  },
  // "*_m.ply" on its own resolved, on BE53A423, to
  // mesh_preview/object_mesh_m.ply — the raw on-device sweep, 90 x 68 x 11 in
  // against a printed 9.00 x 5.75 x 11.00. It is in true metres, so its unit
  // chip reads "metres" like the fuse's and nothing on the page contradicted
  // the guide. A glob is not a file: name the folder.
  {
    want: "Check a number without CAD",
    use: "object_asset/object_mesh.ply, in CloudCompare or MeshLab",
    why:
      "Both are free, both read the metric mesh directly, and the bounding " +
      "box appears in the properties panel with no import-units question. " +
      "Pick the file by its folder, not its name. The fuse the printed " +
      "dimensions were measured from is object_mesh.ply under object_asset/. " +
      "A *_m.ply from the CAD route (couch_m.ply and the like) is the same " +
      "geometry in metres and is equally good — but " +
      "mesh_preview/object_mesh_m.ply is not: two characters different, and it " +
      "is the raw on-device sweep of the floor and the room, feet across " +
      "rather than inches. It is in honest metres, so no unit chip warns you " +
      "off it; the viewer on your asset page refuses to load it for that " +
      "reason, and your asset page names it under the format table when your " +
      "download has one.",
  },
  {
    want: "Just look at it",
    use: "*.glb, or the .splat in the browser viewer",
    why:
      "GLB opens in Blender, Godot, a game engine or a browser. The splat is " +
      "the photoreal one — but it has no surface, so it is for looking, not " +
      "for measuring.",
  },
  {
    want: "Render it",
    use: "*.glb for a mesh, *.splat for photoreal",
    why:
      "The mesh is Poisson-reconstructed over splat centres and reads as " +
      "smoothed upholstery up close. If appearance matters more than " +
      "geometry, render the splat and keep the mesh for physics.",
  },
  {
    want: "Simulate it",
    use: "*.usda for Isaac Sim, *.xml for MuJoCo, *.urdf for ROS or PyBullet",
    why:
      "All three carry the same mass, centre of mass, inertia tensor and " +
      "convex collision hulls. The .xml and the .urdf reference the " +
      "collision_NN.obj and *_visual.obj meshes by relative path, so take the " +
      "whole folder for those two. The .usda is self-contained — every mesh " +
      "is written into the file itself, so it opens on its own.",
  },
  {
    want: "Read the numbers the site prints",
    use: "dims.json, dims_mm.json, cad_report.json",
    why:
      "The printed size comes out of these, under a different name in each — " +
      "and the rounding is not confined to one file. dims.json: read " +
      "`aabb.summary_raw`, not `aabb.summary`, `aabb.inches_0_25` or the " +
      "top-level `rawLengthInches` — those three are all quantized to the " +
      "nearest quarter inch. cad_report.json: `dims.summary_in`, unrounded. " +
      "dims_mm.json: `summary_mm` and `summary_inches` are computed from " +
      "dims.json's quarter-inch values, so 196.8 mm is 7.75 in converted, not " +
      "a measured millimetre — that file holds no unrounded number at all. " +
      "Check which dims.json you opened: a download can hold more than one, " +
      "and the one whose top level reads `\"source\": \"cratescanner\"` with " +
      "`\"measurement\": null` is the bounding box of the raw on-device sweep, " +
      "not of your object. The object's has a `primary` key.",
  },
  {
    want: "Know how much it weighs",
    use: "sim_export.json",
    why:
      "Mass is a density prior over the mesh volume, not a measurement — " +
      "vision cannot weigh anything. The file records which prior was used. " +
      "Treat it as ±2× until you put the object on a scale.",
  },
];
