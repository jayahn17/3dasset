//
//  IntroView.swift
//  CrateScanner
//
//  The first screen a new user meets. A scan is a physical performance — you
//  have to walk a full lap around the machine, slowly — and nothing on the AR
//  screen can teach that mid-scan. So it's taught here, in three steps, before
//  the camera ever opens.
//
//  Shown once (per intro revision) at launch, and reachable afterwards from the
//  ⓘ button on the scan screen. Every control is a tap; there is nothing to
//  swipe past.
//

import SwiftUI

struct IntroView: View {
    /// "Start scanning" / "Done" — dismisses the intro.
    var onStart: () -> Void
    /// Shown as a sheet from the scan screen rather than at first launch, which
    /// changes only the wording of the button.
    var isReturning = false

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(red: 0.05, green: 0.07, blue: 0.16),
                         Color(red: 0.09, green: 0.06, blue: 0.24)],
                startPoint: .topLeading, endPoint: .bottomTrailing)
                .ignoresSafeArea()

            ScrollView {
                VStack(spacing: 28) {
                    header
                    steps
                    startButton
                    Text("Needs an iPad Pro or iPhone Pro with a LiDAR scanner.")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.45))
                        .multilineTextAlignment(.center)
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 36)
                .frame(maxWidth: 540)
                .frame(maxWidth: .infinity)
            }
        }
    }

    // MARK: Pieces

    private var header: some View {
        VStack(spacing: 14) {
            CubeMark(size: 132)
                .padding(.bottom, 4)

            Text("CrateScanner")
                .font(.largeTitle.weight(.bold))
                .foregroundStyle(.white)

            Text("Walk around a machine.\nGet a 3D asset and its crate size.")
                .font(.body)
                .foregroundStyle(.white.opacity(0.7))
                .multilineTextAlignment(.center)
        }
    }

    private var steps: some View {
        VStack(spacing: 14) {
            StepRow(number: 1,
                    symbol: "viewfinder",
                    title: "Point at the object",
                    detail: "Stand back far enough that the whole machine fits on screen, then tap Start.")
            StepRow(number: 2,
                    symbol: "figure.walk.motion",
                    title: "Walk a full lap around it",
                    detail: "Slowly. If you stop moving, a yellow arrow appears in the camera view — follow it.")
            StepRow(number: 3,
                    symbol: "checkmark.circle.fill",
                    title: "Tap Finish",
                    detail: "The scan packages itself and uploads to your Linux worker or Google Drive.")
        }
    }

    private var startButton: some View {
        Button(action: onStart) {
            Text(isReturning ? "Back to scanning" : "Start scanning")
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(Color(red: 0.24, green: 0.62, blue: 1.0))
    }
}

/// One numbered step: a symbol, a headline, and the thing people get wrong.
private struct StepRow: View {
    let number: Int
    let symbol: String
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            ZStack {
                Circle()
                    .fill(Color.white.opacity(0.12))
                    .frame(width: 44, height: 44)
                Image(systemName: symbol)
                    .font(.system(size: 19, weight: .semibold))
                    .foregroundStyle(Color(red: 0.55, green: 0.85, blue: 1.0))
            }
            .overlay(alignment: .topLeading) {
                Text("\(number)")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.black)
                    .frame(width: 18, height: 18)
                    .background(Color(red: 0.55, green: 0.85, blue: 1.0), in: Circle())
                    .offset(x: -4, y: -4)
            }

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(.white)
                Text(detail)
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.62))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))
    }
}

#Preview {
    IntroView(onStart: {})
}
