//
//  ARViewContainer.swift
//  CrateScanner
//
//  Bridges RealityKit's ARView into SwiftUI and owns the touch gesture for
//  nudging the placed ghost box. All AR/session logic lives in the view model;
//  this type only creates the view, wires the delegate, and forwards gestures.
//

import SwiftUI
import RealityKit
import ARKit
import simd

struct ARViewContainer: UIViewRepresentable {
    @ObservedObject var viewModel: ScanViewModel

    func makeUIView(context: Context) -> ARView {
        let arView = ARView(frame: .zero)

        // Hand the view to the view model, which configures + runs the session.
        viewModel.attach(to: arView)

        // One-finger drag repositions the placed box on the ground plane.
        let pan = UIPanGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.handlePan(_:)))
        arView.addGestureRecognizer(pan)

        context.coordinator.arView = arView
        return arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(viewModel: viewModel)
    }

    // MARK: Gesture handling

    final class Coordinator: NSObject {
        let viewModel: ScanViewModel
        weak var arView: ARView?

        init(viewModel: ScanViewModel) {
            self.viewModel = viewModel
        }

        /// Translate an incremental screen-space pan into a world-space nudge on
        /// the ground plane. This is a deliberately simple mapping — screen X →
        /// world X, screen Y → world Z — scaled to a comfortable sensitivity.
        /// Precise placement isn't needed because "Fit" snaps the box to the
        /// object afterward; this is just for rough positioning.
        @objc func handlePan(_ gesture: UIPanGestureRecognizer) {
            guard let view = arView else { return }

            // Work incrementally: read the delta since the last callback, then
            // zero the recognizer's translation so movements accumulate smoothly.
            let t = gesture.translation(in: view)
            gesture.setTranslation(.zero, in: view)

            // Meters of world movement per screen point. Tuned for a phone.
            let metersPerPoint: Float = 0.004
            let worldDelta = SIMD2<Float>(Float(t.x) * metersPerPoint,
                                          Float(t.y) * metersPerPoint)

            Task { @MainActor in
                self.viewModel.moveBox(byWorldXZ: worldDelta)
            }
        }
    }
}
