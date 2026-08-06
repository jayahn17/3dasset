//
//  MoveNudge.swift
//  CrateScanner
//
//  The most common bad scan is not a shaky one — it's a *stationary* one. People
//  aim at the object, press Start, and then stand still, so every frame sees the
//  same side from the same angle. Fusion needs parallax: without it there is no
//  new geometry to add, and the far side of the object is simply never observed.
//
//  So we watch the camera's own motion. If it hasn't gone anywhere for a couple
//  of seconds while recording, we work out which way the user should walk to keep
//  orbiting the object, and hand that back as a direction the UI can show as a
//  banner *and* as a floating arrow in the AR scene.
//
//  Pure geometry + a short history buffer, deliberately free of ARKit types so
//  the decision ("is this person stuck, and which way should they go?") can be
//  reasoned about on its own. The rendering lives in ScanViewModel.
//

import Foundation
import simd

/// A live "you've stopped — go this way" instruction.
struct MoveHint: Equatable {
    /// Short imperative for the banner, e.g. "Step left around the object".
    let text: String
    /// SF Symbol matching the direction, for the banner's arrow.
    let symbol: String
    /// Unit vector in world space — where the AR arrow should point.
    let worldDirection: SIMD3<Float>
}

/// Tracks recent camera motion and decides when to nudge.
///
/// Main-actor only (it's driven from the frame callback's hop to the main actor
/// alongside the rest of the published state), so no locking here.
final class MoveNudge {

    // MARK: Tunables

    /// How far back to look when asking "have they moved?".
    private let window: TimeInterval = 2.0
    /// Less translation than this over the window counts as standing still.
    private let stallMeters: Float = 0.18
    /// …unless they at least panned the camera this much, which still yields
    /// some new coverage and shouldn't be nagged.
    private let stallRadians: Float = 0.26        // ~15°
    /// Once nudging, keep the same direction until they've actually moved this
    /// far. Prevents the arrow flipping left/right while they shuffle in place.
    private let clearMeters: Float = 0.30

    // MARK: State

    private struct Sample {
        let time: TimeInterval
        let position: SIMD3<Float>
        let forward: SIMD3<Float>
    }

    private var samples: [Sample] = []
    /// The direction they were last seen travelling — we ask them to keep going
    /// that way rather than doubling back over ground they've already covered.
    private var lastTravel: SIMD3<Float>?
    /// Sticky hint + where they were standing when it appeared.
    private var activeHint: MoveHint?
    private var hintOrigin: SIMD3<Float>?

    // MARK: Evaluation

    /// Feed one frame's camera pose.
    ///
    /// - Parameters:
    ///   - pivot: what they're scanning (the placed box's centre). When nil we
    ///     assume the object sits about a metre ahead of the camera.
    ///   - isRecording: no nagging before Start or after Finish.
    /// - Returns: the hint to show, or nil when they're moving fine.
    func evaluate(time: TimeInterval,
                  cameraTransform m: simd_float4x4,
                  pivot: SIMD3<Float>?,
                  isRecording: Bool) -> MoveHint? {
        guard isRecording else { reset(); return nil }

        let pos = m.translation
        let fwd = simd_normalize(-m.forward)
        samples.append(Sample(time: time, position: pos, forward: fwd))
        // Keep a little more than the window so `oldest` is a full window back.
        samples.removeAll { time - $0.time > window * 1.5 }

        // A sticky hint clears only once they've genuinely walked away from
        // where they were standing when it appeared.
        if let hint = activeHint, let origin = hintOrigin {
            if simd_distance(pos, origin) >= clearMeters {
                activeHint = nil
                hintOrigin = nil
                lastTravel = simd_normalize(pos - origin)
            } else {
                return hint
            }
        }

        // Need a full window of history before judging.
        guard let oldest = samples.first, time - oldest.time >= window else { return nil }

        let travelled = pos - oldest.position
        let distance = simd_length(travelled)
        let turned = angleBetween(fwd, oldest.forward)

        guard distance < stallMeters && turned < stallRadians else {
            // Moving fine. Remember the heading so the next nudge continues it.
            if distance > 0.05 { lastTravel = simd_normalize(travelled) }
            return nil
        }

        let centre = pivot ?? (pos + fwd * 1.2)
        let direction = Self.orbitDirection(cameraPosition: pos,
                                            pivot: centre,
                                            continuing: lastTravel)
        let hint = Self.describe(direction, cameraTransform: m)
        activeHint = hint
        hintOrigin = pos
        return hint
    }

    func reset() {
        samples.removeAll()
        lastTravel = nil
        activeHint = nil
        hintOrigin = nil
    }

    // MARK: Geometry

    /// A horizontal direction that walks the camera *around* the pivot rather
    /// than into or away from it — the tangent of the orbit circle.
    ///
    /// Two tangents exist (clockwise and counter-clockwise); we pick whichever
    /// agrees with the direction they were already heading, so a scan reads as
    /// one continuous lap instead of a back-and-forth shuffle.
    static func orbitDirection(cameraPosition pos: SIMD3<Float>,
                               pivot: SIMD3<Float>,
                               continuing lastTravel: SIMD3<Float>?) -> SIMD3<Float> {
        let up = SIMD3<Float>(0, 1, 0)
        var radial = pos - pivot
        radial.y = 0

        // Standing (nearly) on top of the pivot leaves the tangent undefined —
        // any horizontal direction is as good as another.
        guard simd_length(radial) > 0.05 else { return SIMD3<Float>(1, 0, 0) }

        let tangent = simd_normalize(simd_cross(up, simd_normalize(radial)))
        if let travel = lastTravel {
            var flat = travel
            flat.y = 0
            if simd_length(flat) > 0.01, simd_dot(simd_normalize(flat), tangent) < 0 {
                return -tangent
            }
        }
        return tangent
    }

    /// Turn a world direction into words + an arrow, in the camera's own frame,
    /// so "left" means the user's left rather than the world's.
    static func describe(_ direction: SIMD3<Float>,
                         cameraTransform m: simd_float4x4) -> MoveHint {
        let right = simd_normalize(SIMD3<Float>(m.columns.0.x, 0, m.columns.0.z))
        let forward = simd_normalize(SIMD3<Float>(-m.columns.2.x, 0, -m.columns.2.z))

        let lateral = simd_dot(direction, right)
        let axial = simd_dot(direction, forward)

        let text: String
        let symbol: String
        if abs(lateral) >= abs(axial) {
            text = lateral >= 0 ? "Step right around the object"
                                : "Step left around the object"
            symbol = lateral >= 0 ? "arrow.right" : "arrow.left"
        } else {
            text = axial >= 0 ? "Walk forward around the object"
                              : "Step back around the object"
            symbol = axial >= 0 ? "arrow.up" : "arrow.down"
        }
        return MoveHint(text: text, symbol: symbol, worldDirection: simd_normalize(direction))
    }
}
