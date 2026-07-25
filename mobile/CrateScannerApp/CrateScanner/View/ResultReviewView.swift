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

    // Tailscale worker (the automatic iPad → Linux path).
    @State private var workerURL: String = WorkerSettings.shared.url
    @State private var autoSendOn: Bool = WorkerSettings.shared.autoSend
    @State private var sendState: SendState = .idle
    @State private var didAutoSend = false

    enum SendState: Equatable {
        case idle, sending
        case sent(String)
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
        .alert("Export failed",
               isPresented: Binding(
                get: { exportError != nil },
                set: { if !$0 { exportError = nil } }
               ),
               actions: { Button("OK") { exportError = nil } },
               message: { Text(exportError ?? "") })
        .task {
            // Auto-send once when review appears, if a worker URL is set.
            guard !didAutoSend, autoSendOn,
                  WorkerSettings.shared.isConfigured,
                  viewModel.lastSessionURL != nil else { return }
            didAutoSend = true
            await runSend()
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

            Text("Send to Linux (Tailscale worker)")
                .font(.caption).foregroundStyle(.secondary)

            workerSection

            // Manual fallback: AirDrop, or Save to Google Drive / Files via the
            // share sheet (Drive's share extension works even though its folder
            // picker doesn't).
            Button {
                exportLinuxPackage()
            } label: {
                Label("Share elsewhere (AirDrop / Drive…)", systemImage: "square.and.arrow.up")
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

    // MARK: Send to worker

    @ViewBuilder
    private var workerSection: some View {
        // Worker address — entered once, persisted.
        HStack(spacing: 8) {
            Image(systemName: "network")
                .foregroundStyle(.secondary)
            TextField("http://100.x.y.z:8090", text: $workerURL)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .font(.caption.monospaced())
                .onChange(of: workerURL) { _, new in
                    WorkerSettings.shared.url = new
                }
        }

        HStack(spacing: 8) {
            Image(systemName: sendIcon).foregroundStyle(sendTint)
            Text(sendStatusLine)
                .font(.caption2).foregroundStyle(.secondary)
            Spacer()
        }

        Toggle("Auto-send each scan", isOn: $autoSendOn)
            .font(.caption)
            .onChange(of: autoSendOn) { _, on in WorkerSettings.shared.autoSend = on }

        Button {
            Task { await runSend() }
        } label: {
            Label(sendState == .sending ? "Sending…" : "Send to Linux now",
                  systemImage: "arrow.up.circle")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .disabled(sendState == .sending
                  || viewModel.lastSessionURL == nil
                  || workerURL.trimmingCharacters(in: .whitespaces).isEmpty)
    }

    private var sendIcon: String {
        switch sendState {
        case .sent:    return "checkmark.circle.fill"
        case .failed:  return "exclamationmark.triangle.fill"
        case .sending: return "arrow.up.circle"
        case .idle:    return "wifi"
        }
    }

    private var sendTint: Color {
        switch sendState {
        case .sent:   return .green
        case .failed: return .orange
        default:      return .secondary
        }
    }

    private var sendStatusLine: String {
        switch sendState {
        case .idle:    return autoSendOn ? "Auto-send on · fuses on the 4080" : "Auto-send off"
        case .sending: return "Uploading to the worker…"
        case .sent(let name): return "Sent ✓ \(name) — the watcher is fusing it"
        case .failed(let why): return why
        }
    }

    @MainActor
    private func runSend() async {
        sendState = .sending
        do {
            let result = try await viewModel.exportAndSendToWorker()
            sendState = .sent(result.name)
        } catch {
            sendState = .failed(error.localizedDescription)
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
