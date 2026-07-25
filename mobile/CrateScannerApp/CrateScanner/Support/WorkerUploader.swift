//
//  WorkerUploader.swift
//  CrateScanner
//
//  Uploads a finished CrateScan package straight to the Linux assetpipe worker
//  (services/capture_worker.py) over the network — typically a Tailscale IP so
//  it works from anywhere. The worker's POST /rgbd/upload drops the zip into the
//  inbox that tools/watch_inbox.py fuses, so a scan goes iPad → 4080 → nvblox
//  with no Google Drive, no OAuth, and no manual copying.
//
//  The multipart body is streamed from a temp file (prefix + the zip + trailer)
//  via URLSession.upload(fromFile:), so a several-hundred-MB scan never has to
//  sit in memory.
//

import Foundation

/// Persisted worker connection settings.
final class WorkerSettings {
    static let shared = WorkerSettings()
    private let defaults = UserDefaults.standard
    private init() {}

    /// Base URL of the worker, e.g. http://100.x.y.z:8090 (Tailscale) — no path.
    var url: String {
        get { defaults.string(forKey: "worker.url") ?? "" }
        set { defaults.set(newValue.trimmingCharacters(in: .whitespaces), forKey: "worker.url") }
    }

    /// Auto-send each finished scan to the worker.
    var autoSend: Bool {
        get { defaults.object(forKey: "worker.autoSend") as? Bool ?? false }
        set { defaults.set(newValue, forKey: "worker.autoSend") }
    }

    var isConfigured: Bool { !url.isEmpty }
}

struct WorkerUploadResult {
    let name: String          // server's stem for this scan
    let bytes: Int            // bytes received
    let statusURL: String?    // GET here to poll fusion state
}

enum WorkerUploadError: LocalizedError {
    case notConfigured
    case badURL(String)
    case http(Int, String)
    case transport(String)

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "No worker URL set. Enter the 4080's Tailscale address (e.g. http://100.x.y.z:8090)."
        case .badURL(let u):
            return "That worker URL isn't valid: \(u)"
        case .http(let code, let body):
            return "Worker rejected the upload (HTTP \(code)). \(body)"
        case .transport(let why):
            return "Couldn't reach the worker: \(why). Is it running and on the same Tailnet?"
        }
    }
}

enum WorkerUploader {

    /// POST a file to `<base>/rgbd/upload` as multipart field "package".
    static func upload(_ fileURL: URL, toWorker base: String) async throws -> WorkerUploadResult {
        let trimmed = base.trimmingCharacters(in: .whitespaces)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !trimmed.isEmpty else { throw WorkerUploadError.notConfigured }
        guard let endpoint = URL(string: "\(trimmed)/rgbd/upload"),
              endpoint.scheme != nil, endpoint.host != nil else {
            throw WorkerUploadError.badURL(base)
        }

        let boundary = "CrateScannerBoundary-\(UUID().uuidString)"
        let bodyFile = try makeMultipartBody(fileURL: fileURL,
                                             fieldName: "package",
                                             boundary: boundary)
        defer { try? FileManager.default.removeItem(at: bodyFile) }

        var req = URLRequest(url: endpoint)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)",
                     forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 600

        let data: Data, resp: URLResponse
        do {
            (data, resp) = try await URLSession.shared.upload(for: req, fromFile: bodyFile)
        } catch {
            throw WorkerUploadError.transport(error.localizedDescription)
        }

        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            let body = String(data: data.prefix(300), encoding: .utf8) ?? ""
            throw WorkerUploadError.http(code, body)
        }

        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        return WorkerUploadResult(
            name: json?["name"] as? String ?? fileURL.deletingPathExtension().lastPathComponent,
            bytes: json?["bytes"] as? Int ?? 0,
            statusURL: json?["status_url"] as? String)
    }

    // MARK: -

    /// Write `--boundary … <file> … --boundary--` to a temp file, streaming the
    /// zip through in chunks so a large scan is never fully resident in memory.
    private static func makeMultipartBody(fileURL: URL, fieldName: String,
                                          boundary: String) throws -> URL {
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("upload-\(UUID().uuidString).multipart")
        FileManager.default.createFile(atPath: out.path, contents: nil)
        let handle = try FileHandle(forWritingTo: out)
        defer { try? handle.close() }

        let filename = fileURL.lastPathComponent
        var header = "--\(boundary)\r\n"
        header += "Content-Disposition: form-data; name=\"\(fieldName)\"; filename=\"\(filename)\"\r\n"
        header += "Content-Type: application/zip\r\n\r\n"
        handle.write(Data(header.utf8))

        let input = try FileHandle(forReadingFrom: fileURL)
        defer { try? input.close() }
        while case let chunk = input.readData(ofLength: 1 << 20), !chunk.isEmpty {
            handle.write(chunk)
        }

        handle.write(Data("\r\n--\(boundary)--\r\n".utf8))
        return out
    }
}
