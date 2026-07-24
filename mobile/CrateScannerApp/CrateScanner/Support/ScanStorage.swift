//
//  ScanStorage.swift
//  CrateScanner
//
//  User-visible, persistent storage for scans.
//
//  Everything lands under Documents/ so it appears in
//  Files → On My iPad → CrateScanner → CrateScans. That requires two Info.plist
//  keys (already set): UIFileSharingEnabled and LSSupportsOpeningDocumentsInPlace.
//
//  We deliberately do NOT use FileManager.temporaryDirectory: iOS reclaims it
//  whenever it likes, so a scan could disappear between Capture and AirDrop —
//  and nothing in temp is reachable from the Files app for a Drive sync.
//
//      Documents/CrateScans/
//        Sessions/session-ab12cd34/    ← RGB-D frames (what Linux nvblox reads)
//          manifest.json, color/*.jpg, depth/*.png
//        Packages/CrateScan-ab12cd34.zip   ← the thing you sync to Linux
//

import Foundation

enum ScanStorage {

    /// Documents/CrateScans — the folder the user sees in Files.
    static var root: URL {
        let docs = FileManager.default.urls(for: .documentDirectory,
                                            in: .userDomainMask)[0]
        return ensure(docs.appendingPathComponent("CrateScans", isDirectory: true),
                      excludeFromBackup: true)
    }

    /// Raw RGB-D sessions, one folder per scan.
    static var sessionsDirectory: URL {
        ensure(root.appendingPathComponent("Sessions", isDirectory: true))
    }

    /// Finished zips — this is what you drag into Google Drive.
    static var packagesDirectory: URL {
        ensure(root.appendingPathComponent("Packages", isDirectory: true))
    }

    /// Finished packages, newest first.
    static func packages() -> [URL] {
        let items = (try? FileManager.default.contentsOfDirectory(
            at: packagesDirectory,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles])) ?? []
        return items
            .filter { $0.pathExtension == "zip" }
            .sorted { modified($0) > modified($1) }
    }

    /// Total bytes currently held under Documents/CrateScans.
    static func totalBytes() -> Int64 {
        guard let e = FileManager.default.enumerator(
            at: root, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
        var total: Int64 = 0
        for case let url as URL in e {
            total += Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        }
        return total
    }

    /// Human-readable size of everything stored, for the review screen.
    static var totalSizeDescription: String {
        ByteCountFormatter.string(fromByteCount: totalBytes(), countStyle: .file)
    }

    /// Delete a session folder or package zip.
    static func remove(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
    }

    // MARK: Internals

    @discardableResult
    private static func ensure(_ url: URL, excludeFromBackup: Bool = false) -> URL {
        var url = url
        if !FileManager.default.fileExists(atPath: url.path) {
            try? FileManager.default.createDirectory(at: url,
                                                     withIntermediateDirectories: true)
            if excludeFromBackup {
                // Scans run to hundreds of MB; keep them out of iCloud backup.
                var values = URLResourceValues()
                values.isExcludedFromBackup = true
                try? url.setResourceValues(values)
            }
        }
        return url
    }

    private static func modified(_ url: URL) -> Date {
        (try? url.resourceValues(forKeys: [.contentModificationDateKey])
            .contentModificationDate) ?? .distantPast
    }
}
