//
//  CrateScannerApp.swift
//  CrateScanner
//
//  App entry point. The very first thing we do is gate on LiDAR support: a
//  device without a LiDAR scanner can't run the scan at all, so we route it to
//  a clear explanation instead of a broken AR view.
//
//  On a supported device the intro walkthrough comes first, once, before the
//  camera opens — see IntroView.
//

import SwiftUI

@main
struct CrateScannerApp: App {
    var body: some Scene {
        WindowGroup {
            if LiDARAvailability.isSupported {
                RootView()
            } else {
                UnsupportedDeviceView()
            }
        }
    }
}

/// Intro → scan. The intro shows itself until the user taps through it, and the
/// stored revision means a future rewrite can show itself once more.
struct RootView: View {
    @AppStorage(CaptureSettings.introSeenKey) private var seenIntro = 0

    var body: some View {
        if seenIntro < CaptureSettings.introVersion {
            IntroView { seenIntro = CaptureSettings.introVersion }
        } else {
            ScanView()
        }
    }
}
