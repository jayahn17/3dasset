//
//  UnsupportedDeviceView.swift
//  CrateScanner
//
//  Shown when the device has no LiDAR scanner. We fail gracefully with a clear
//  explanation rather than letting ARKit crash or silently do nothing.
//

import SwiftUI

struct UnsupportedDeviceView: View {
    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "arkit")
                .font(.system(size: 64))
                .foregroundStyle(.secondary)

            Text("LiDAR Required")
                .font(.title2.bold())

            Text(LiDARAvailability.unsupportedReason)
                .font(.body)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }
}

#Preview {
    UnsupportedDeviceView()
}
