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
//     your email → add EVERY account that will sign in from the app under
//     "Test users" — including the upload account in `accountHint` below, which
//     is not the project owner. (No Google review needed while it stays in
//     Testing and uses the drive.file scope.)
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
    /// Baked-in default client ID (iOS OAuth client IDs aren't secret — no
    /// client secret in PKCE, and it's public in the redirect). The in-app field
    /// can still override it.
    private static let defaultClientID =
        "2691632658-t5k0rb2hcsmp8c169sasst3htusdrsuh.apps.googleusercontent.com"

    /// The iOS OAuth client ID. Uses the in-app override if set, else the default.
    static var clientID: String {
        get {
            let stored = UserDefaults.standard.string(forKey: "drive.clientID") ?? ""
            return stored.isEmpty ? defaultClientID : stored
        }
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

    /// Full Drive scope, and the one thing here that deserves a second look.
    ///
    /// The narrow `drive.file` scope grants per-file access to files the app
    /// *created*, which is enough to make its own folder and fill it — but not
    /// to write into a folder that already exists and was made by someone else.
    /// Uploading into `folderID` below therefore fails with "File not found"
    /// under `drive.file`, however correct the id is. Targeting a shared class
    /// folder is the whole point, so the scope has to widen.
    ///
    /// Consequences worth knowing: the app can now read and modify anything in
    /// the connected account's Drive, and this is a "restricted" scope, so the
    /// consent screen must stay in **Testing** with the upload account listed as
    /// a Test user (publishing it would need Google's security review). That is
    /// also why the upload account should be the dedicated sync account and not
    /// a personal one.
    ///
    /// Want the narrow scope back? Set `folderID` to "" — the app then creates
    /// and owns a `folderName` folder — and change this back to
    /// `…/auth/drive.file`.
    static let scope = "https://www.googleapis.com/auth/drive"

    /// The exact destination folder, taken from its Drive URL:
    /// https://drive.google.com/drive/folders/1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI
    ///
    /// Empty means "find or create `folderName` instead".
    static let folderID = "1_HjCyP9-Th3UTjK6Kk89vTuud6ltL4AI"

    /// Drive folder finished scans are uploaded into. Used as the label in the
    /// UI, and as the folder to create when `folderID` is empty.
    static let folderName = "engin170_sync"

    /// The Google account that folder belongs to.
    ///
    /// Only a hint and a check: it preselects the account on the sign-in page,
    /// and the review screen warns when the app is actually connected as someone
    /// else — otherwise scans would quietly land in an `engin170_sync` folder in
    /// the wrong Drive, which looks identical from inside the app.
    ///
    /// IMPORTANT: the OAuth consent screen is in Testing mode, so this address
    /// must be listed under APIs & Services → OAuth consent screen → Test users
    /// in the same Cloud project as `defaultClientID`. Without that, sign-in
    /// comes back `access_denied` no matter how correct everything else is.
    static let accountHint = "xkdaus0417@gmail.com"
}
