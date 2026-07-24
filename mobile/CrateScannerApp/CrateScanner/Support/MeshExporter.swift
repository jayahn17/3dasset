//
//  MeshExporter.swift
//  CrateScanner
//
//  Turns a CapturedMesh into an on-disk 3D file the client can reproduce from:
//  STL (the CNC/CAM/3D-print lingua franca), OBJ (broad interchange), or USDZ
//  (Apple AR / Quick Look). Uses ModelIO's MDLAsset export path.
//
//  UNITS — important for reproduction:
//  ARKit geometry is in meters. STL and OBJ are unitless *by convention*, and
//  most CAM/CAD tooling assumes millimeters or inches, not meters. Since this
//  client works in inches, we bake an inch scale into the coordinates by default
//  so the exported model's dimensions match the on-screen inch readout exactly.
//  (Caveat: a USDZ scaled to inches will look oversized in AR Quick Look, which
//  assumes meters — fine for interchange, noted here so it isn't a surprise.)
//

import Foundation
import ModelIO
import simd

enum MeshExportFormat: String, CaseIterable, Identifiable {
    case stl
    case obj
    case usdz

    var id: String { rawValue }
    var fileExtension: String { rawValue }
    var displayName: String { rawValue.uppercased() }
}

enum MeshExportError: LocalizedError {
    case emptyMesh
    case unsupportedFormat(String)
    case writeFailed(String)

    var errorDescription: String? {
        switch self {
        case .emptyMesh:
            return "There's no captured geometry to export yet. Scan the object and fit the box first."
        case .unsupportedFormat(let ext):
            return "This device's ModelIO can't export .\(ext) files."
        case .writeFailed(let reason):
            return "Couldn't write the mesh file: \(reason)"
        }
    }
}

enum MeshExporter {

    /// Export `mesh` to a temporary file and return its URL (ready for the share
    /// sheet). By default the geometry is scaled from meters to inches so the
    /// file is to-scale in the client's working unit.
    ///
    /// - Parameters:
    ///   - mesh: the captured, already-cropped geometry (world space, meters).
    ///   - format: STL / OBJ / USDZ.
    ///   - baseName: filename without extension (a session id is a good choice).
    ///   - metersToUnit: coordinate scale applied on export. Defaults to
    ///     inches-per-meter so exported dimensions read in inches.
    static func export(_ mesh: CapturedMesh,
                       as format: MeshExportFormat,
                       baseName: String,
                       metersToUnit: Float = Float(MeasurementResult.inchesPerMeter)) throws -> URL {

        guard !mesh.isEmpty else { throw MeshExportError.emptyMesh }

        guard MDLAsset.canExportFileExtension(format.fileExtension) else {
            throw MeshExportError.unsupportedFormat(format.fileExtension)
        }

        // Bake the unit scale into the coordinates.
        let scaled = mesh.scaled(by: metersToUnit)
        let mdlMesh = makeMDLMesh(from: scaled)

        // Generate normals so the exported surface shades correctly and STL gets
        // valid facet normals.
        mdlMesh.addNormals(withAttributeNamed: MDLVertexAttributeNormal, creaseThreshold: 0.5)

        let asset = MDLAsset()
        asset.add(mdlMesh)

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(baseName)
            .appendingPathExtension(format.fileExtension)

        // Replace any stale file at this path from a previous export.
        try? FileManager.default.removeItem(at: url)

        do {
            try asset.export(to: url)
        } catch {
            throw MeshExportError.writeFailed(error.localizedDescription)
        }
        return url
    }

    // MARK: - ModelIO construction

    /// Build an MDLMesh from raw positions + triangle indices.
    ///
    /// We hand ModelIO two buffers — packed vertex positions and a UInt32 index
    /// list — described by a minimal vertex descriptor (position only; normals
    /// are added afterward).
    private static func makeMDLMesh(from mesh: CapturedMesh) -> MDLMesh {
        let allocator = MDLMeshBufferDataAllocator()

        // Vertex buffer: positions as SIMD3<Float> (16-byte stride).
        let vertexData = mesh.vertices.withUnsafeBytes { Data($0) }
        let vertexBuffer = allocator.newBuffer(with: vertexData, type: .vertex)

        // Index buffer: UInt32 triangle list.
        let indexData = mesh.indices.withUnsafeBytes { Data($0) }
        let indexBuffer = allocator.newBuffer(with: indexData, type: .index)

        let submesh = MDLSubmesh(indexBuffer: indexBuffer,
                                 indexCount: mesh.indices.count,
                                 indexType: .uint32,
                                 geometryType: .triangles,
                                 material: nil)

        let descriptor = MDLVertexDescriptor()
        descriptor.attributes[0] = MDLVertexAttribute(
            name: MDLVertexAttributePosition,
            format: .float3,
            offset: 0,
            bufferIndex: 0)
        // Match the SIMD3<Float> in-memory stride (16 bytes, not 12).
        descriptor.layouts[0] = MDLVertexBufferLayout(
            stride: MemoryLayout<SIMD3<Float>>.stride)

        return MDLMesh(vertexBuffer: vertexBuffer,
                       vertexCount: mesh.vertices.count,
                       descriptor: descriptor,
                       submeshes: [submesh])
    }
}
