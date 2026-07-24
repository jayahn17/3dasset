//
//  SessionExporter.swift
//  CrateScanner — drop into the E170_Client_Project iOS target
//
//  Records ARKit RGB + sceneDepth + pose + intrinsics into the assetpipe
//  session layout so the 4080 box can run:
//
//      python -m assetpipe rgbd SESSION --backend open3d --out ...
//
//  Wire from ScanViewModel:
//    - create `SessionExporter` when scanning starts
//    - in `session(_:didUpdate:)` call `exporter?.maybeRecord(frame)` (throttled)
//    - on `capture()`, call `exporter?.finalize(objectHint:)` and zip/share the folder
//
//  Requires: sceneDepth already enabled (CrateScanner does this today).
//

import ARKit
import CoreImage
import Foundation
import UIKit
import simd

/// Writes a fuse-ready RGB-D session for assetpipe / nvblox.
final class SessionExporter {

    let rootURL: URL
    private let colorDir: URL
    private let depthDir: URL
    private let minInterval: TimeInterval
    private var lastRecordTime: TimeInterval = -1
    private var frames: [[String: Any]] = []
    private let ciContext = CIContext(options: nil)

    /// - Parameters:
    ///   - directory: parent folder (a UUID subfolder is created).
    ///   - minInterval: seconds between saved frames (2–5 Hz is plenty).
    init(directory: URL = FileManager.default.temporaryDirectory,
         minInterval: TimeInterval = 0.25) throws {
        let id = UUID().uuidString.prefix(8)
        rootURL = directory.appendingPathComponent("session-\(id)", isDirectory: true)
        colorDir = rootURL.appendingPathComponent("color", isDirectory: true)
        depthDir = rootURL.appendingPathComponent("depth", isDirectory: true)
        self.minInterval = minInterval
        try FileManager.default.createDirectory(at: colorDir, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: depthDir, withIntermediateDirectories: true)
    }

    /// Sample one frame if enough time has elapsed. Safe to call every AR tick.
    func maybeRecord(_ frame: ARFrame) {
        let t = frame.timestamp
        guard t - lastRecordTime >= minInterval else { return }
        guard let depth = frame.smoothedSceneDepth ?? frame.sceneDepth else { return }
        lastRecordTime = t

        let idx = frames.count
        let colorName = String(format: "%04d.jpg", idx)
        let depthName = String(format: "%04d.png", idx)

        do {
            try writeColorJPEG(frame.capturedImage, to: colorDir.appendingPathComponent(colorName))
            try writeDepthPNGMM(depth.depthMap, to: depthDir.appendingPathComponent(depthName))
        } catch {
            return
        }

        let pose = frame.camera.transform  // camera-to-world, column-major float4x4
        let K = frame.camera.intrinsics    // 3x3; fx=K[0,0], fy=K[1,1], cx=K[2,0], cy=K[2,1] in column-major
        // simd float3x3 columns: col0=(fx,0,0), col1=(0,fy,0), col2=(cx,cy,1) in row-ish Apple layout
        let fx = K.columns.0.x
        let fy = K.columns.1.y
        let cx = K.columns.2.x
        let cy = K.columns.2.y

        // Depth map resolution often differs from color — assetpipe resizes color to depth.
        // Intrinsics here are for the **color** camera; for best results scale K to depth size
        // when you know depth width/height (CVPixelBufferGetWidth/Height).
        let depthW = CVPixelBufferGetWidth(depth.depthMap)
        let depthH = CVPixelBufferGetHeight(depth.depthMap)
        let colorW = CVPixelBufferGetWidth(frame.capturedImage)
        let colorH = CVPixelBufferGetHeight(frame.capturedImage)
        let sx = Float(depthW) / Float(max(colorW, 1))
        let sy = Float(depthH) / Float(max(colorH, 1))

        frames.append([
            "id": String(format: "f%05d", idx),
            "t": t,
            "color": "color/\(colorName)",
            "depth": "depth/\(depthName)",
            "pose": float4x4ToRowMajorArray(pose),
            "intrinsics": [Double(fx * sx), Double(fy * sy), Double(cx * sx), Double(cy * sy)],
        ])
    }

