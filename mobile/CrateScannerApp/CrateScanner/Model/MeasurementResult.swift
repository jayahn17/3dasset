//
//  MeasurementResult.swift
//  CrateScanner
//
//  The final, backend-ready description of a scanned machine.
//
//  Everything the client's crating team cares about lives here: the raw
//  measured dimensions, the crating buffer that gets added on, and enough
//  metadata (session id + timestamp) to trace a measurement back to a scan.
//
//  This type is `Codable` on purpose — the exact same struct that drives the
//  on-screen result panel is what we'll POST to the Node.js backend later.
//  For the MVP there is no backend, but keeping the shape final now means the
//  network layer is a drop-in, not a rewrite.
//

import Foundation

/// A single completed measurement of a machine, in crating units (inches).
///
/// Convention note on padding: `paddingInches` is applied **per side**. A box
/// that measures 40" long with a 2" buffer becomes 44" (2" added at each end).
/// This matches how a crate is actually built — clearance on every face — and
/// is the interpretation the client asked for ("+2 inches on all sides").
struct MeasurementResult: Codable, Identifiable, Equatable {

    /// Meters → inches. 1 m == 39.3700787 in.
    static let inchesPerMeter: Double = 39.3700787

    /// Stable id for this measurement (also used as the scan/session id in the payload).
    let id: UUID

    /// When the measurement was finalized.
    let timestamp: Date

    // MARK: Raw measured object dimensions (inches, no padding)

    let rawLengthInches: Double
    let rawWidthInches: Double
    let rawHeightInches: Double

    /// Crating clearance added to **each** side, in inches. Default 2.0.
    let paddingInches: Double

    // MARK: Derived crate dimensions (raw + padding on both sides)

    /// Outer crate length = raw + 2 × padding (one buffer per end).
    var crateLengthInches: Double { rawLengthInches + 2 * paddingInches }
    var crateWidthInches: Double { rawWidthInches + 2 * paddingInches }
    var crateHeightInches: Double { rawHeightInches + 2 * paddingInches }

    // MARK: Convenience initializer from meters

    /// Build a result directly from the AABB extents (in meters) produced by the
    /// scanner. Extents are the full edge lengths of the box, not half-extents.
    ///
    /// - Parameters:
    ///   - extentsMeters: (x = width, y = height, z = length) full edge lengths.
    ///   - padding: per-side crating buffer in inches (defaults to 2.0).
    ///   - id: session id; a fresh one is generated if omitted.
    ///   - timestamp: capture time; defaults to now.
    init(extentsMeters: SIMD3<Float>,
         padding: Double = 2.0,
         id: UUID = UUID(),
         timestamp: Date = Date()) {

        // Map the AR axes to crating semantics. In ARKit world space:
        //   x → width, y → height (up), z → length (depth).
        // The AABB is axis-aligned to the *world*, so this mapping is stable.
        self.id = id
        self.timestamp = timestamp
        self.rawWidthInches  = Double(extentsMeters.x) * Self.inchesPerMeter
        self.rawHeightInches = Double(extentsMeters.y) * Self.inchesPerMeter
        self.rawLengthInches = Double(extentsMeters.z) * Self.inchesPerMeter
        self.paddingInches = padding
    }

    // MARK: Display helpers (decimal inches, per the agreed format)

    /// Format a value as decimal inches, e.g. 44.25 → "44.25 in".
    /// Two decimals is plenty given the sensor's real precision, and it reads
    /// cleanly against the client's tolerance spec.
    static func format(_ inches: Double) -> String {
        String(format: "%.2f in", inches)
    }

    /// "L × W × H" of the raw object, decimal inches.
    var rawSummary: String {
        "\(Self.format(rawLengthInches)) × \(Self.format(rawWidthInches)) × \(Self.format(rawHeightInches))"
    }

    /// "L × W × H" of the outer crate (raw + padding), decimal inches.
    var crateSummary: String {
        "\(Self.format(crateLengthInches)) × \(Self.format(crateWidthInches)) × \(Self.format(crateHeightInches))"
    }

    /// Produce a copy with a different padding value. The UI slider uses this so
    /// the client can watch the crate size change live without re-scanning.
    func withPadding(_ padding: Double) -> MeasurementResult {
        MeasurementResult(rawLengthInches: rawLengthInches,
                          rawWidthInches: rawWidthInches,
                          rawHeightInches: rawHeightInches,
                          paddingInches: padding,
                          id: id,
                          timestamp: timestamp)
    }

    /// Memberwise-style init used by `withPadding` and decoding.
    init(rawLengthInches: Double,
         rawWidthInches: Double,
         rawHeightInches: Double,
         paddingInches: Double,
         id: UUID,
         timestamp: Date) {
        self.rawLengthInches = rawLengthInches
        self.rawWidthInches = rawWidthInches
        self.rawHeightInches = rawHeightInches
        self.paddingInches = paddingInches
        self.id = id
        self.timestamp = timestamp
    }
}
