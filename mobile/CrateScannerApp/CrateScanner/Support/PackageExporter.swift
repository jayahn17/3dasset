//
//  PackageExporter.swift
//  CrateScanner
//
//  Builds one AirDrop/scp-ready folder for the Linux assetpipe pipeline:
//
//      package/
//        README_LINUX.txt
//        measurement.json
//        mesh.obj                 (inches — Path A: assetpipe cratescan)
//        session/                 (RGB-D — Path B: assetpipe rgbd)
//          manifest.json
//          color/*.jpg
//          depth/*.png
//
//  Zip with NSFileCoordinator (.forUploading) for ShareSheet.
//

import Foundation

enum PackageExportError: LocalizedError {
    case nothingToExport
    case writeFailed(String)

    var errorDescription: String? {
        switch self {
        case .nothingToExport:
            return "Capture a scan first (mesh and/or RGB-D session)."
        case .writeFailed(let reason):
            return "Could not build export package: \(reason)"
        }
    }
}

enum PackageExporter {

    /// Assemble mesh + measurement + RGB-D session into one folder; return zip URL.
    static func buildZip(
        mesh: CapturedMesh?,
        measurement: MeasurementResult?,
        sessionDirectory: URL?,
        baseName: String
    ) throws -> URL {
        let fm = FileManager.default
        let root = fm.temporaryDirectory
            .appendingPathComponent("CrateScan-\(baseName)", isDirectory: true)
        try? fm.removeItem(at: root)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)

        var wroteSomething = false

        if let measurement {
            let enc = JSONEncoder()
            enc.outputFormatting = [.prettyPrinted, .sortedKeys]
            enc.dateEncodingStrategy = .iso8601
            let data = try enc.encode(measurement)
            try data.write(to: root.appendingPathComponent("measurement.json"))
            wroteSomething = true
        }

        if let mesh, !mesh.isEmpty {
            let objURL = try MeshExporter.export(
                mesh, as: .obj, baseName: "mesh",
                metersToUnit: Float(MeasurementResult.inchesPerMeter)
            )
            let dest = root.appendingPathComponent("mesh.obj")
            if fm.fileExists(atPath: dest.path) { try fm.removeItem(at: dest) }
            try fm.copyItem(at: objURL, to: dest)
            wroteSomething = true
        }

        if let sessionDirectory, fm.fileExists(atPath: sessionDirectory.path) {
            let dest = root.appendingPathComponent("session", isDirectory: true)
            try fm.copyItem(at: sessionDirectory, to: dest)
            wroteSomething = true
        }

        guard wroteSomething else { throw PackageExportError.nothingToExport }

        let readme = """
        CrateScanner → Linux assetpipe package
        ======================================

        On the Linux box (4080):

          unzip this folder, then:

          # Path A — mesh + dims (on-device LiDAR mesh)
          python -m assetpipe cratescan . --out demo_out/crate1

          # Path B — RGB-D → nvblox (preferred). auto falls back to Open3D TSDF
          # only if nvblox_torch cannot load on the server.
          python -m assetpipe rgbd ./session --inspect-only
          python -m assetpipe rgbd ./session --backend auto --out demo_out/crate1_rgbd
          # python -m assetpipe rgbd ./session --backend nvblox --out demo_out/crate1_rgbd

        Docs: 3dasset/docs/CRATESCANNER_BRIDGE.md
              3dasset/docs/DUAL_MACHINE_PLAYBOOK.md
              3dasset/docs/NVBLOX_WORKFLOW.md
        """
        try readme.write(to: root.appendingPathComponent("README_LINUX.txt"),
                         atomically: true, encoding: .utf8)

        return try zipForSharing(root)
    }

    /// Zip a directory for UIActivityViewController / AirDrop.
    static func zipForSharing(_ directory: URL) throws -> URL {
        let zipURL = directory.appendingPathExtension("zip")
        try? FileManager.default.removeItem(at: zipURL)

        var coordinatorError: NSError?
        var copyError: Error?
        let coordinator = NSFileCoordinator()
        coordinator.coordinate(readingItemAt: directory,
                               options: [.forUploading],
                               error: &coordinatorError) { tempZip in
            do {
                try FileManager.default.copyItem(at: tempZip, to: zipURL)
            } catch {
                copyError = error
            }
        }
        if let coordinatorError { throw PackageExportError.writeFailed(coordinatorError.localizedDescription) }
        if let copyError { throw PackageExportError.writeFailed(copyError.localizedDescription) }
        guard FileManager.default.fileExists(atPath: zipURL.path) else {
            throw PackageExportError.writeFailed("Zip was not created.")
        }
        return zipURL
    }
}
