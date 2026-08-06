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
//     "Test users", not just the project owner. An account that is missing here
//     gets `Error 403: access_denied` at sign-in, which reads like a broken
//     client but is only this list. (No Google review needed while it stays in
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

    /// drive.file = the app only ever sees files it created. No broad-access
    /// review, and it can't touch the rest of the user's Drive.
    ///
    /// This and `folderID` are a matched pair, and getting them out of step is
    /// the subtle failure here. `drive.file` grants per-file access to files the
    /// app *created* — enough to make its own folder and fill it, but not to
    /// write into a folder that already exists and was made by someone else. So
    /// pointing `folderID` at a pre-existing shared folder means widening this
    /// to `https://www.googleapis.com/auth/drive`; leaving it narrow makes every
    /// upload fail with "File not found" however correct the id is. That wide
    /// scope is "restricted", which also forces the consent screen to stay in
    /// Testing with every signing-in account listed as a Test user. Staying
    /// narrow is what avoids all of that.
    static let scope = "https://www.googleapis.com/auth/drive.file"

    /// A specific pre-existing folder to upload into, taken from its Drive URL
    /// (`drive.google.com/drive/folders/<id>`). Empty means "find or create
    /// `folderName` instead", which is what the narrow scope above supports.
    ///
    /// Setting this requires widening `scope` — see the note there.
    static let folderID = ""

    /// Drive folder finished scans are uploaded into. The app creates it on the
    /// first upload and owns it from then on.
    static let folderName = "CrateScans"

    /// The Google account those scans belong to.
    ///
    /// Only a hint and a check: it preselects the account on the sign-in page,
    /// and the review screen warns when the app is actually connected as someone
    /// else — otherwise scans would quietly land in a `CrateScans` folder in the
    /// wrong Drive, which looks identical from inside the app.
    ///
    /// Whatever address goes here must be listed under APIs & Services → OAuth
    /// consent screen → Test users in the same Cloud project as
    /// `defaultClientID`, or sign-in comes back `access_denied` no matter how
    /// correct everything else is. This one owns the project, so it already is;
    /// any other account has to be added there first.
    static let accountHint = "jayahn@berkeley.edu"
}
