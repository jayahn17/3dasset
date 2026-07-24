//
//  GhostBoxEntity.swift
//  CrateScanner
//
//  The translucent "ghost crate" the user places around the machine: a faint
//  filled volume plus a bright wireframe edge frame so the dimensions read
//  clearly against a busy mesh.
//
//  RealityKit has no built-in wireframe primitive, so we build the 12 edges of
//  an axis-aligned box out of thin boxes. Because the box is axis-aligned no
//  rotation is needed — each edge is just a thin box offset to a face/corner.
//

import RealityKit
import UIKit
import simd

final class GhostBoxEntity: Entity {

    /// Thickness of the wireframe edges, in meters.
    private let edgeThickness: Float = 0.008

    private let edgeColor: UIColor = UIColor.systemCyan
    private let fillColor: UIColor = UIColor.systemCyan.withAlphaComponent(0.10)

    private var fill = ModelEntity()
    private var edgeContainer = Entity()

    required init() {
        super.init()
        addChild(fill)
        addChild(edgeContainer)
    }

    /// Resize the ghost box to the given full extents (meters). The entity's own
    /// transform positions it in the world; this only shapes it around the origin.
    func update(extents: SIMD3<Float>) {
        // Clamp to something renderable so a not-yet-fitted box still shows.
        let e = simd_max(extents, SIMD3<Float>(repeating: 0.01))

        // --- Translucent fill: a single box mesh, gives the volume a body. ---
        var fillMaterial = UnlitMaterial()
        fillMaterial.color = .init(tint: fillColor)
        // UnlitMaterial honors the tint's alpha for simple transparency.
        fill.model = ModelComponent(
            mesh: .generateBox(size: e),
            materials: [fillMaterial]
        )

        // --- Wireframe: rebuild the 12 edges for the new size. ---
        rebuildEdges(extents: e)
    }

    private func rebuildEdges(extents e: SIMD3<Float>) {
        edgeContainer.children.removeAll()

        var mat = UnlitMaterial()
        mat.color = .init(tint: edgeColor)

        let hx = e.x / 2, hy = e.y / 2, hz = e.z / 2
        let t = edgeThickness

        // Helper to spawn one edge box of a given size at a given offset.
        func addEdge(size: SIMD3<Float>, at offset: SIMD3<Float>) {
            let edge = ModelEntity(mesh: .generateBox(size: size), materials: [mat])
            edge.position = offset
            edgeContainer.addChild(edge)
        }

        // 4 edges running along X (top/bottom × front/back).
        for sy in [-hy, hy] {
            for sz in [-hz, hz] {
                addEdge(size: SIMD3(e.x, t, t), at: SIMD3(0, sy, sz))
            }
        }
        // 4 edges running along Y (the vertical posts at each corner).
        for sx in [-hx, hx] {
            for sz in [-hz, hz] {
                addEdge(size: SIMD3(t, e.y, t), at: SIMD3(sx, 0, sz))
            }
        }
        // 4 edges running along Z (top/bottom × left/right).
        for sx in [-hx, hx] {
            for sy in [-hy, hy] {
                addEdge(size: SIMD3(t, t, e.z), at: SIMD3(sx, sy, 0))
            }
        }
    }
}
