//
//  ScanFeedback.swift
//  CrateScanner
//
//  The finite set of live guidance messages we surface during scanning, derived
//  from ARKit's tracking state + light estimate. Kept as an enum (not raw
//  strings) so the UI can also react to severity — e.g. tint warnings amber.
//

import SwiftUI

enum ScanFeedback: Equatable {
    /// Session warming up / (re)localizing.
    case initializing
    /// Tracking is good; keep moving around the object.
    case scanning
    /// Device is moving too fast for stable tracking.
    case moveSlower
    /// Poor lighting or featureless surfaces — LiDAR + tracking both suffer.
    case needsMoreLightOrDetail
    /// Tracking unavailable entirely (rare; usually transient).
    case trackingUnavailable
    /// Fit ran but found no geometry inside the placed box.
    case noGeometry
    /// Box is placed and tracking is healthy — ready to fit/capture.
    case fitReady

    /// The on-screen message.
    var message: String {
        switch self {
        case .initializing:            return "Move your device to start tracking…"
        case .scanning:                return "Scanning — move slowly around the machine"
        case .moveSlower:              return "Move slower"
        case .needsMoreLightOrDetail:  return "Needs more light"
        case .trackingUnavailable:     return "Tracking unavailable — hold still"
        case .noGeometry:              return "No surface found in the box — cover the object, then fit"
        case .fitReady:                return "Looks good — tap Fit, then Finish"
        }
    }

    /// Severity, so the overlay can color-code (green = good, amber = attention).
    enum Severity { case good, warning }

    var severity: Severity {
        switch self {
        case .scanning, .fitReady:
            return .good
        case .initializing, .moveSlower, .needsMoreLightOrDetail, .trackingUnavailable, .noGeometry:
            return .warning
        }
    }

    var tint: Color {
        switch severity {
        case .good:    return .green
        case .warning: return .orange
        }
    }
}
