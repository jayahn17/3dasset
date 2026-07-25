//
//  GoogleDriveConfig.swift
//  CrateScanner
//
//  ── ONE-TIME SETUP (only you can do this; ~5 minutes) ────────────────────────
//
//  Programmatic Google Drive upload needs an OAuth client. To create one:
//
//  1. https://console.cloud.google.com  → create a project (any name).
//  2. APIs & Services → Enable APIs → enable "Google Drive API".
//  3. APIs & Services → OAuth consent screen → External → fill the app name +
//     your email → add yourself under "Test users" (no Google review needed
//     while it stays in Testing and uses the drive.file scope).
//  4. Credentials → Create Credentials → OAuth client ID → Application type:
//     iOS → Bundle ID:  com.jayahn.CrateScanner  (matches project.yml).
//  5. Copy the Client ID (1234567890-abcd.apps.googleusercontent.com).
//
//  Then just PASTE that client ID into the app: review screen → Sync to Google
//  Drive → "Set up". It's saved on the device — no rebuild, no Info.plist edit.
//
//  (No URL scheme needed: ASWebAuthenticationSession captures the OAuth redirect
//  itself, so nothing has to be registered in Info.plist.)
//

import Foundation

enum GoogleDriveConfig {
    /// The iOS OAuth client ID, entered in-app and stored on the device.
    static var clientID: String {
        get { UserDefaults.standard.string(forKey: "drive.clientID") ?? "" }
        set {
            UserDefaults.standard.set(
                newValue.trimmingCharacters(in: .whitespacesAndNewlines),
                forKey: "drive.clientID")
        }
    }

    static var isConfigured: Bool {
        clientID.hasSuffix(".apps.googleusercontent.com")
    }

    /// The reversed-client-ID scheme Google redirects to. Used as the
    /// ASWebAuthenticationSession callback scheme (not registered in Info.plist).
    static var redirectScheme: String {
        let head = clientID.components(separatedBy: ".apps.googleusercontent.com").first ?? ""
        return "com.googleusercontent.apps.\(head)"
    }

    static var redirectURI: String { "\(redirectScheme):/oauth2redirect" }

    /// drive.file = the app only ever sees files it created. No broad-access
    /// review, and it can't touch the rest of the user's Drive.
    static let scope = "https://www.googleapis.com/auth/drive.file"

    /// Drive folder finished scans are uploaded into.
    static let folderName = "CrateScans"
}
