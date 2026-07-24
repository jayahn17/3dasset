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
    private let maxColorWidth: CGFloat

    /// Encoding + disk I/O happens here, never on the caller's thread. The
    /// caller is ARKit's delegate queue; blocking it drops AR frames.
    private let ioQueue = DispatchQueue(label: "CrateScanner.SessionExporter.io",
                                        qos: .utility)
    /// `frames` is appended from the delegate queue and read by finalize() on
    /// the main actor, so it needs a lock.
    private let lock = NSLock()

    private var lastRecordTime: TimeInterval = -1   // delegate queue only
    private var frames: [[String: Any]] = []        // guarded by `lock`

    /// - Parameters:
    ///   - directory: parent folder (a UUID subfolder is created). Defaults to
    ///     the user-visible Documents/CrateScans/Sessions — *not* the temporary
    ///     directory, which iOS purges and the Files app cannot see.
    ///   - minInterval: seconds between saved frames. 0.1 ≈ 10 Hz; TSDF fusion
    ///     wants good angular coverage far more than raw frame rate.
    ///   - maxColorWidth: color is downscaled to this before JPEG encoding.
    ///     assetpipe resizes color to the *depth* resolution anyway
    ///     (nvblox_fuse.py:111) — 256×192 on LiDAR iPads — so storing the full
    ///     1920×1440 capture is ~28× more pixels than the fuser ever reads.
    ///     640 px keeps ample headroom at roughly a tenth of the bytes.
    init(directory: URL = ScanStorage.sessionsDirectory,
         minInterval: TimeInterval = 0.1,
         maxColorWidth: CGFloat = 640) throws {
        let id = UUID().uuidString.prefix(8)
        rootURL = directory.appendingPathComponent("session-\(id)", isDirectory: true)
        colorDir = rootURL.appendingPathComponent("color", isDirectory: true)
        depthDir = rootURL.appendingPathComponent("depth", isDirectory: true)
        self.minInterval = minInterval
        self.maxColorWidth = maxColorWidth
        try FileManager.default.createDirectory(at: colorDir, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: depthDir, withIntermediateDirectories: true)
    }

    /// Sample one frame if enough time has elapsed.
    ///
    /// Call this from `ARSession`'s delegate queue — NOT the main actor, and
    /// never from inside a `Task` that outlives the callback. `ARFrame` owns
    /// pixel buffers from a small fixed pool; holding one past the delegate
    /// call starves ARKit and it stops delivering frames. Everything that needs
    /// the buffers happens synchronously here; the expensive encode + write is
    /// handed to `ioQueue` afterwards.
    func maybeRecord(_ frame: ARFrame) {
        let t = frame.timestamp
        guard t - lastRecordTime >= minInterval else { return }
        guard let depth = frame.smoothedSceneDepth ?? frame.sceneDepth else { return }
        lastRecordTime = t

        // --- everything below touches the pixel buffers: must be synchronous ---

        let depthW = CVPixelBufferGetWidth(depth.depthMap)
        let depthH = CVPixelBufferGetHeight(depth.depthMap)
        let colorW = CVPixelBufferGetWidth(frame.capturedImage)
        let colorH = CVPixelBufferGetHeight(frame.capturedImage)

        // Downscale + decouple from ARKit's buffer in one step.
        guard let colorImage = downscaledCGImage(frame.capturedImage,
                                                 maxWidth: maxColorWidth) else { return }
        // Depth is tiny (256×192×2B ≈ 98 KB) — copying it out is cheap.
        guard let depthMM = depthMillimetres(depth.depthMap) else { return }

        let pose = frame.camera.transform  // camera-to-world, column-major float4x4
        let K = frame.camera.intrinsics    // 3x3 column-major
        let fx = K.columns.0.x
        let fy = K.columns.1.y
        let cx = K.columns.2.x
        let cy = K.columns.2.y

        // Intrinsics are emitted in DEPTH pixel coordinates, because the fuser
        // pairs them with the depth image's shape. This is independent of the
        // resolution we store color at — which is why downscaling color above
        // is transparent to the pipeline.
        let sx = Float(depthW) / Float(max(colorW, 1))
        let sy = Float(depthH) / Float(max(colorH, 1))

        lock.lock()
        let idx = frames.count
        let colorName = String(format: "%04d.jpg", idx)
        let depthName = String(format: "%04d.png", idx)
        frames.append([
            "id": String(format: "f%05d", idx),
            "t": t,
            "color": "color/\(colorName)",
            "depth": "depth/\(depthName)",
            "pose": float4x4ToRowMajorArray(pose),
            "intrinsics": [Double(fx * sx), Double(fy * sy), Double(cx * sx), Double(cy * sy)],
        ])
        lock.unlock()

        let colorURL = colorDir.appendingPathComponent(colorName)
        let depthURL = depthDir.appendingPathComponent(depthName)
        ioQueue.async {
            try? writeJPEG(colorImage, to: colorURL)
            try? writeDepthPNG16(depthMM, width: depthW, height: depthH, to: depthURL)
        }
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

/// One context for the whole app. Constructing a CIContext allocates a Metal
/// command queue and compiles kernels — doing that per frame at 4 Hz stalls the
/// capture loop.
private let sharedCIContext = CIContext(options: nil)

/// Downscale ARKit's capture and detach it from the pixel-buffer pool.
///
/// Must run synchronously inside the delegate callback — after it returns, the
/// buffer belongs to ARKit again. The returned CGImage owns its own storage, so
/// it is safe to encode later on a background queue.
private func downscaledCGImage(_ pixelBuffer: CVPixelBuffer,
                               maxWidth: CGFloat) -> CGImage? {
    let image = CIImage(cvPixelBuffer: pixelBuffer)
    let width = image.extent.width
    let scale = width > maxWidth ? maxWidth / width : 1
    let scaled = scale < 1
        ? image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        : image
    return sharedCIContext.createCGImage(scaled, from: scaled.extent)
}

/// Copy ARKit's Float32-metres depth out as UInt16 millimetres.
///
/// Also synchronous-in-callback. 256×192×2 B ≈ 98 KB, so the copy is cheap
/// next to the JPEG encode it lets us defer.
private func depthMillimetres(_ depthMap: CVPixelBuffer) -> [UInt16]? {
    CVPixelBufferLockBaseAddress(depthMap, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }

    let w = CVPixelBufferGetWidth(depthMap)
    let h = CVPixelBufferGetHeight(depthMap)
    guard let base = CVPixelBufferGetBaseAddress(depthMap) else { return nil }
    let src = base.bindMemory(to: Float32.self, capacity: w * h)

    var u16 = [UInt16](repeating: 0, count: w * h)
    for i in 0..<(w * h) {
        let m = src[i]
        if m.isFinite, m > 0 {
            u16[i] = UInt16(min(max(m * 1000.0, 0), Float(UInt16.max)))
        }
    }
    return u16
}

private func writeJPEG(_ image: CGImage, to url: URL) throws {
    guard let jpeg = UIImage(cgImage: image).jpegData(compressionQuality: 0.85) else {
        throw NSError(domain: "SessionExporter", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "JPEG encode failed"])
    }
    try jpeg.write(to: url)
}

