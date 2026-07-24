//
//  LiDARAvailability.swift
//  CrateScanner
//
//  One place to answer the question "can this device actually do the scan?"
//
//  The whole app hinges on LiDAR scene reconstruction. Rather than let ARKit
//  fail opaquely at runtime, we check up front and route unsupported devices to
//  a friendly explanation screen.
//

import ARKit

enum LiDARAvailability {

    /// True only on devices with a LiDAR scanner that support the mesh
    /// reconstruction we rely on for measurement (iPhone 12 Pro+/iPad Pro '20+).
    static var isSupported: Bool {
        // `supportsSceneReconstruction(.mesh)` is the authoritative check — it's
        // true exactly on the LiDAR-equipped devices. It also implicitly covers
        // world tracking support.
        ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
    }

    /// Human-readable reason to show when unsupported, for the fallback screen.
    static var unsupportedReason: String {
        if !ARWorldTrackingConfiguration.isSupported {
            return "This device doesn't support the AR world tracking required to scan."
        }
        return "This device doesn't have a LiDAR scanner. CrateScanner needs LiDAR "
             + "(iPhone 12 Pro or later, or an iPad Pro from 2020 on) to measure machines accurately."
    }
}
