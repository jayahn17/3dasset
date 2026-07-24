//
//  CrateScannerApp.swift
//  CrateScanner
//
//  App entry point. The very first thing we do is gate on LiDAR support: a
//  device without a LiDAR scanner can't run the scan at all, so we route it to
//  a clear explanation instead of a broken AR view.
//

import SwiftUI

@main
struct CrateScannerApp: App {
    var body: some Scene {
        WindowGroup {
            if LiDARAvailability.isSupported {
                ScanView()
            } else {
                UnsupportedDeviceView()
            }
        }
    }
}
