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

    @State private var shareItem: ShareItem?
    @State private var exportError: String?

    // Tailscale worker (the automatic iPad → Linux path).
    @State private var workerURL: String = WorkerSettings.shared.url
    @State private var autoSendOn: Bool = WorkerSettings.shared.autoSend
    @State private var sendState: SendState = .idle
    @State private var didAutoSend = false

    // Google Drive
    @ObservedObject private var drive = GoogleDriveSync.shared
    @State private var driveState: SendState = .idle
    @State private var didAutoDrive = false
    @State private var clientIDField = GoogleDriveConfig.clientID
    @State private var driveConfigured = GoogleDriveConfig.isConfigured

    // KIRI Engine (photos → Photos album)
    @State private var kiriState: SendState = .idle

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
        .sheet(item: $shareItem) { item in
            ShareSheet(items: [item.url])
        }
        .alert("Export failed",
               isPresented: Binding(
                get: { exportError != nil },
                set: { if !$0 { exportError = nil } }
               ),
               actions: { Button("OK") { exportError = nil } },
               message: { Text(exportError ?? "") })
        .task {
            // Learn which Drive we're connected to BEFORE anything auto-uploads,
            // so a scan can't land in the wrong account while the screen still
            // says "Connected".
            if drive.isConnected, drive.connectedEmail == nil {
                await drive.refreshAccountEmail()
            }
            guard viewModel.lastSessionURL != nil else { return }
            // Auto-send to the worker if configured …
            if !didAutoSend, autoSendOn, WorkerSettings.shared.isConfigured {
                didAutoSend = true
                await runSend()
            }
            // … and/or auto-sync to Drive if connected. Both can run. Auto-sync
            // holds off when we're signed in as the wrong account — the manual
            // button is still there if that's actually intended.
            if !didAutoDrive, drive.isConnected, drive.autoSync,
               drive.isExpectedAccount, !drive.needsReconnect {
                didAutoDrive = true
                await runDrive()
            }
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

                    paddingChips
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

            Divider()

            Text("Send to KIRI Engine (photogrammetry)")
                .font(.caption).foregroundStyle(.secondary)
            kiriSection

            Divider()

            Text("Sync to Google Drive → \(drive.folderDisplayName ?? GoogleDriveConfig.folderName)")
                .font(.caption).foregroundStyle(.secondary)
            driveSection

            // Manual fallback: AirDrop, or Save to Files via the share sheet.
            Button {
                exportLinuxPackage()
            } label: {
                Label("Share elsewhere (AirDrop / Files…)", systemImage: "square.and.arrow.up")
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

    /// Crating buffer as tap targets instead of a slider. Half-inch precision on
    /// a slider was never real — these are the sizes anyone actually orders, and
    /// on a jobsite a tap beats a drag.
    private var paddingChips: some View {
        HStack(spacing: 8) {
            ForEach([0.0, 1.0, 2.0, 3.0, 4.0, 6.0], id: \.self) { inches in
                ChipButton(title: "\(Int(inches))\"",
                           isSelected: abs(viewModel.paddingInches - inches) < 0.01) {
                    viewModel.paddingInches = inches
                }
            }
        }
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

    // MARK: KIRI Engine

    @ViewBuilder
    private var kiriSection: some View {
        HStack(spacing: 8) {
            Image(systemName: kiriIcon).foregroundStyle(kiriTint)
            Text(kiriStatusLine).font(.caption2).foregroundStyle(.secondary)
            Spacer()
        }
        Button {
            Task { await runKiri() }
        } label: {
            Label(kiriState == .sending ? "Saving photos…" : "Save photos for KIRI",
                  systemImage: "photo.on.rectangle.angled")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .disabled(kiriState == .sending || viewModel.lastSessionURL == nil)
        Text("Then open KIRI Engine → Photo mode → import the “CrateScanner …” album.")
            .font(.caption2).foregroundStyle(.tertiary)
    }

    private var kiriIcon: String {
        switch kiriState {
        case .sent:   return "checkmark.circle.fill"
        case .failed: return "exclamationmark.triangle.fill"
        case .sending: return "photo.badge.arrow.down"
        case .idle:   return "cube.transparent"
        }
    }
    private var kiriTint: Color {
        switch kiriState {
        case .sent: return .green
        case .failed: return .orange
        default: return .secondary
        }
    }
    private var kiriStatusLine: String {
        switch kiriState {
        case .idle:    return "Saves this scan's high-res photos to a Photos album"
        case .sending: return "Saving to Photos…"
        case .sent(let s): return s
        case .failed(let why): return why
        }
    }

    @MainActor
    private func runKiri() async {
        kiriState = .sending
        do {
            let r = try await viewModel.exportPhotosForKiri()
            if let album = r.albumName {
                kiriState = .sent("\(r.count) photos → “\(album)” ✓")
            } else {
                // Add-only access: the photos are saved, just not grouped.
                kiriState = .sent("\(r.count) photos → Recents (allow full Photos access for an album)")
            }
        } catch {
            kiriState = .failed(error.localizedDescription)
        }
    }

    // MARK: Google Drive

    @ViewBuilder
    private var driveSection: some View {
        if !driveConfigured {
            // Paste the OAuth client ID once; stored on the device, no rebuild.
            VStack(alignment: .leading, spacing: 6) {
                TextField("iOS OAuth client ID (…apps.googleusercontent.com)",
                          text: $clientIDField)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.caption2.monospaced())
                Text("Create it in Google Cloud Console → Credentials → iOS OAuth client (Bundle ID com.jayahn.CrateScanner). Steps in GoogleDriveConfig.swift.")
                    .font(.caption2).foregroundStyle(.secondary)
                Button("Save client ID") {
                    GoogleDriveConfig.clientID = clientIDField
                    driveConfigured = GoogleDriveConfig.isConfigured
                }
                .buttonStyle(.bordered)
                .disabled(!clientIDField.hasSuffix(".apps.googleusercontent.com"))
            }
        } else if !drive.isConnected {
            Button {
                Task {
                    do { try await drive.connect() }
                    catch { exportError = error.localizedDescription }
                }
            } label: {
                Label("Connect Google Drive", systemImage: "person.crop.circle.badge.plus")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            Text("Sign in with your own Google account — scans go to your own subfolder inside the shared “\(GoogleDriveConfig.folderName)” folder.")
                .font(.caption2).foregroundStyle(.secondary)
            Button("Change client ID") {
                driveConfigured = false          // back to the paste field
            }
            .font(.caption2)
        } else {
            HStack(spacing: 8) {
                Image(systemName: driveIcon).foregroundStyle(driveTint)
                Text(driveStatusLine).font(.caption2).foregroundStyle(.secondary)
                Spacer()
                Button("Disconnect") { drive.disconnect() }.font(.caption2)
            }

            // Which Drive this is actually going to. Uploads are invisible from
            // inside the app, so a wrong account is otherwise silent.
            if !drive.isExpectedAccount, let email = drive.connectedEmail {
                driveWarning("Signed in as \(email), which isn't one of the project accounts. Auto-sync is paused.",
                             button: "Switch account")
            } else if drive.needsReconnect {
                // A connection made before the shared-folder change carries the
                // old, narrower permission and cannot write there.
                driveWarning("Drive permission changed — sign in again so scans can go into the shared folder.",
                             button: "Reconnect")
            }
            Toggle("Auto-sync each scan", isOn: $drive.autoSync).font(.caption)
            Button {
                Task { await runDrive() }
            } label: {
                Label(driveState == .sending ? "Uploading…" : "Sync to Drive now",
                      systemImage: "arrow.up.to.line.circle")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(driveState == .sending || viewModel.lastSessionURL == nil)
        }
    }

    /// An amber "this won't land where you think" row. Both cases it covers are
    /// fixed the same way — disconnect, then sign in again — so the button does
    /// that and the text says why.
    private func driveWarning(_ message: String, button: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 4) {
                Text(message).font(.caption2)
                Button(button) { drive.disconnect() }.font(.caption2)
            }
            Spacer()
        }
    }

    private var driveIcon: String {
        switch driveState {
        case .sent:   return "checkmark.circle.fill"
        case .failed: return "exclamationmark.triangle.fill"
        case .sending: return "arrow.up.circle"
        case .idle:   return "checkmark.icloud"
        }
    }
    private var driveTint: Color {
        switch driveState {
        case .sent: return .green
        case .failed: return .orange
        default: return .secondary
        }
    }
    private var driveStatusLine: String {
        let account = drive.connectedEmail ?? "Google Drive"
        switch driveState {
        case .idle:
            return drive.autoSync ? "\(account) · auto-sync on" : account
        case .sending: return "Uploading to Drive…"
        case .sent:    return "Synced ✓ to \(GoogleDriveConfig.folderName) on Drive"
        case .failed(let why): return why
        }
    }

    @MainActor
    private func runDrive() async {
        driveState = .sending
        do {
            try await viewModel.exportAndSyncToDrive()
            driveState = .sent("")
        } catch {
            driveState = .failed(error.localizedDescription)
        }
    }

    private func export(_ format: MeshExportFormat) {
        do {
            shareItem = ShareItem(url: try viewModel.exportCapturedMesh(as: format))
        } catch {
            exportError = error.localizedDescription
        }
    }

    private func exportLinuxPackage() {
        do {
            shareItem = ShareItem(url: try viewModel.exportLinuxPackage())
        } catch {
            exportError = error.localizedDescription
        }
    }
}

/// Wrapper so `.sheet(item:)` has an Identifiable without conforming Foundation's
/// URL to Identifiable ourselves (a retroactive conformance that warns under
/// Swift 5 and errors under Swift 6).
private struct ShareItem: Identifiable {
    let id = UUID()
    let url: URL
}
