//
//  GoogleDriveSync.swift
//  CrateScanner
//
//  In-app Google Drive upload with no third-party SDK: OAuth 2.0 (PKCE, the
//  no-client-secret flow for native apps) via ASWebAuthenticationSession, then
//  the Drive REST v3 resumable-upload endpoint over URLSession — so a
//  several-hundred-MB scan streams from disk instead of loading into memory.
//
//  Scope is drive.file: the app only ever sees the files it creates, so it never
//  needs Google's restricted-scope review. See GoogleDriveConfig for setup.
//
//  NOTE: this is build-verified but not run-verified here — it needs the OAuth
//  client ID (GoogleDriveConfig.clientID) and a device. Errors surface with a
//  reason so the first real run is debuggable.
//

import AuthenticationServices
import CryptoKit
import Foundation
import UIKit

@MainActor
final class GoogleDriveSync: NSObject, ObservableObject {
    static let shared = GoogleDriveSync()

    @Published private(set) var isConnected = false
    @Published var autoSync = UserDefaults.standard.bool(forKey: "drive.autoSync") {
        didSet { UserDefaults.standard.set(autoSync, forKey: "drive.autoSync") }
    }

    private var accessToken: String?
    private var accessExpiry = Date.distantPast
    private var cachedFolderID: String?

    override init() {
        super.init()
        isConnected = Keychain.get("drive.refreshToken") != nil
    }

    // MARK: Sign-in / out

    /// Run the consent flow and store a refresh token. Idempotent.
    func connect() async throws {
        guard GoogleDriveConfig.isConfigured else { throw DriveError.notConfigured }
        let verifier = Self.randomURLSafe(64)
        let challenge = Self.s256(verifier)

        var comps = URLComponents(string: "https://accounts.google.com/o/oauth2/v2/auth")!
        comps.queryItems = [
            .init(name: "client_id", value: GoogleDriveConfig.clientID),
            .init(name: "redirect_uri", value: GoogleDriveConfig.redirectURI),
            .init(name: "response_type", value: "code"),
            .init(name: "scope", value: GoogleDriveConfig.scope),
            .init(name: "code_challenge", value: challenge),
            .init(name: "code_challenge_method", value: "S256"),
            .init(name: "access_type", value: "offline"),
            .init(name: "prompt", value: "consent"),
        ]

        let callback = try await authenticate(url: comps.url!,
                                              scheme: GoogleDriveConfig.redirectScheme)
        guard let code = URLComponents(url: callback, resolvingAgainstBaseURL: false)?
            .queryItems?.first(where: { $0.name == "code" })?.value else {
            throw DriveError.auth("No authorization code returned.")
        }
        try await exchangeCode(code, verifier: verifier)
        isConnected = true
    }

    func disconnect() {
        Keychain.delete("drive.refreshToken")
        accessToken = nil
        accessExpiry = .distantPast
        cachedFolderID = nil
        isConnected = false
    }

    // MARK: Upload

