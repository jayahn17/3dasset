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
//  5. Copy the Client ID (looks like 1234567890-abcd.apps.googleusercontent.com)
//     into `clientID` below.
//  6. Copy the "iOS URL scheme" it shows (the reversed client ID,
//     com.googleusercontent.apps.1234567890-abcd) into Info.plist under
//     CFBundleURLTypes → CFBundleURLSchemes.  (A placeholder is already there.)
//
//  Until `clientID` is filled in, the Drive section shows a setup prompt and
//  everything else (local folder, Tailscale upload, share sheet) still works.
//

import Foundation

enum GoogleDriveConfig {
    /// Paste your iOS OAuth client ID here.
    static let clientID = ""   // e.g. "1234567890-abcd.apps.googleusercontent.com"

    static var isConfigured: Bool { !clientID.isEmpty }

    /// The reversed-client-ID URL scheme, also required in Info.plist.
    static var redirectScheme: String {
        let head = clientID.components(separatedBy: ".apps.googleusercontent.com").first ?? ""
        return "com.googleusercontent.apps.\(head)"
    }

    /// Redirect back into the app after the consent screen.
    static var redirectURI: String { "\(redirectScheme):/oauth2redirect" }

    /// drive.file = the app only ever sees files it created. No broad-access
    /// review, and it can't touch the rest of the user's Drive.
    static let scope = "https://www.googleapis.com/auth/drive.file"

    /// Drive folder finished scans are uploaded into.
    static let folderName = "CrateScans"
}
