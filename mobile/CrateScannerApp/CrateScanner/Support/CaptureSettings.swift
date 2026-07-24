//
//  CaptureSettings.swift
//  CrateScanner
//
//  User-facing capture options, chosen on the pre-Start screen:
//
//    • Quality  — how much colour resolution to keep (Fast … 4K)
//    • Mode     — Auto (continuous ~video-rate stream) or Photo (manual stills)
//
//  A note on what resolution buys you, since it's easy to over-invest:
//  nvblox fusion resizes colour down to the DEPTH resolution (256×192 on LiDAR
//  iPads), so higher colour does NOT sharpen the fused geometry. It matters for
//  TEXTURING the mesh afterwards (OpenMVS / TRELLIS on Linux). To make that
//  usable we also record native colour intrinsics + size in the manifest, so a
//  later texture pass has the high-res frames and the maths to project them.
//

import CoreGraphics
import Foundation

/// How much colour resolution to store per frame.
enum CaptureQuality: String, CaseIterable, Identifiable {
    case fast       // 640  — smallest upload; fusion-only
    case balanced   // 1280
    case high       // 1920 (≈ default ARKit video format)
    case max4K      // 4K video format / native still — best for texturing

    var id: String { rawValue }

    var label: String {
        switch self {
        case .fast:     return "Fast"
        case .balanced: return "Balanced"
        case .high:     return "High"
        case .max4K:    return "4K"
        }
    }

    /// Colour is downscaled to at most this width before JPEG. `.max4K` keeps
    /// whatever the sensor delivered (the 4K video format, or the full still).
    var maxColorWidth: CGFloat {
        switch self {
        case .fast:     return 640
        case .balanced: return 1280
        case .high:     return 1920
        case .max4K:    return .greatestFiniteMagnitude
        }
    }

    /// Seconds between saved frames in Auto mode. 4K frames are large, so we
    /// sample them a little slower; coverage matters more than raw rate.
    var autoInterval: TimeInterval {
        self == .max4K ? 0.2 : 0.1        // 5 Hz vs 10 Hz
    }

    /// Only 4K selects ARKit's dedicated 4K video format for the live stream.
    var wants4KVideoFormat: Bool { self == .max4K }

    var caption: String {
        switch self {
        case .fast:     return "640 px · smallest files · geometry only"
        case .balanced: return "1280 px · good balance"
        case .high:     return "1920 px · sharp colour"
        case .max4K:    return "4K · largest files · best for texturing"
        }
    }
}

/// How frames are captured.
enum CaptureMode: String, CaseIterable, Identifiable {
    case auto       // continuous stream while scanning
    case photo      // one high-resolution still per shutter tap
    case hybrid     // 10 Hz RGB-D stream + automatic ~1.5 Hz 12 MP keyframes

    var id: String { rawValue }

    var label: String {
        switch self {
        case .auto:   return "Auto"
        case .photo:  return "Photo"
        case .hybrid: return "Detail"
        }
    }

    var caption: String {
        switch self {
        case .auto:   return "Streams frames as you orbit"
        case .photo:  return "Tap the shutter for each high-res photo"
        case .hybrid: return "10 Hz depth + auto 12 MP keyframes (best quality)"
        }
    }

    /// Seconds between automatic 12 MP keyframes in Hybrid mode (~1.5 Hz → about
    /// 90–135 keyframes over a 60–90 s orbit, the 80–150 sweet spot for MVS).
    var keyframeInterval: TimeInterval { 0.67 }
}

/// Persisted capture preferences (survive app launches).
final class CaptureSettings {
    static let shared = CaptureSettings()
    private let defaults = UserDefaults.standard
    private init() {}

    var quality: CaptureQuality {
        get { CaptureQuality(rawValue: defaults.string(forKey: "capture.quality") ?? "") ?? .fast }
        set { defaults.set(newValue.rawValue, forKey: "capture.quality") }
    }

    var mode: CaptureMode {
        get { CaptureMode(rawValue: defaults.string(forKey: "capture.mode") ?? "") ?? .auto }
        set { defaults.set(newValue.rawValue, forKey: "capture.mode") }
    }
}
