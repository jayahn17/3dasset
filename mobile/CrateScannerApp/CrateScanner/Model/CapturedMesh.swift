//
//  CapturedMesh.swift
//  CrateScanner
//
//  A plain, framework-free triangle mesh: world-space vertices (meters) plus a
//  flat triangle index list. This is the interchange format between the AR layer
//  (which extracts ARMeshAnchor geometry) and the exporter (which writes files).
//
//  Keeping it ARKit-free means the cropping and scaling math is trivially
//  testable, and the exporter never has to know about ARKit.
//

import simd

struct CapturedMesh {

    /// World-space vertex positions, in meters (ARKit's native unit).
    var vertices: [SIMD3<Float>]

    /// Flat triangle list: every three entries index a triangle in `vertices`.
    var indices: [UInt32]

    init(vertices: [SIMD3<Float>] = [], indices: [UInt32] = []) {
        self.vertices = vertices
        self.indices = indices
    }

    var isEmpty: Bool { indices.isEmpty }

    var triangleCount: Int { indices.count / 3 }

    /// Merge another mesh's geometry into this one, offsetting its indices so the
    /// two vertex arrays concatenate correctly. Used to combine the many
    /// ARMeshAnchors that make up a single scan into one exportable mesh.
    mutating func append(_ other: CapturedMesh) {
        let offset = UInt32(vertices.count)
        vertices.append(contentsOf: other.vertices)
        indices.append(contentsOf: other.indices.map { $0 + offset })
    }

    /// Uniformly scale every vertex (e.g. meters → inches for export). Returns a
    /// new mesh; the original is untouched.
    func scaled(by factor: Float) -> CapturedMesh {
        CapturedMesh(vertices: vertices.map { $0 * factor }, indices: indices)
    }

    /// Keep only the triangles whose centroid falls inside `box`, then drop any
    /// vertices no longer referenced and re-index compactly.
    ///
    /// This is what isolates the machine from the floor and surrounding clutter:
    /// the user's placed box defines the region, and everything outside it is
    /// discarded before export.
    func cropped(to box: AABB) -> CapturedMesh {
        var newVertices: [SIMD3<Float>] = []
        var newIndices: [UInt32] = []
        // Map an old vertex index to its slot in the compacted array (lazily).
        var remap: [UInt32: UInt32] = [:]

        func remapped(_ old: UInt32) -> UInt32 {
            if let existing = remap[old] { return existing }
            let slot = UInt32(newVertices.count)
            newVertices.append(vertices[Int(old)])
            remap[old] = slot
            return slot
        }

        var i = 0
        while i + 2 < indices.count {
            let a = indices[i], b = indices[i + 1], c = indices[i + 2]
            let centroid = (vertices[Int(a)] + vertices[Int(b)] + vertices[Int(c)]) / 3
            if box.contains(centroid) {
                newIndices.append(remapped(a))
                newIndices.append(remapped(b))
                newIndices.append(remapped(c))
            }
            i += 3
        }

        return CapturedMesh(vertices: newVertices, indices: newIndices)
    }
}
