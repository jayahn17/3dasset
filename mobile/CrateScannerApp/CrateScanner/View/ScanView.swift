//
//  ScanView.swift
//  CrateScanner
//
//  The main scanning screen. Hosts the live AR view, the guidance banners, and
//  the stage-appropriate controls (start → scan → finish). When the user
//  finishes, we hand off to ResultReviewView.
//
//  Interaction rule for the whole app: everything is a tap. This gets used
//  one-handed while walking around a machine, often in gloves, so there are no
//  sliders, no segmented controls to drag, and no gestures on the camera view
//  beyond a single tap. Options are cards you press; the choice you made carries
//  a checkmark.
//
//  This view owns the single ScanViewModel for the whole flow.
//

import SwiftUI

struct ScanView: View {
    @StateObject private var viewModel = ScanViewModel()
    @State private var showingIntro = false

    var body: some View {
        Group {
            if viewModel.phase == .reviewing {
                ResultReviewView(viewModel: viewModel)
            } else {
                scanningContent
            }
        }
        .sheet(isPresented: $showingIntro) {
            IntroView(onStart: { showingIntro = false }, isReturning: true)
        }
    }

    // MARK: Scanning stage

    private var scanningContent: some View {
        ZStack {
            ARViewContainer(viewModel: viewModel)
                .ignoresSafeArea()

            // Focus reticle: a ring that turns green when the frame is sharp and
            // steady enough to capture without blur.
            Circle()
                .strokeBorder(reticleColor, lineWidth: 2.5)
                .frame(width: 64, height: 64)
                .overlay(Image(systemName: viewModel.readiness == .ready ? "checkmark" : "camera.metering.center.weighted")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(reticleColor))
                .opacity(0.9)

            VStack(spacing: 10) {
                HStack(alignment: .top, spacing: 10) {
                    feedbackBanner
                    helpButton
                }
                if let hint = viewModel.moveHint {
                    moveBanner(hint)
                }
                if viewModel.phase == .scanning && viewModel.guidedEnabled {
                    guidedBanner
                }
                Spacer()
                if viewModel.phase == .ready {
                    startControls
                } else {
                    controls
                }
            }
            .padding()
        }
    }

    private var helpButton: some View {
        Button {
            showingIntro = true
        } label: {
            Image(systemName: "questionmark")
                .font(.system(size: 15, weight: .semibold))
                .frame(width: 40, height: 40)
                .background(.ultraThinMaterial, in: Circle())
        }
        .accessibilityLabel("How to scan")
    }

    // MARK: Pre-start

