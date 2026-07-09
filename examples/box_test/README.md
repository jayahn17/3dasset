# Box test

The first milestone from the vision: **"test on 'box'."** It exercises the
whole pipeline — reconstruct → digitalize (mesh + URDF) → catalog → control
center — with zero dependencies.

## Synthetic (no camera, no installs)

```bash
python -m assetpipe demo --out twin_out
open twin_out/control_center.html   # drag to orbit; sidebar = your twin
```

This digitalizes three stand-in boxes (a cardboard box, a shoe box, a moving
carton) at real-world dimensions and files them into the twin with locations.

## With your own photos

Drop a few photos of a real box in a folder and run:

```bash
python -m assetpipe run \
  --input ./examples/box_test/photos \
  --classes "cardboard box" \
  --location "garage shelf" \
  --box-dims 0.40,0.30,0.30      # measured W,H,D in meters (fallback)
```

Then `python -m assetpipe list` and open the control center.

## What to do next

- Install `ultralytics` and switch to `YoloWorldDetector` so the label and box
  come from the image instead of `--classes`.
- Stand up a TRELLIS service and switch to `TrellisReconstructor` for a
  textured mesh instead of a procedural cuboid.
- See [`../../docs/ROADMAP.md`](../../docs/ROADMAP.md).
