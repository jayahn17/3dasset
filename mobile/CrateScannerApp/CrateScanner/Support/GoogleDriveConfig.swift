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

    /// Full Drive access, and the line here that deserves the most scrutiny.
    ///
    /// The narrow `drive.file` scope grants per-file access to files the app
    /// itself *created*. That is enough for one person filling their own folder,
    /// but not for the shared-core setup: a teammate's app did not create the
    /// core folder, so under `drive.file` every upload into it fails with "File
    /// not found" however correct the id is. Pooling scans in one Drive
    /// therefore requires this wider scope.
    ///
    /// The costs, so they aren't a surprise: the app can read and modify
    /// anything in the connected account's Drive, and `drive` is a *restricted*
    /// scope, so the consent screen must stay in **Testing** with every user
    /// listed under Test users. Publishing it would need Google's security
    /// review. Going back to one-person-one-folder means setting `scope` back to
    /// `…/auth/drive.file` and clearing `coreFolderOwner`.
    static let scope = "https://www.googleapis.com/auth/drive"

    /// Who owns the core folder everyone uploads into.
    ///
    /// Set, the app looks for `folderName` **owned by this account** — so a
    /// teammate signing in finds the folder shared with them rather than making
    /// a private one of their own, which is the failure that looks like success.
    /// Empty means every account just uses its own `folderName` folder.
    static let coreFolderOwner = "jayahn@berkeley.edu"

    /// Optional exact folder, from its Drive URL
    /// (`drive.google.com/drive/folders/<id>`). Takes precedence over the
    /// owner + name lookup; use it if two folders share a name.
    static let folderID = ""

    /// The core Drive folder finished scans are uploaded into.
    static let folderName = "CrateScans"

    /// Give each account its own subfolder inside the core folder, named after
    /// its email, instead of pooling every scan loose in one directory. Uploads
    /// stay attributable and `rclone copy` still pulls the lot in one command.
    static let perAccountSubfolders = true

    /// Accounts expected to upload. The review screen names the connected
    /// account and warns — pausing auto-sync — when it isn't one of these,
    /// because a scan in the wrong Drive looks identical from inside the app.
    /// Empty disables the check.
    ///
    /// Every address here must ALSO be listed under APIs & Services → OAuth
    /// consent screen → Test users in the Cloud project that owns
    /// `defaultClientID`, and must have Editor access to the core folder.
    /// Missing from the tester list gives `Error 403: access_denied` at sign-in;
    /// missing from the folder share gives "can't open the destination folder"
    /// at upload.
    static let allowedAccounts = [
        "jayahn@berkeley.edu",
        "xkdaus0417@gmail.com",
    ]
}
