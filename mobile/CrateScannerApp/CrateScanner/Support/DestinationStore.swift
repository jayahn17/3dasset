//
//  DestinationStore.swift
//  CrateScanner
//
//  "Pick a save folder once, then every scan lands there automatically."
//
//  The user chooses a destination folder with the system folder picker
//  (`.fileImporter` for `.folder`). If the Google Drive app is installed it
//  registers as a Files provider, so a Drive folder is selectable right there —
//  and copying a file into it makes Drive upload it. No Drive SDK, no OAuth, no
//  Google Cloud console: the File Provider does the sync.
//
//  We persist a security-scoped bookmark to that folder (not just its path — a
//  raw path loses the sandbox permission across launches). On each save we
//  resolve the bookmark, take the security scope, copy the zip in, and release.
//
//  Trade-off vs. a real Drive integration: this only reaches folders exposed
//  through Files. That covers Google Drive, iCloud Drive, Dropbox, and On My
//  iPad. It does not give per-file upload status — once copied, the provider
//  syncs on its own schedule.
//

import Foundation

enum DestinationError: LocalizedError {
    case noDestination
    case permissionLost
    case copyFailed(String)

    var errorDescription: String? {
        switch self {
        case .noDestination:
            return "No save folder chosen yet. Tap “Change” to pick one (e.g. a Google Drive folder)."
        case .permissionLost:
            return "Lost access to the saved folder. Pick it again."
        case .copyFailed(let why):
            return "Couldn’t save to the folder: \(why)"
        }
    }
}

/// Persists the auto-save destination and copies packages into it.
final class DestinationStore {
    static let shared = DestinationStore()

    private let bookmarkKey = "CrateScanner.destinationBookmark"
    private let nameKey = "CrateScanner.destinationName"
    private let autoSaveKey = "CrateScanner.autoSaveEnabled"
    private let defaults = UserDefaults.standard

    private init() {}

    /// Whether a destination has been chosen.
    var hasDestination: Bool { defaults.data(forKey: bookmarkKey) != nil }

    /// Display name of the chosen folder, for the UI ("Drive · CrateScans").
    var destinationName: String? { defaults.string(forKey: nameKey) }

    /// Whether finished scans copy themselves in automatically. Defaults to on
    /// once a destination exists — the whole point is not tapping every time.
    var autoSaveEnabled: Bool {
        get { defaults.object(forKey: autoSaveKey) as? Bool ?? true }
        set { defaults.set(newValue, forKey: autoSaveKey) }
    }

    /// Remember a folder the user picked. Call with the URL from `.fileImporter`.
    func setDestination(_ folder: URL) throws {
        let scoped = folder.startAccessingSecurityScopedResource()
        defer { if scoped { folder.stopAccessingSecurityScopedResource() } }
        // On iOS a plain bookmark of a document-picker URL is security-scoped;
        // `.withSecurityScope` is a macOS-only option and must not be passed.
        let bookmark = try folder.bookmarkData(options: [],
                                               includingResourceValuesForKeys: nil,
                                               relativeTo: nil)
        defaults.set(bookmark, forKey: bookmarkKey)
        defaults.set(prettyName(for: folder), forKey: nameKey)
    }

    /// Copy a file into the saved destination. Returns the destination name.
    @discardableResult
    func copy(_ fileURL: URL) throws -> String {
        guard let data = defaults.data(forKey: bookmarkKey) else {
            throw DestinationError.noDestination
        }
        var stale = false
        guard let folder = try? URL(resolvingBookmarkData: data,
                                    options: [],
                                    relativeTo: nil,
                                    bookmarkDataIsStale: &stale) else {
            throw DestinationError.permissionLost
        }
        let scoped = folder.startAccessingSecurityScopedResource()
        defer { if scoped { folder.stopAccessingSecurityScopedResource() } }

        let dest = folder.appendingPathComponent(fileURL.lastPathComponent)
        do {
            if FileManager.default.fileExists(atPath: dest.path) {
                try FileManager.default.removeItem(at: dest)
            }
            // Coordinated write — the File Provider (Drive/iCloud) needs it to
            // notice the new file and begin uploading.
            var coordError: NSError?
            var innerError: Error?
            NSFileCoordinator().coordinate(writingItemAt: dest,
                                           options: .forReplacing,
                                           error: &coordError) { target in
                do { try FileManager.default.copyItem(at: fileURL, to: target) }
                catch { innerError = error }
            }
            if let coordError { throw coordError }
            if let innerError { throw innerError }
        } catch {
            throw DestinationError.copyFailed(error.localizedDescription)
        }

        // Refresh the stored bookmark if the system flagged it stale.
        if stale { try? setDestination(folder) }
        return destinationName ?? prettyName(for: folder)
    }

    /// Forget the destination (UI "Remove").
    func clear() {
        defaults.removeObject(forKey: bookmarkKey)
        defaults.removeObject(forKey: nameKey)
    }

    // MARK: -

    /// A short, recognizable label. File-provider URLs encode the provider, so
    /// the last two path components ("Drive/CrateScans") read better than the
    /// opaque full path.
    private func prettyName(for folder: URL) -> String {
        let parts = folder.pathComponents.filter { $0 != "/" }
        return parts.suffix(2).joined(separator: " / ")
    }
}
