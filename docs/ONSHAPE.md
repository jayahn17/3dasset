# Scan → Onshape (and other CAD)

Two routes reach CAD, chosen by `route_decision.json` (see `assetpipe/scene/route.py`):

| Capture | Route | Geometry source | Tool |
|---------|-------|-----------------|------|
| Product, ≤24″ longest edge | TRELLIS | generated textured mesh | already exported |
| Furniture / scene, >24″ | **3DGUT splat** | trained gaussians | `tools/splat_to_cad.py` |

Neither produces parametric BREP. Both import as a **Mesh**, which you measure
directly or trace into real CAD from the DXF sections.

---

## A. Splat route — `tools/splat_to_cad.py`

A trained splat is a cloud of ellipsoids; Onshape cannot import or measure it.
This converts one into a levelled, metric, millimetre mesh plus DXF section
profiles.

```bash
conda activate assetpipe

# 1. See the scene and pick the object's crop box
python tools/splat_to_cad.py <scene_gaussians.ply> --out cad_out/<name> --name <name>
#    → open cad_out/<name>/views_levelled.png, read the box off the metre grid

# 2. Re-run with the crop
python tools/splat_to_cad.py <scene_gaussians.ply> --out cad_out/<name> --name <name> \
    --crop=x0,y0,z0,x1,y1,z1
```

`--crop` must use the `=` form — argparse reads a leading `-4.75` as a flag.

The crop is a real step, not a workaround: clustering cannot separate a couch
from the shopping bag leaning against it, so one human decision beats a
heuristic that silently measures the wrong object.

### Output

| File | Units | Use |
|------|-------|-----|
| `<name>_mm.stl` | **mm** | Onshape / Fusion / SolidWorks import |
| `<name>_lite_mm.stl` | mm | same part, ~25k faces — responsive picking |
| `<name>_mm.obj` | mm | vertex colours kept |
| `<name>.glb` | m | web / Blender viewing |
| `sections/*.dxf` | **mm** | import into an Onshape **sketch** and dimension |
| `scale_check_100mm.stl` | mm | a 100 mm cube — confirms units survived import |
| `views_levelled.png` | m | the drawing you read `--crop` off |
| `views_part.png` | mm | shaded elevations of the finished part |
| `cad_report.json` | — | dims, provenance, every pipeline count |

### Frame

Export lands on **X = length, Y = depth, Z = height**, floor on `z = 0`, part
against the origin corner. So in Onshape the Top plane is the floor and every
height dimension measures from a datum instead of a picked mesh vertex.

### Import and measure

1. Onshape → **Create → Import** → `<name>_mm.stl`, units **millimeter**.
2. Insert into a Part Studio as a **Mesh**.
3. Import `scale_check_100mm.stl` alongside once; measure it. If it is not
   100 mm, the import units are wrong — fix that before trusting anything.
4. Bounding box: right-click the mesh → **Properties**.
5. **Detail measurement — use the sections.** Create a sketch on the plane
   matching the section (`profile` → Right, `elevation` → Front, `plan` →
   Top), then **Insert DXF** into that sketch. DXF coordinates are model
   coordinates, so a `profile` curve's Y ordinate *is* height above the floor.
   Dimension the curve, or trace it with splines to rebuild the shape as
   parametric CAD.

### Accuracy

Scale is inherited, not assumed: 3DGUT trains on ARKit metric poses, so the
gaussians are already in metres. Cross-check against `dims.json` from the
independent RGB-D fuse before quoting anything.

What limits it: a splat's centers form a noisy shell a few cm thick, so the
surface is smoothed upholstery, not a crisp edge. The floor fit reports
`fit_rms_mm` — treat that as the noise floor for height measurements.
Unobserved regions are Poisson-invented and trimmed by density; anything the
camera never saw is absent, not wrong. Measure captured surfaces only, and
check `views_part.png` before trusting a dimension.

Defaults are tuned for upholstered furniture. Rigid, sharp-edged parts want a
smaller `--voxel` and a deeper `--poisson-depth`.

---

## B. TRELLIS route (product scale)

| File | Units | Use |
|------|-------|-----|
| `asset_trellis_mm.stl` | **millimeters** | Onshape / Fusion / SolidWorks / FreeCAD |
| `asset_trellis.glb` | meters (scaled) | Web viewer / Blender |
| `dims_mm.json` | mm | Authoritative L×W×H (+ crate) from RGB-D |
| `dims.json` | inches | Same source as object fuse |

1. Onshape → **Create** → **Import** → `asset_trellis_mm.stl`
2. Insert as **Mesh** into a Part Studio
3. **Measure** edges / bounding box — should match `dims_mm.json` (± tessellation)

Do **not** measure the raw unscaled generator mesh for quoting.

## Blender

1. File → Import → glTF 2.0 → `.glb` (meters), or STL (mm — set scene unit to mm)
2. Enable MeasureIt or use the Measure tool
3. Compare longest edges to `dims_mm.json`

## Limits

- Mesh is not editable BREP / FeatureScript CAD.
- Crate quoting: use `dims_mm.json`, not hand-picked noisy vertices.
- STEP export would require remeshing / surface reconstruction (out of scope);
  the DXF sections are the practical bridge to parametric geometry.
