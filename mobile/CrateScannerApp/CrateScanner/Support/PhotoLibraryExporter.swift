//
//  PhotoLibraryExporter.swift
//  CrateScanner
//
//  Bridge to KIRI Engine (and any photogrammetry app that imports from Photos).
//  KIRI takes a set of photos, not a zip or a Drive link, so we drop the scan's
//  high-resolution keyframes into a named Photos album. The user then opens KIRI
//  Engine → Photo/Upload mode → picks that album → reconstructs.
//
//  ── Why this asks for FULL photo access, not add-only ────────────────────────
//
//  Creating an album is a write to the library's structure, which Photos only
//  permits under `.readWrite`. Under `.addOnly`, PHAssetCollectionChangeRequest
//  raises an Objective-C exception — uncatchable from Swift, so the app hard
//  crashes instead of surfacing an error. So: ask for .readWrite (needs
//  NSPhotoLibraryUsageDescription), and if the user declines, fall back to
//  add-only and drop the photos in the camera roll with no album.
//
//  Photos are handed to Photos as file URLs, never as in-memory Data: a scan can
//  be 100+ 12 MP JPEGs, and loading those into one change block is a fast route
//  to a jetsam kill.
//
//  (The future path is KIRI's Open API with a developer key to create the asset
//  programmatically; this is the manual, key-free bridge for now.)
//

import Photos
import Foundation

enum PhotoExportError: LocalizedError {
    case denied
    case noImages
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .denied:  return "Photos access is off. Enable it in Settings → CrateScanner → Photos."
        case .noImages: return "This scan has no keyframes to export. Use Detail or Photo mode to capture high-res photos."
        case .failed(let why): return "Couldn’t save to Photos: \(why)"
        }
    }
}

/// What the export actually managed to do — the UI reports the album case
/// differently from the camera-roll fallback.
struct PhotoExportResult {
    let count: Int
    let albumName: String?   // nil = saved to the camera roll, no album
}

enum PhotoLibraryExporter {

    /// How many assets go into a single `performChanges` transaction. Keeps peak
    /// memory and transaction time bounded on large scans.
    private static let batchSize = 40

    /// Copy every JPEG in `folder` into a Photos album named `albumName`.
    /// Falls back to the camera roll if the user grants only add-only access.
    static func export(imagesIn folder: URL, albumName: String) async throws -> PhotoExportResult {
        // Enumerate first — cheap, and it means we never prompt for permission
        // on a scan that has nothing to export.
        let jpgs = ((try? FileManager.default.contentsOfDirectory(
            at: folder, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension.lowercased() == "jpg" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        guard !jpgs.isEmpty else { throw PhotoExportError.noImages }

        // .readWrite is what album creation needs. Only full authorization will
        // do — under .limited the app sees a user-picked subset and album
        // writes still fail.
        var canUseAlbum = await PHPhotoLibrary.requestAuthorization(for: .readWrite) == .authorized
        if !canUseAlbum {
            let addOnly = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
            guard addOnly == .authorized || addOnly == .limited else {
                throw PhotoExportError.denied
            }
        }

        // Resolve the album up front so every batch can append to the same one.
        var album: PHAssetCollection?
        if canUseAlbum {
            do {
                album = try await findOrCreateAlbum(named: albumName)
            } catch {
                album = nil
            }
            if album == nil { canUseAlbum = false }   // degrade, don't fail
        }

        var saved = 0
        for batch in stride(from: 0, to: jpgs.count, by: batchSize).map({
            Array(jpgs[$0 ..< min($0 + batchSize, jpgs.count)])
        }) {
            do {
                try await PHPhotoLibrary.shared().performChanges {
                    var placeholders: [PHObjectPlaceholder] = []
                    for url in batch {
                        let req = PHAssetCreationRequest.forAsset()
                        let opts = PHAssetResourceCreationOptions()
                        // Photos streams the file from disk; nothing is held in
                        // memory here, and the original stays in the session dir.
                        opts.shouldMoveFile = false
                        req.addResource(with: .photo, fileURL: url, options: opts)
                        if let ph = req.placeholderForCreatedAsset {
                            placeholders.append(ph)
                        }
                    }
                    // addAssets on an empty array throws — and touching the album
                    // request at all is only legal under .readWrite.
                    if let album, !placeholders.isEmpty,
                       let albumReq = PHAssetCollectionChangeRequest(for: album) {
                        albumReq.addAssets(placeholders as NSArray)
                    }
                }
                saved += batch.count
            } catch {
                // Partial success is worth reporting: some photos are already in
                // the library and KIRI can still use them.
                if saved == 0 { throw PhotoExportError.failed(error.localizedDescription) }
                break
            }
        }

        return PhotoExportResult(count: saved, albumName: canUseAlbum ? albumName : nil)
    }

    /// Reuse an album of this name if one exists, else create it. Returns nil if
    /// Photos won't give us the collection back (treated as "no album").
    private static func findOrCreateAlbum(named name: String) async throws -> PHAssetCollection? {
        let opts = PHFetchOptions()
        opts.predicate = NSPredicate(format: "title = %@", name)
        let existing = PHAssetCollection.fetchAssetCollections(
            with: .album, subtype: .albumRegular, options: opts)
        if let found = existing.firstObject { return found }

        var placeholder: PHObjectPlaceholder?
        try await PHPhotoLibrary.shared().performChanges {
            let req = PHAssetCollectionChangeRequest
                .creationRequestForAssetCollection(withTitle: name)
            placeholder = req.placeholderForCreatedAssetCollection
        }
        guard let id = placeholder?.localIdentifier else { return nil }
        return PHAssetCollection.fetchAssetCollections(
            withLocalIdentifiers: [id], options: nil).firstObject
    }
}
