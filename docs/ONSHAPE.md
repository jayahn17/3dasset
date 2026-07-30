# TRELLIS mesh → Onshape (and other CAD)

TRELLIS produces a triangle mesh, not a parametric solid. Import it as a **Mesh**.

## Files from the GPU route queue

After a product-scale scan (≤24″ longest edge):

| File | Units | Use |
|------|-------|-----|
| `asset_trellis_mm.stl` | **millimeters** | Onshape / Fusion / SolidWorks / FreeCAD import |
| `asset_trellis.glb` | meters (scaled) | Web viewer / Blender |
| `dims_mm.json` | mm | Authoritative L×W×H (+ crate) from RGB-D |
| `dims.json` | inches | Same source as object fuse |

Do **not** measure the raw unscaled generator mesh for quoting.

## Onshape

1. Open Onshape → **Create** → **Import**
2. Upload `asset_trellis_mm.stl`
3. Insert as **Mesh** into a Part Studio
4. **Measure** edges / bounding box — should match `dims_mm.json` (± tessellation)

## Blender

1. File → Import → glTF 2.0 → `asset_trellis.glb` (meters), or STL (mm — set scene unit to mm)
2. Enable MeasureIt or use the Measure tool
3. Compare longest edges to `dims_mm.json`

## Limits

- Mesh is not editable BREP / FeatureScript CAD.
- Crate quoting: use `dims_mm.json`, not hand-picked noisy vertices.
- STEP export would require remeshing / surface reconstruction (out of scope).
