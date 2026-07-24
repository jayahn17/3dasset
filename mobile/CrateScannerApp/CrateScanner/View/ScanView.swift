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

            // Center reticle while aiming to place the box.
            if !viewModel.isBoxPlaced {
                Image(systemName: "plus")
                    .font(.system(size: 28, weight: .thin))
                    .foregroundStyle(.white.opacity(0.8))
            }

            VStack {
                feedbackBanner
                Spacer()
                controls
            }
            .padding()
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

            if !viewModel.isBoxPlaced {
                Text("Aim at the machine, then place the box around it")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button {
                    viewModel.placeBox()
                } label: {
                    Label("Place Box", systemImage: "cube")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            } else {
                HStack(spacing: 12) {
                    Button {
                        viewModel.fitToObject()
                    } label: {
                        Label("Fit to Object", systemImage: "scope")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)

                    Button {
                        viewModel.capture()
                    } label: {
                        Label("Capture", systemImage: "camera.metering.matrix")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                }

                Button("Reset", role: .destructive) {
                    viewModel.reset()
                }
                .font(.footnote)
            }
        }
    }
}

#Preview {
    ScanView()
}