    /// Upload a file into the CrateScans folder. Returns the Drive file id.
    @discardableResult
    func upload(_ fileURL: URL, mimeType: String = "application/zip") async throws -> String {
        let token = try await validAccessToken()
        let folderID = try await ensureFolder(token: token)

        // 1) Open a resumable session.
        var start = URLRequest(url: URL(string:
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable")!)
        start.httpMethod = "POST"
        start.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        start.setValue("application/json; charset=UTF-8", forHTTPHeaderField: "Content-Type")
        let meta = ["name": fileURL.lastPathComponent, "parents": [folderID]] as [String: Any]
        start.httpBody = try JSONSerialization.data(withJSONObject: meta)

        let (_, startResp) = try await URLSession.shared.data(for: start)
        guard let http = startResp as? HTTPURLResponse,
              (200..<300).contains(http.statusCode),
              let sessionURI = http.value(forHTTPHeaderField: "Location") else {
            throw DriveError.upload("Could not start upload (HTTP \((startResp as? HTTPURLResponse)?.statusCode ?? 0)).")
        }

        // 2) Stream the bytes to the session URI.
        var put = URLRequest(url: URL(string: sessionURI)!)
        put.httpMethod = "PUT"
        put.setValue(mimeType, forHTTPHeaderField: "Content-Type")
        put.timeoutInterval = 3600
        let (data, putResp) = try await URLSession.shared.upload(for: put, fromFile: fileURL)
        guard let ph = putResp as? HTTPURLResponse, (200..<300).contains(ph.statusCode) else {
            let body = String(data: data.prefix(300), encoding: .utf8) ?? ""
            throw DriveError.upload("Upload failed (HTTP \((putResp as? HTTPURLResponse)?.statusCode ?? 0)). \(body)")
        }
        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        return json?["id"] as? String ?? ""
    }

    // MARK: OAuth internals

    private func exchangeCode(_ code: String, verifier: String) async throws {
        let body = form([
            "code": code,
            "client_id": GoogleDriveConfig.clientID,
            "redirect_uri": GoogleDriveConfig.redirectURI,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        ])
        let json = try await postToken(body)
        if let refresh = json["refresh_token"] as? String {
            Keychain.set("drive.refreshToken", refresh)
        }
        applyAccess(json)
    }

    private func validAccessToken() async throws -> String {
        if let t = accessToken, Date() < accessExpiry { return t }
        guard let refresh = Keychain.get("drive.refreshToken") else {
            throw DriveError.auth("Not connected to Google Drive.")
        }
        let json = try await postToken(form([
            "client_id": GoogleDriveConfig.clientID,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        ]))
        applyAccess(json)
        guard let t = accessToken else { throw DriveError.auth("Could not refresh token.") }
        return t
    }

    private func applyAccess(_ json: [String: Any]) {
        accessToken = json["access_token"] as? String
        let ttl = (json["expires_in"] as? Double) ?? 3600
        accessExpiry = Date().addingTimeInterval(ttl - 60)   // refresh a minute early
    }

    private func postToken(_ body: Data) async throws -> [String: Any] {
        var req = URLRequest(url: URL(string: "https://oauth2.googleapis.com/token")!)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            let msg = String(data: data.prefix(300), encoding: .utf8) ?? "token request failed"
            throw DriveError.auth(msg)
        }
        return json
    }

    /// Find (or create) the CrateScans folder, caching its id.
    private func ensureFolder(token: String) async throws -> String {
        if let id = cachedFolderID { return id }
        let q = "mimeType='application/vnd.google-apps.folder' and name='\(GoogleDriveConfig.folderName)' and trashed=false"
        var comps = URLComponents(string: "https://www.googleapis.com/drive/v3/files")!
        comps.queryItems = [.init(name: "q", value: q), .init(name: "fields", value: "files(id)")]
        var find = URLRequest(url: comps.url!)
        find.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, findResp) = try await URLSession.shared.data(for: find)
        if let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            if let files = json["files"] as? [[String: Any]],
               let id = files.first?["id"] as? String {
                cachedFolderID = id
                return id
            }
            // Surface Google's own error (e.g. Drive API disabled) rather than
            // silently falling through to a create that will fail the same way.
            if let err = json["error"] as? [String: Any] {
                throw DriveError.upload(driveErrorText(err, status: findResp))
            }
        }
        // Create it.
        var create = URLRequest(url: URL(string: "https://www.googleapis.com/drive/v3/files")!)
        create.httpMethod = "POST"
        create.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        create.setValue("application/json", forHTTPHeaderField: "Content-Type")
        create.httpBody = try JSONSerialization.data(withJSONObject: [
            "name": GoogleDriveConfig.folderName,
            "mimeType": "application/vnd.google-apps.folder",
        ])
        let (cData, cResp) = try await URLSession.shared.data(for: create)
        let cJSON = (try? JSONSerialization.jsonObject(with: cData)) as? [String: Any]
        if let id = cJSON?["id"] as? String {
            cachedFolderID = id
            return id
        }
        if let err = cJSON?["error"] as? [String: Any] {
            throw DriveError.upload(driveErrorText(err, status: cResp))
        }
        throw DriveError.upload("Could not create the Drive folder (HTTP \((cResp as? HTTPURLResponse)?.statusCode ?? 0)).")
    }

    /// Human-readable text from a Drive API error object.
    private func driveErrorText(_ err: [String: Any], status: URLResponse) -> String {
        let code = (status as? HTTPURLResponse)?.statusCode ?? 0
        let msg = (err["message"] as? String) ?? "unknown error"
        if code == 403 && msg.contains("has not been used") {
            return "Google Drive API isn't enabled for this project yet. Enable it at console.cloud.google.com → APIs & Services → Enable APIs → Google Drive API, then wait a minute. (\(msg))"
        }
        return "HTTP \(code): \(msg)"
    }

    // MARK: Web auth

    private func authenticate(url: URL, scheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { cont in
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: scheme) { callback, error in
                if let callback { cont.resume(returning: callback) }
                else { cont.resume(throwing: error ?? DriveError.auth("Sign-in was cancelled.")) }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            if !session.start() { cont.resume(throwing: DriveError.auth("Could not open the sign-in page.")) }
        }
    }

    // MARK: Helpers

    private func form(_ dict: [String: String]) -> Data {
        dict.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? $0.value)" }
            .joined(separator: "&").data(using: .utf8) ?? Data()
    }

    private static func randomURLSafe(_ n: Int) -> String {
        var bytes = [UInt8](repeating: 0, count: n)
        _ = SecRandomCopyBytes(kSecRandomDefault, n, &bytes)
        return Data(bytes).base64URL()
    }

    private static func s256(_ v: String) -> String {
        Data(SHA256.hash(data: Data(v.utf8))).base64URL()
    }
}

extension GoogleDriveSync: ASWebAuthenticationPresentationContextProviding {
    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            UIApplication.shared.connectedScenes
                .compactMap { ($0 as? UIWindowScene)?.keyWindow }
                .first ?? ASPresentationAnchor()
        }
    }
}

enum DriveError: LocalizedError {
    case notConfigured, auth(String), upload(String)
    var errorDescription: String? {
        switch self {
        case .notConfigured: return "Google Drive isn't set up yet — add your OAuth client ID (see GoogleDriveConfig.swift)."
        case .auth(let m):   return "Google sign-in failed: \(m)"
        case .upload(let m): return "Drive upload failed: \(m)"
        }
    }
}

private extension Data {
    func base64URL() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// Minimal Keychain string store for the refresh token.
enum Keychain {
    static func set(_ key: String, _ value: String) {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                 kSecAttrAccount as String: key]
        SecItemDelete(q as CFDictionary)
        var add = q
        add[kSecValueData as String] = Data(value.utf8)
        SecItemAdd(add as CFDictionary, nil)
    }
    static func get(_ key: String) -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrAccount as String: key,
                                kSecReturnData as String: true]
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
    static func delete(_ key: String) {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                       kSecAttrAccount as String: key] as CFDictionary)
    }
}
