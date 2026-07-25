//
//  CaptureGate.swift
//  CrateScanner
//
//  Why ARKit frames look worse than the Camera app: ARKit runs the camera for
//  tracking, not photography — no Deep Fusion / Smart HDR, and while you orbit,
//  indoor exposure is long enough that each frame smears with motion blur.
//  Capturing continuously therefore saves mostly blurry frames.
//
//  The gate fixes this by only letting a keyframe through when the live frame is
//  actually sharp AND the device is steady — a "focus → ready → capture" flow.
//  In Detail mode it auto-fires a 12 MP keyframe at each brief pause of a
//  step-and-shoot orbit, so the saved views are crisp and well spread.
//
//  Runs on the ARSession delegate queue (serial), guarded by a lock so reset()
//  from the main actor is safe.
//

import ARKit
import CoreVideo
import simd

/// Live capture readiness, surfaced to the UI.
enum CaptureReadiness: Equatable {
    case focusing     // too blurry (out of focus, dark, or moving fast)
    case moving       // sharp enough but the device is still swinging
    case ready        // sharp and steady — good moment to shoot

    var isReady: Bool { self == .ready }
}

final class CaptureGate: @unchecked Sendable {
    // Tunables (device-tuned defaults; adjust if too eager / too strict).
    private let sharpFloor: Float = 12          // reject near-dark / very soft frames
    private let sharpFraction: Float = 0.45     // sharp if ≥ this × recent peak
    private let maxAngularSpeed: Float = 0.6    // rad/s of view direction (blur onset)
    private let maxLinearSpeed: Float = 0.25    // m/s
    private let minKeyframeGap: TimeInterval = 1.0
    private let minMoveMeters: Float = 0.12     // spread keyframes across the orbit
    private let minTurnRadians: Float = 0.14    // ~8°

    private let lock = NSLock()
    private var lastT: TimeInterval = -1
    private var lastPos = SIMD3<Float>(repeating: 0)
    private var lastFwd = SIMD3<Float>(0, 0, -1)
    private var peakSharpness: Float = 0

    // Last committed keyframe, to spread shots and avoid same-spot duplicates.
    private var lastKeyframeT: TimeInterval = -1
    private var lastKeyframePos = SIMD3<Float>(repeating: 0)
    private var lastKeyframeFwd = SIMD3<Float>(0, 0, -1)

    /// Evaluate one live frame.
    /// - Returns: current readiness, and whether Detail mode should auto-capture
    ///   a keyframe now. When it returns `autoFire = true` it has already
    ///   reserved this pose/time, so it won't fire again until you've moved on.
    func evaluate(_ frame: ARFrame, isRecording: Bool, autoKeyframes: Bool)
        -> (readiness: CaptureReadiness, autoFire: Bool) {
        let t = frame.timestamp
        let pos = frame.camera.transform.translation
        let fwd = -SIMD3<Float>(frame.camera.transform.columns.2.x,
                                frame.camera.transform.columns.2.y,
                                frame.camera.transform.columns.2.z)
        let sharp = lumaSharpness(frame.capturedImage)

        lock.lock()
        defer { lock.unlock() }

        // Motion since the previous frame.
        var linSpeed: Float = 0, angSpeed: Float = 0
        if lastT > 0, t > lastT {
            let dt = Float(t - lastT)
            linSpeed = simd_distance(pos, lastPos) / dt
            angSpeed = angle(lastFwd, fwd) / dt
        }
        lastT = t; lastPos = pos; lastFwd = fwd

        // Adaptive sharpness peak (slow decay so it tracks the scene/lighting).
        peakSharpness = max(peakSharpness * 0.99, sharp)
        let sharpEnough = sharp >= sharpFloor && sharp >= sharpFraction * peakSharpness
        let steady = angSpeed <= maxAngularSpeed && linSpeed <= maxLinearSpeed

        let readiness: CaptureReadiness =
            !sharpEnough ? .focusing : (steady ? .ready : .moving)

        var autoFire = false
        if autoKeyframes, isRecording, readiness == .ready {
            let movedEnough = simd_distance(pos, lastKeyframePos) >= minMoveMeters
                || angle(lastKeyframeFwd, fwd) >= minTurnRadians
            let gapOK = lastKeyframeT < 0 || (t - lastKeyframeT) >= minKeyframeGap
            if movedEnough && gapOK {
                autoFire = true
                lastKeyframeT = t
                lastKeyframePos = pos
                lastKeyframeFwd = fwd
            }
        }
        return (readiness, autoFire)
    }

    func reset() {
        lock.lock(); defer { lock.unlock() }
        lastT = -1; peakSharpness = 0; lastKeyframeT = -1
        lastPos = .init(repeating: 0); lastFwd = .init(0, 0, -1)
        lastKeyframePos = .init(repeating: 0); lastKeyframeFwd = .init(0, 0, -1)
    }
}

/// Unsigned angle (radians) between two unit-ish vectors.
private func angle(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Float {
    let na = simd_normalize(a), nb = simd_normalize(b)
    return acos(min(max(simd_dot(na, nb), -1), 1))
}

/// Gradient energy of the luma plane — a cheap focus/sharpness score (higher =
/// sharper). Reads ARKit's Y plane directly (plane 0 of the YUV buffer) and
/// subsamples, so it's fast enough to run every frame.
func lumaSharpness(_ pixelBuffer: CVPixelBuffer, step: Int = 4) -> Float {
    CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
    guard CVPixelBufferGetPlaneCount(pixelBuffer) >= 1,
          let base = CVPixelBufferGetBaseAddressOfPlane(pixelBuffer, 0) else { return 0 }
    let w = CVPixelBufferGetWidthOfPlane(pixelBuffer, 0)
    let h = CVPixelBufferGetHeightOfPlane(pixelBuffer, 0)
    let rowBytes = CVPixelBufferGetBytesPerRowOfPlane(pixelBuffer, 0)
    let p = base.assumingMemoryBound(to: UInt8.self)

    var sum: Double = 0
    var count = 0
    var y = step
    while y < h - step {
        let row = y * rowBytes
        let rowDown = (y + step) * rowBytes
        var x = step
        while x < w - step {
            let c = Int(p[row + x])
            let gx = c - Int(p[row + x + step])
            let gy = c - Int(p[rowDown + x])
            sum += Double(gx * gx + gy * gy)
            count += 1
            x += step
        }
        y += step
    }
    return count > 0 ? Float(sum / Double(count)) : 0
}
