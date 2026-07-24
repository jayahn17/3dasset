//
//  ResultReviewView.swift
//  CrateScanner
//
//  Post-capture: orbit mesh, inch dims, crate padding, export STL/OBJ/USDZ,
//  and the Linux package (mesh + measurement.json + RGB-D session zip).
//

import SwiftUI
import UniformTypeIdentifiers

struct ResultReviewView: View {
    @ObservedObject var viewModel: ScanViewModel

    @State private var shareURL: URL?
    @State private var exportError: String?

    // Auto-save destination (a Google Drive / Files folder chosen once).
    @State private var destinationName: String? = DestinationStore.shared.destinationName
    @State private var autoSaveOn: Bool = DestinationStore.shared.autoSaveEnabled
    @State private var showFolderPicker = false
    @State private var autoSaveState: AutoSaveState = .idle
    @State private var didAutoSave = false

    enum AutoSaveState: Equatable {
        case idle, saving
        case saved(String)
        case failed(String)
    }

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
        .fileImporter(isPresented: $showFolderPicker,
                      allowedContentTypes: [.folder],
                      allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls):
                guard let folder = urls.first else { return }
                do {
                    try DestinationStore.shared.setDestination(folder)
                    destinationName = DestinationStore.shared.destinationName
                    autoSaveState = .idle
                    // Just chose a folder — save this scan into it now.
                    Task { await runAutoSave() }
                } catch {
                    exportError = error.localizedDescription
                }
            case .failure(let error):
                exportError = error.localizedDescription
            }
        }
        .alert("Export failed",
               isPresented: Binding(
                get: { exportError != nil },
                set: { if !$0 { exportError = nil } }
               ),
               actions: { Button("OK") { exportError = nil } },
               message: { Text(exportError ?? "") })
        .task {
            // Auto-save once when the review appears, if a folder is set.
            guard !didAutoSave, autoSaveOn,
                  DestinationStore.shared.hasDestination,
                  viewModel.lastSessionURL != nil else { return }
            didAutoSave = true
            await runAutoSave()
        }
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

            Text("Auto-save to Google Drive")
                .font(.caption).foregroundStyle(.secondary)

            autoSaveSection

            // Manual fallback: AirDrop / any other destination via the share
            // sheet. Always available even when a folder is set.
            Button {
                exportLinuxPackage()
            } label: {
                Label("Share elsewhere (AirDrop…)", systemImage: "square.and.arrow.up")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)

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

    // MARK: Auto-save

    @ViewBuilder
    private var autoSaveSection: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: autoSaveIcon)
                .foregroundStyle(autoSaveTint)
            VStack(alignment: .leading, spacing: 2) {
                if let name = destinationName {
                    Text("Saves to \(name)").font(.caption)
                    Text(autoSaveStatusLine)
                        .font(.caption2).foregroundStyle(.tertiary)
                } else {
                    Text("Choose a folder once — every scan saves there").font(.caption)
                    Text("Pick a Google Drive folder in Files; it uploads on its own")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
            Spacer()
            Button(destinationName == nil ? "Choose" : "Change") {
                showFolderPicker = true
            }
            .font(.caption)
        }

        if destinationName != nil {
            Toggle("Auto-save each scan", isOn: $autoSaveOn)
                .font(.caption)
                .onChange(of: autoSaveOn) { _, on in
                    DestinationStore.shared.autoSaveEnabled = on
                }

            Button {
                Task { await runAutoSave() }
            } label: {
                Label(autoSaveState == .saving ? "Saving…" : "Save to folder now",
                      systemImage: "arrow.up.doc")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(autoSaveState == .saving || viewModel.lastSessionURL == nil)
        }
    }

    private var autoSaveIcon: String {
        switch autoSaveState {
        case .saved: return "checkmark.icloud.fill"
        case .failed: return "exclamationmark.icloud"
        case .saving: return "icloud.and.arrow.up"
        case .idle: return destinationName == nil ? "folder.badge.plus" : "icloud"
        }
    }

    private var autoSaveTint: Color {
        switch autoSaveState {
        case .saved: return .green
        case .failed: return .orange
        default: return .secondary
        }
    }

    private var autoSaveStatusLine: String {
        switch autoSaveState {
        case .idle: return autoSaveOn ? "Auto-save on" : "Auto-save off"
        case .saving: return "Saving… Drive will upload it"
        case .saved: return "Saved ✓ — Drive syncs it to Linux"
        case .failed(let why): return why
        }
    }

    @MainActor
    private func runAutoSave() async {
        autoSaveState = .saving
        do {
            let name = try await viewModel.exportAndAutoSave()
            autoSaveState = .saved(name)
            destinationName = DestinationStore.shared.destinationName
        } catch {
            autoSaveState = .failed(error.localizedDescription)
        }
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
}

extension URL: Identifiable {
    public var id: String { absoluteString }
}
