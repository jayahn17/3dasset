//
//  AABB.swift
//  CrateScanner
//
//  A tiny axis-aligned bounding box value type plus the point-cloud math the
//  scanner uses to fit a box to the LiDAR mesh vertices inside the user's
//  placed region.
//
//  Kept framework-free (pure SIMD) so it's trivial to unit-test off-device.
//

import simd

/// An axis-aligned bounding box in world space, expressed as min/max corners.
struct AABB: Equatable {

    var min: SIMD3<Float>
    var max: SIMD3<Float>

    /// Center point of the box.
    var center: SIMD3<Float> { (min + max) * 0.5 }

    /// Full edge lengths along x/y/z (always non-negative).
    var extents: SIMD3<Float> { max - min }

    /// Half edge lengths — handy for RealityKit entity sizing.
    var halfExtents: SIMD3<Float> { extents * 0.5 }

    /// Is this box degenerate (no volume)? Used to guard empty scans.
    var isEmpty: Bool { extents.x <= 0 || extents.y <= 0 || extents.z <= 0 }

    /// Build an AABB from a center and full extents.
    init(center: SIMD3<Float>, extents: SIMD3<Float>) {
        let h = extents * 0.5
        self.min = center - h
        self.max = center + h
    }

    /// Build directly from corners.
    init(min: SIMD3<Float>, max: SIMD3<Float>) {
        self.min = min
        self.max = max
    }

    /// Does this box contain `point` (inclusive)?
    func contains(_ p: SIMD3<Float>) -> Bool {
        p.x >= min.x && p.x <= max.x &&
        p.y >= min.y && p.y <= max.y &&
        p.z >= min.z && p.z <= max.z
    }

    /// The tight AABB of a set of points. Returns `nil` for an empty set.
    ///
    /// This is the core dimensional-extraction routine: give it the LiDAR
    /// vertices that fall inside the user's placed box and it returns the
    /// smallest world-axis-aligned box that encloses them.
    static func fitting(_ points: [SIMD3<Float>]) -> AABB? {
        guard let first = points.first else { return nil }
        var lo = first
        var hi = first
        for p in points.dropFirst() {
            lo = simd_min(lo, p)
            hi = simd_max(hi, p)
        }
        return AABB(min: lo, max: hi)
    }
}
