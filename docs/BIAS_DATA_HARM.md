# Bias · Data · Harm — assetpipe / CrateScanner

**1. Bias — who might it get wrong, or leave out?**

LiDAR can't see glass, black matte, or chrome and the generator invents those
missing surfaces from a Western-consumer prior, so anything that isn't a
well-lit beige box — scanned by someone who owns a LiDAR iPad, an Xcode Mac,
and a 4080 — comes out wrong or not at all.

*Fix:* Benchmark a deliberate hard-case set (glass / black / chrome / dim room /
non-Western objects) and flag every generated face as generated instead of
letting it pass as measured.

**2. Data — whose data, and are they okay with it?**

Each scan is the whole room in RGB + depth + pose — enough to rebuild a
floorplan — auto-synced to a shared Drive folder whose link sits in a public
doc, and nobody in the frame ever consented, because the repo has no consent,
scrubbing, or retention code at all.

*Fix:* Crop and face-scrub on-device before upload, record a consent scope in
the session manifest, and write a data policy with real retention and a delete
path.

**3. Harm — who could be hurt if it works? If it fails?**

Working, it's an automated way to turn anyone's home into a metric floorplan
plus an inventory of what's in it; failing, it hands Onshape and URDF a
hallucinated `dims_mm.json` with no error bar, so someone machines a part or
plans a grasp against geometry that was never measured.

*Fix:* Ship a confidence bound with every metric export, refuse the STL below a
depth-coverage threshold, and require explicit opt-in before any whole-space
capture leaves the device.