/// assetpipe expects uint16 millimetre PNGs.
private func writeDepthPNG16(_ u16: [UInt16], width w: Int, height h: Int,
                             to url: URL) throws {
    let data = u16.withUnsafeBufferPointer { Data(buffer: $0) }
    let cfData = data as CFData
    guard let provider = CGDataProvider(data: cfData) else {
        throw NSError(domain: "SessionExporter", code: 4, userInfo: nil)
    }
    // byteOrder16Little is REQUIRED. `u16` is a native little-endian UInt16
    // buffer; without this flag CoreGraphics treats it as big-endian and the
    // PNG on disk comes out byte-swapped — 1000 mm is written as 59395 mm.
    // assetpipe then multiplies by 0.001 and fuses a 59 m scene, so nvblox
    // returns "Fusion produced only N points" (nvblox_fuse.py:251) or garbage
    // geometry. `rgbd --inspect-only` does NOT catch it: that only checks the
    // manifest has pose/intrinsics fields, never depth magnitudes.
    let bitmapInfo = CGImageAlphaInfo.none.rawValue | CGBitmapInfo.byteOrder16Little.rawValue
    guard let cgImage = CGImage(
        width: w, height: h,
        bitsPerComponent: 16, bitsPerPixel: 16,
        bytesPerRow: w * 2,
        space: CGColorSpaceCreateDeviceGray(),
        bitmapInfo: CGBitmapInfo(rawValue: bitmapInfo),
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
