//
//  ARViewContainer.swift
//  CrateScanner
//
//  Bridges RealityKit's ARView into SwiftUI and owns the touch gesture for
//  positioning the ghost box. All AR/session logic lives in the view model;
//  this type only creates the view, wires the delegate, and forwards gestures.
//
//  Tap, not drag: this app is used one-handed while walking around a machine, so
//  every interaction is a single tap. A tap also raycasts to the actual surface
//  under the finger, which is more accurate than the pixels-to-metres guess a
//  pan gesture had to make.
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

        // One tap moves the placed box to whatever surface was tapped.
        let tap = UITapGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.handleTap(_:)))
        arView.addGestureRecognizer(tap)

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

        /// Move the ghost box under the tapped point. The view model ignores this
        /// when no box has been placed yet, so stray taps during a plain scan do
        /// nothing — Measure is what puts a box on screen in the first place.
        @objc func handleTap(_ gesture: UITapGestureRecognizer) {
            guard let view = arView else { return }
            let point = gesture.location(in: view)
            Task { @MainActor in
                self.viewModel.moveBox(toScreenPoint: point)
            }
        }
    }
}