    /// Write manifest.json. Returns session root URL (zip this for upload).
    @discardableResult
    func finalize(objectHint: String? = nil, location: String? = nil) throws -> URL {
        var man: [String: Any] = [
            "depth_unit": "mm",
            "source": "cratescanner",
            "frames": frames,
        ]
        if let objectHint { man["object_hint"] = objectHint }
        if let location { man["location"] = location }
        let data = try JSONSerialization.data(withJSONObject: man, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: rootURL.appendingPathComponent("manifest.json"))
        return rootURL
    }

    var frameCount: Int { frames.count }
}

// MARK: - Writers

private func writeColorJPEG(_ pixelBuffer: CVPixelBuffer, to url: URL) throws {
    let image = CIImage(cvPixelBuffer: pixelBuffer)
    guard let cg = CIContext().createCGImage(image, from: image.extent) else {
        throw NSError(domain: "SessionExporter", code: 1, userInfo: [NSLocalizedDescriptionKey: "CGImage failed"])
    }
    let ui = UIImage(cgImage: cg)
    guard let jpeg = ui.jpegData(compressionQuality: 0.85) else {
        throw NSError(domain: "SessionExporter", code: 2, userInfo: [NSLocalizedDescriptionKey: "JPEG encode failed"])
    }
    try jpeg.write(to: url)
}

/// ARKit sceneDepth is Float32 meters. assetpipe expects uint16 millimeters PNG.
private func writeDepthPNGMM(_ depthMap: CVPixelBuffer, to url: URL) throws {
    CVPixelBufferLockBaseAddress(depthMap, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }

    let w = CVPixelBufferGetWidth(depthMap)
    let h = CVPixelBufferGetHeight(depthMap)
    guard let base = CVPixelBufferGetBaseAddress(depthMap) else {
        throw NSError(domain: "SessionExporter", code: 3, userInfo: nil)
    }
    let src = base.bindMemory(to: Float32.self, capacity: w * h)

    var u16 = [UInt16](repeating: 0, count: w * h)
    for i in 0..<(w * h) {
        let m = src[i]
        if m.isFinite, m > 0 {
            let mm = min(max(m * 1000.0, 0), Float(UInt16.max))
            u16[i] = UInt16(mm)
        }
    }

    let data = u16.withUnsafeBufferPointer { Data(buffer: $0) }
    // Write a minimal 16-bit gray PNG via CGImage
    let cfData = data as CFData
    guard let provider = CGDataProvider(data: cfData) else {
        throw NSError(domain: "SessionExporter", code: 4, userInfo: nil)
    }
    guard let cgImage = CGImage(
        width: w, height: h,
        bitsPerComponent: 16, bitsPerPixel: 16,
        bytesPerRow: w * 2,
        space: CGColorSpaceCreateDeviceGray(),
        bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
        provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent
    ) else {
        throw NSError(domain: "SessionExporter", code: 5, userInfo: nil)
    }
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else {
        throw NSError(domain: "SessionExporter", code: 6, userInfo: nil)
    }
    CGImageDestinationAddImage(dest, cgImage, nil)
    if !CGImageDestinationFinalize(dest) {
        throw NSError(domain: "SessionExporter", code: 7, userInfo: nil)
    }
}

/// assetpipe expects row-major 4×4 camera-to-world.
private func float4x4ToRowMajorArray(_ m: simd_float4x4) -> [Double] {
    // simd is column-major; emit rows.
    var out: [Double] = []
    out.reserveCapacity(16)
    for row in 0..<4 {
        for col in 0..<4 {
            out.append(Double(m[col][row]))
        }
    }
    return out
}