    private var startControls: some View {
        VStack(spacing: 14) {
            // Capture options — chosen before Start, because they configure the
            // AR session's video format.
            VStack(spacing: 8) {
                ForEach(CaptureMode.allCases) { mode in
                    ModeCard(mode: mode, isSelected: viewModel.mode == mode) {
                        viewModel.mode = mode
                    }
                }
            }

            qualityRow
            guidedRow

            Button {
                viewModel.start()
            } label: {
                Label("Start Scan", systemImage: "record.circle")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .tint(.red)
        }
    }

    /// Colour resolution, as four tap targets. Was a segmented picker, which on
    /// a held-up iPad is a drag more often than a tap.
    private var qualityRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Detail level")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            HStack(spacing: 8) {
                ForEach(CaptureQuality.allCases) { quality in
                    ChipButton(title: quality.label,
                               isSelected: viewModel.quality == quality) {
                        viewModel.quality = quality
                    }
                }
            }
            Text(viewModel.quality.caption)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(12)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    /// Guided capture as a whole-row tap target rather than a switch you have to
    /// hit exactly.
    private var guidedRow: some View {
        Button {
            viewModel.guidedEnabled.toggle()
        } label: {
            HStack(spacing: 12) {
                Image(systemName: viewModel.guidedEnabled ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 22))
                    .foregroundStyle(viewModel.guidedEnabled ? Color.accentColor : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Guided capture")
                        .font(.subheadline.weight(.medium))
                    Text("Shows target viewpoints and walks you to each")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var guidedBanner: some View {
        HStack(spacing: 8) {
            Image(systemName: "figure.walk")
                .foregroundStyle(.yellow)
            Text(viewModel.guidanceText.isEmpty ? "Walk to the highlighted marker" : viewModel.guidanceText)
                .font(.subheadline.weight(.semibold))
            Spacer()
            Text("\(viewModel.guidedDone)/\(viewModel.guidedTotal)")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    /// "You've stopped — go this way." The matching arrow is drawn in the AR
    /// scene itself; this is the same instruction in words, for the moment the
    /// user is looking at the controls rather than through the camera.
    private func moveBanner(_ hint: MoveHint) -> some View {
        HStack(spacing: 10) {
            Image(systemName: hint.symbol)
                .font(.system(size: 20, weight: .bold))
            Text(hint.text)
                .font(.subheadline.weight(.semibold))
            Spacer()
        }
        .foregroundStyle(.black)
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Color.yellow, in: RoundedRectangle(cornerRadius: 12))
        .transition(.move(edge: .top).combined(with: .opacity))
        .animation(.easeOut(duration: 0.2), value: hint)
    }

    private var shutterLabel: String {
        if viewModel.isCapturingPhoto { return "Capturing…" }
        switch viewModel.readiness {
        case .ready:    return "Take Photo"
        case .moving:   return "Hold steady…"
        case .focusing: return "Focusing…"
        }
    }

    private var hybridHint: String {
        switch viewModel.readiness {
        case .ready:    return "Sharp — capturing keyframe"
        case .moving:   return "Pause a beat to capture · \(viewModel.keyframeCount) so far"
        case .focusing: return "Focusing… move closer / add light · \(viewModel.keyframeCount) so far"
        }
    }

    private var reticleColor: Color {
        switch viewModel.readiness {
        case .ready:    return .green
        case .moving:   return .yellow
        case .focusing: return .white.opacity(0.7)
        }
    }

    // MARK: Guidance banner

    private var feedbackBanner: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(viewModel.feedback.tint)
                .frame(width: 10, height: 10)
            Text(viewModel.feedback.message)
                .font(.subheadline.weight(.medium))
            Spacer()
            if viewModel.rgbdFrameCount > 0 {
                Text("\(viewModel.rgbdFrameCount)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            if viewModel.keyframeCount > 0 {
                Text("· \(viewModel.keyframeCount) photos")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: Stage controls

    @ViewBuilder
    private var controls: some View {
        VStack(spacing: 12) {
            // Live dimension preview once a fit has produced numbers.
            if let result = viewModel.result {
                HStack {
                    Text("Object")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Text(result.rawSummary)
                        .font(.callout.monospacedDigit().weight(.semibold))
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
            }

            // Photo mode: a shutter for each high-res still, tinted by readiness
            // so the user waits for a sharp, steady frame.
            if viewModel.mode == .photo {
                Button {
                    viewModel.capturePhoto()
                } label: {
                    Label(shutterLabel, systemImage: "camera.shutter.button")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 4)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(viewModel.readiness == .ready ? .green : .gray)
                .disabled(viewModel.isCapturingPhoto)
            }

            // Detail mode: keyframes fire automatically when sharp + steady.
            if viewModel.mode == .hybrid {
                HStack(spacing: 6) {
                    Image(systemName: viewModel.readiness == .ready ? "checkmark.circle.fill" : "camera.metering.center.weighted")
                        .foregroundStyle(viewModel.readiness == .ready ? .green : .secondary)
                    Text(hybridHint)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            // Finish is the one primary action: the RGB-D session is the
            // deliverable and Linux builds the asset (and the crate) from it. The
            // ghost box is an optional on-device measuring aid, not a step.
            Button {
                viewModel.capture()
            } label: {
                Label("Finish Scan", systemImage: "checkmark.circle.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            // Photo mode has no RGB-D stream, only keyframes — accept either.
            .disabled(viewModel.rgbdFrameCount == 0 && viewModel.keyframeCount == 0)

            // Everything optional lives on one quiet secondary row.
            HStack(spacing: 12) {
                Button {
                    viewModel.isBoxPlaced ? viewModel.fitToObject() : viewModel.placeBox()
                } label: {
                    Label(viewModel.isBoxPlaced ? "Fit" : "Measure",
                          systemImage: viewModel.isBoxPlaced ? "scope" : "cube")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                Button(role: .destructive) {
                    viewModel.reset()
                } label: {
                    Label("Start Over", systemImage: "arrow.counterclockwise")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
            .controlSize(.regular)

            if viewModel.isBoxPlaced {
                Text("Tap the floor to move the box")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

/// One capture mode as a full-width tap target: symbol, name, and the one line
/// that tells you when to pick it.
private struct ModeCard: View {
    let mode: CaptureMode
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: mode.symbol)
                    .font(.system(size: 20))
                    .frame(width: 28)
                    .foregroundStyle(isSelected ? Color.accentColor : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(mode.label)
                        .font(.subheadline.weight(.semibold))
                    Text(mode.caption)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 20))
                    .foregroundStyle(isSelected ? Color.accentColor : Color.secondary.opacity(0.5))
            }
            .padding(12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14)
            .stroke(Color.accentColor.opacity(isSelected ? 0.8 : 0), lineWidth: 2))
    }
}

#Preview {
    ScanView()
}
