//
//  ResultReviewView.swift
//  CrateScanner
//
//  Post-capture: orbit mesh, inch dims, crate padding, export STL/OBJ/USDZ,
//  and the Linux package (mesh + measurement.json + RGB-D session zip).
//

import SwiftUI

struct ResultReviewView: View {
    @ObservedObject var viewModel: ScanViewModel

    @State private var shareURL: URL?
    @State private var exportError: String?

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .topLeading) {
                if let mesh = viewModel.capturedMesh {
                    MeshSceneView(mesh: mesh)
                        .background(Color(.secondarySystemBackground))
                } else {
                    Color(.secondarySystemBackground)
                }

                Text("Drag to rotate · pinch to zoom")
                    .font(.caption)
                    .padding(8)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(12)
            }
            .frame(maxHeight: .infinity)

            dimensionsPanel
        }
        .sheet(item: $shareURL) { url in
            ShareSheet(items: [url])
        }
        .alert("Export failed",
               isPresented: Binding(
                get: { exportError != nil },
                set: { if !$0 { exportError = nil } }
               ),
               actions: { Button("OK") { exportError = nil } },
               message: { Text(exportError ?? "") })
    }

    private var dimensionsPanel: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let result = viewModel.result {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Measured object")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(result.rawSummary)
                        .font(.title3.monospacedDigit().weight(.semibold))
                    Text("L × W × H")
                        .font(.caption2).foregroundStyle(.tertiary)
                }

                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Crate size")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Text("+\(result.paddingInches, specifier: "%.1f")\" / side")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text(result.crateSummary)
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(.tint)

                    Slider(value: $viewModel.paddingInches, in: 0...6, step: 0.5)
                }
            }

            // RGB-D status for Linux pipeline
            HStack {
                Image(systemName: viewModel.lastSessionURL == nil ? "exclamationmark.triangle" : "checkmark.circle.fill")
                    .foregroundStyle(viewModel.lastSessionURL == nil ? .orange : .green)
                Text(viewModel.lastSessionURL == nil
                      ? "No RGB-D session (depth missing?)"
                      : "RGB-D ready · \(viewModel.rgbdFrameCount) frames")
                    .font(.caption)
                Spacer()
            }

            Divider()

            Text("Share to Linux (assetpipe)")
                .font(.caption).foregroundStyle(.secondary)

            // Scans are kept in Documents/, so they survive app restarts and are
            // reachable from Files — drag one into Google Drive to sync to Linux.
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "folder")
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Saved to Files → On My iPad → CrateScanner")
                    Text("CrateScans/Packages · \(ScanStorage.totalSizeDescription) stored")
                        .foregroundStyle(.tertiary)
                }
                .font(.caption2)
                Spacer()
            }

            Button {
                exportLinuxPackage()
            } label: {
                Label("Share full package (mesh + RGB-D)", systemImage: "square.and.arrow.up")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)

            Button {
                exportSessionOnly()
            } label: {
                Label("Share RGB-D session only", systemImage: "photo.stack")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(viewModel.lastSessionURL == nil)

            Divider()

            Text("Export 3D file")
                .font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 12) {
                ForEach(MeshExportFormat.allCases) { format in
                    Button {
                        export(format)
                    } label: {
                        Text(format.displayName)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
            }

            HStack(spacing: 12) {
                Button("Keep Scanning") { viewModel.resumeScanning() }
                    .buttonStyle(.bordered)
                    .frame(maxWidth: .infinity)
                Button("New Scan") { viewModel.reset() }
                    .buttonStyle(.borderedProminent)
                    .frame(maxWidth: .infinity)
            }
        }
        .padding()
        .background(Color(.systemBackground))
    }

    private func export(_ format: MeshExportFormat) {
        do {
            shareURL = try viewModel.exportCapturedMesh(as: format)
        } catch {
            exportError = error.localizedDescription
        }
    }

    private func exportLinuxPackage() {
        do {
            shareURL = try viewModel.exportLinuxPackage()
        } catch {
            exportError = error.localizedDescription
        }
    }

    private func exportSessionOnly() {
        do {
            shareURL = try viewModel.exportSessionZip()
        } catch {
            exportError = error.localizedDescription
        }
    }
}

extension URL: Identifiable {
    public var id: String { absoluteString }
}
