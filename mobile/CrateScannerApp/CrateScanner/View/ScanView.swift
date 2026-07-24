//
//  ScanView.swift
//  CrateScanner
//
//  The main scanning screen. Hosts the live AR view, the guidance banner, and
//  the stage-appropriate controls (place box → fit → capture). When the user
//  captures, we hand off to ResultReviewView.
//
//  This view owns the single ScanViewModel for the whole flow.
//

import SwiftUI

struct ScanView: View {
    @StateObject private var viewModel = ScanViewModel()

    var body: some View {
        Group {
            if viewModel.phase == .reviewing {
                ResultReviewView(viewModel: viewModel)
            } else {
                scanningContent
            }
        }
    }

    // MARK: Scanning stage

    private var scanningContent: some View {
        ZStack {
            ARViewContainer(viewModel: viewModel)
                .ignoresSafeArea()

            // Center reticle while aiming (pre-start, and before a box).
            if viewModel.phase == .ready || !viewModel.isBoxPlaced {
                Image(systemName: "plus")
                    .font(.system(size: 28, weight: .thin))
                    .foregroundStyle(.white.opacity(0.8))
            }

            VStack {
                feedbackBanner
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

    // MARK: Pre-start

    private var startControls: some View {
        VStack(spacing: 12) {
            Text("Aim at the object, then start. Orbit it slowly so every side is seen.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button {
                viewModel.start()
            } label: {
                Label("Start Scan", systemImage: "record.circle")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .tint(.red)
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
            if viewModel.capturedVertexCount > 0 {
                Text("\(viewModel.capturedVertexCount) pts")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            if viewModel.rgbdFrameCount > 0 {
                Text("· \(viewModel.rgbdFrameCount) RGB-D")
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

            // Finish is always available: the RGB-D session is the deliverable
            // and Linux builds the asset (and the crate) from it. The ghost box
            // is an optional on-device measuring aid, not a step in the flow.
            if !viewModel.isBoxPlaced {
                Text("Orbit the object slowly until coverage looks complete")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 12) {
                Button {
                    viewModel.isBoxPlaced ? viewModel.fitToObject() : viewModel.placeBox()
                } label: {
                    Label(viewModel.isBoxPlaced ? "Fit to Object" : "Measure (optional)",
                          systemImage: viewModel.isBoxPlaced ? "scope" : "cube")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)

                Button {
                    viewModel.capture()
                } label: {
                    Label("Finish Scan", systemImage: "checkmark.circle")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(viewModel.rgbdFrameCount == 0)
            }

            Button("Reset", role: .destructive) {
                viewModel.reset()
            }
            .font(.footnote)
        }
    }
}

#Preview {
    ScanView()
}
