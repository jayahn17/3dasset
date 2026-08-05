# capture/

iPad Pro room-mode capture. **Not a new app** — this extends
`mobile/CrateScannerApp`, which already owns the ARKit session, the depth PNG16
writer, pose/intrinsics recording, and export.

Planned contents: room-mode Swift sources, the coverage-HUD components, and the
session v2 writer, added to the existing Xcode project rather than a fork.

Spec: [../docs/CAPTURE_SPEC.md](../docs/CAPTURE_SPEC.md) — including the two
silent bugs in the current exporter (high-res keyframes carry no `sceneDepth`;
the streaming track can record zero frames) that P1 must fix.
