//
//  MeshSceneView.swift
//  CrateScanner
//
//  Renders a frozen CapturedMesh in a plain (non-AR) SceneKit view the user can
//  orbit with touch. This is the "review" experience: after capture we stop the
//  live camera and let the client spin the scanned object around to inspect
//  coverage and shape.
//
//  We center the geometry on the origin so SceneKit's built-in camera controls
//  orbit around the object rather than around some arbitrary world point.
//

import SwiftUI
import SceneKit
import simd

struct MeshSceneView: UIViewRepresentable {
    let mesh: CapturedMesh

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.scene = buildScene()
        view.allowsCameraControl = true          // pinch/rotate/pan orbit
        view.autoenablesDefaultLighting = true
        view.antialiasingMode = .multisampling4X
        view.backgroundColor = .clear
        return view
    }

    func updateUIView(_ view: SCNView, context: Context) {
        // Rebuild only if the mesh identity changed (cheap guard via triangle count).
        if context.coordinator.lastTriangleCount != mesh.triangleCount {
            view.scene = buildScene()
            context.coordinator.lastTriangleCount = mesh.triangleCount
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(lastTriangleCount: mesh.triangleCount)
    }

    final class Coordinator {
        var lastTriangleCount: Int
        init(lastTriangleCount: Int) { self.lastTriangleCount = lastTriangleCount }
    }

    // MARK: Scene construction

    private func buildScene() -> SCNScene {
        let scene = SCNScene()

        guard !mesh.isEmpty else { return scene }

        // Center the mesh on the origin for pleasant orbiting.
        let center = meshCenter()
        let centered = mesh.vertices.map { $0 - center }

        let positionSource = SCNGeometrySource(
            vertices: centered.map { SCNVector3($0.x, $0.y, $0.z) }
        )

        // SceneKit wants Data for the index element.
        let indexData = mesh.indices.withUnsafeBufferPointer { Data(buffer: $0) }
        let element = SCNGeometryElement(
            data: indexData,
            primitiveType: .triangles,
            primitiveCount: mesh.triangleCount,
            bytesPerIndex: MemoryLayout<UInt32>.size
        )

        let geometry = SCNGeometry(sources: [positionSource], elements: [element])

        let material = SCNMaterial()
        material.diffuse.contents = UIColor.systemCyan
        material.isDoubleSided = true             // LiDAR meshes aren't watertight
        material.lightingModel = .physicallyBased
        geometry.materials = [material]

        let node = SCNNode(geometry: geometry)
        scene.rootNode.addChildNode(node)

        return scene
    }

    /// Centroid of the mesh's bounding box, used to recenter for display.
    private func meshCenter() -> SIMD3<Float> {
        guard let box = AABB.fitting(mesh.vertices) else { return .zero }
        return box.center
    }
}
