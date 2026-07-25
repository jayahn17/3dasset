//
//  GuidedCapture.swift
//  CrateScanner
//
//  Turns "orbit and hope you covered it" into a guided checklist: a dome of
//  optimal camera viewpoints around the object. Each target is a position + a
//  look-at-the-object direction. The app shows them in AR, points the user at
//  the nearest unshot one, and auto-captures when they arrive sharp and steady.
//
//  This file is the pure geometry (targets + guidance math) so it can be reasoned
//  about and tested independently of ARKit/RealityKit; the rendering and capture
//  wiring live in ScanViewModel.
//

import simd

/// One planned viewpoint around the object.
struct CaptureTarget {
    let position: SIMD3<Float>   // where to stand, world space
    let forward: SIMD3<Float>    // unit direction to look (toward the object)
    var done: Bool = false
}

enum GuidedCapture {

    /// How close (metres) and how aligned (radians) the camera must be to a
    /// target to count as "reached".
    static let reachMeters: Float = 0.30
    static let reachRadians: Float = 22 * .pi / 180

    /// Build a dome of viewpoints around `center` at `radius`, all looking in at
    /// the object. Three elevation rings plus a top-down, azimuth counts thinned
    /// toward the poles — ~25–35 targets, a good spread for TSDF + MVS.
    static func makeTargets(center: SIMD3<Float>, radius: Float) -> [CaptureTarget] {
        var targets: [CaptureTarget] = []
        let r = max(radius, 0.3)
        for elevDeg in [18.0, 45.0, 68.0] as [Double] {
            let elev = Float(elevDeg * .pi / 180)
            let count = max(6, Int((12 * cos(elev)).rounded()))
            for i in 0..<count {
                let az = 2 * Float.pi * Float(i) / Float(count)
                let dir = SIMD3<Float>(cos(elev) * cos(az),
                                       sin(elev),
                                       cos(elev) * sin(az))
                let pos = center + dir * r
                targets.append(CaptureTarget(position: pos, forward: simd_normalize(center - pos)))
            }
        }
        // One top-down.
        let top = center + SIMD3<Float>(0, r, 0)
        targets.append(CaptureTarget(position: top, forward: simd_normalize(center - top)))
        return targets
    }

    /// Index of the nearest not-yet-done target to a camera position.
    static func nearestPending(to camPos: SIMD3<Float>, in targets: [CaptureTarget]) -> Int? {
        var best: Int?
        var bestD = Float.greatestFiniteMagnitude
        for (i, t) in targets.enumerated() where !t.done {
            let d = simd_distance(camPos, t.position)
            if d < bestD { bestD = d; best = i }
        }
        return best
    }

    /// Is the camera close enough and aligned enough to capture this target?
    static func reached(_ target: CaptureTarget,
                        camPos: SIMD3<Float>, camForward: SIMD3<Float>) -> Bool {
        guard simd_distance(camPos, target.position) <= reachMeters else { return false }
        let a = angleBetween(simd_normalize(camForward), target.forward)
        return a <= reachRadians
    }

    /// A short "move …" instruction toward `target`, in the camera's own frame.
    static func instruction(to target: CaptureTarget, cameraTransform m: simd_float4x4) -> String {
        let camPos = SIMD3<Float>(m.columns.3.x, m.columns.3.y, m.columns.3.z)
        let right = simd_normalize(SIMD3<Float>(m.columns.0.x, m.columns.0.y, m.columns.0.z))
        let up = simd_normalize(SIMD3<Float>(m.columns.1.x, m.columns.1.y, m.columns.1.z))
        let fwd = simd_normalize(-SIMD3<Float>(m.columns.2.x, m.columns.2.y, m.columns.2.z))
        let delta = target.position - camPos

        var parts: [String] = []
        let r = simd_dot(delta, right)
        let u = simd_dot(delta, up)
        let f = simd_dot(delta, fwd)
        if r >  0.15 { parts.append("right") } else if r < -0.15 { parts.append("left") }
        if u >  0.15 { parts.append("up") }    else if u < -0.15 { parts.append("down") }
        if f >  0.20 { parts.append("forward") } else if f < -0.20 { parts.append("back") }

        if parts.isEmpty {
            // In position — is the aim off?
            let a = angleBetween(fwd, target.forward)
            return a > reachRadians ? "Aim at the object" : "Hold steady…"
        }
        return "Move " + parts.joined(separator: " + ")
    }
}

/// Unsigned angle (radians) between two unit vectors.
func angleBetween(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Float {
    acos(min(max(simd_dot(a, b), -1), 1))
}
