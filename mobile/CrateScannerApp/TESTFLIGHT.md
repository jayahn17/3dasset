# Shipping CrateScanner to TestFlight

Private distribution to your own testers — no public App Store listing.

The repo side is done (see [What's already handled](#whats-already-handled)).
What's left is account setup, which only you can do because it needs your Apple
ID and a paid membership.

---

## 1. Pick the flavour

| | **Internal testing** | **External testing** |
|---|---|---|
| Testers | up to 100 | up to 10,000 |
| Who | must be users on your App Store Connect team | anyone, by email or public link |
| Review | **none** | Beta App Review on the first build, usually < 24 h |
| Live in | minutes after processing | after review passes |

For a class team, **internal** is almost certainly right: add each teammate as
an App Store Connect user, builds go live in minutes, and nobody at Apple reads
your app. The cost is that each person needs an Apple ID on your team.

Go external only if you want to hand out a link to people you won't add to the
team — and budget a day for the first review.

Neither is "published" in the store sense; the app is not listed or searchable.
If you ever want a real but private *store* listing, that's a different feature
(**Unlisted App Distribution**), and it does require full App Review.

> TestFlight builds **expire after 90 days**. This is a testing channel, not a
> way to keep an app installed indefinitely.

---

## 2. What only you can do

1. **Apple Developer Program — $99/year.** A free Apple ID can build to your own
   device but *cannot* use TestFlight. This is the one hard gate.
2. **Register the bundle ID** `com.jayahn.cratescanner170` at
   developer.apple.com → Certificates, Identifiers & Profiles → Identifiers.
   (It's set by `PRODUCT_BUNDLE_IDENTIFIER` in `project.yml`. Bundle ids are
   globally unique across all of Apple — if Xcode says one "cannot be
   registered to your development team because it is not available", some team
   already holds it, quite possibly an older Apple ID of your own.)
3. **Create the app record** in App Store Connect → Apps → **+** → New App:
   platform iOS, that bundle ID, a name, primary language, and any SKU.
4. **Set your team** so the archive can sign. Either pick it in Xcode under
   Signing & Capabilities, or — better, because it survives `xcodegen generate` —
   uncomment and fill in `DEVELOPMENT_TEAM` in [`project.yml`](project.yml).

---

## 3. Archive and upload

### Xcode, first time

```bash
cd mobile/CrateScannerApp
xcodegen generate && open CrateScanner.xcodeproj
```

Destination **Any iOS Device (arm64)** — you cannot archive with a simulator
selected — then **Product → Archive**. When the Organizer opens:
**Distribute App → App Store Connect → Upload**, and let it manage signing.

### Command line, for repeat uploads

```bash
cd mobile/CrateScannerApp
xcodegen generate

xcodebuild -project CrateScanner.xcodeproj -scheme CrateScanner \
    -configuration Release -destination 'generic/platform=iOS' \
    -archivePath build/CrateScanner.xcarchive \
    CURRENT_PROJECT_VERSION=2 \
    archive

xcodebuild -exportArchive \
    -archivePath build/CrateScanner.xcarchive \
    -exportPath build/export \
    -exportOptionsPlist ExportOptions.plist

xcrun altool --upload-app -f build/export/CrateScanner.ipa -t ios \
    --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER_ID"
```

`ExportOptions.plist` is a four-line file you write once:

```xml
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>teamID</key><string>YOUR_TEAM_ID</string>
</dict></plist>
```

The API key comes from App Store Connect → Users and Access → Integrations →
App Store Connect API. Put the `.p8` in `~/.appstoreconnect/private_keys/`.

> **`CURRENT_PROJECT_VERSION` must increase on every upload.** App Store Connect
> permanently refuses a build number it has already seen — including one you
> deleted. Bump it in `project.yml` or pass it on the command line as above.

---

## 4. Turn on testing

App Store Connect → your app → **TestFlight**. The build shows "Processing" for
5–15 minutes.

- **Internal:** create an internal group, add testers (they must already be
  users on the team), enable the build. They get an email; the TestFlight app
  installs it.
- **External:** create an external group, add emails or turn on a public link,
  then submit for Beta App Review.

Export compliance won't be asked — [`Info.plist`](CrateScanner/Info.plist)
already declares `ITSAppUsesNonExemptEncryption = false`, which is correct here:
the only cryptography is HTTPS and the system Keychain.

---

## 5. Tell your testers three things

1. **It needs a LiDAR device** — iPad Pro or iPhone Pro. `UIRequiredDeviceCapabilities`
   only filters to *ARKit*, and plenty of ARKit devices have no LiDAR, so a
   regular iPhone will install fine and then show the unsupported-device screen.
2. **Google Drive sync needs their account allowlisted.** The OAuth consent
   screen is in Testing, so any account not on the **Test users** list gets
   `Error 403: access_denied` at sign-in, and the core folder must be shared
   with them as Editor. See [WORKFLOW.md](WORKFLOW.md#google-drive-destination--one-shared-core-folder).
   Testers who skip Drive can still use "Share elsewhere" and the worker upload.
3. **Walk a full lap, slowly.** The app nags with an arrow if they stand still,
   but it's worth saying up front — it's the difference between a usable scan
   and a wasted one.

---

## What's already handled

Four things App Store Connect rejects or nags about, done in the repo:

| | Where | Why it matters |
|---|---|---|
| **Privacy manifest** | [`CrateScanner/PrivacyInfo.xcprivacy`](CrateScanner/PrivacyInfo.xcprivacy) | Declares `UserDefaults` (CA92.1) and file timestamps (DDA9.1). Without it: `ITMS-91053`, emailed *after* the build appears to succeed |
| **Version + build** | `MARKETING_VERSION`, `CURRENT_PROJECT_VERSION` in `project.yml` | No `CFBundleShortVersionString` = rejected upload |
| **Export compliance** | `ITSAppUsesNonExemptEncryption` in `Info.plist` | Otherwise every single build asks before testers can install |
| **App icon** | `CrateScanner/Assets.xcassets` | A missing 1024px icon fails validation |

Verified by a Release build: the bundle carries `PrivacyInfo.xcprivacy`, both
icon sizes, `CFBundleShortVersionString 1.0`, `CFBundleVersion 1`, and
`com.jayahn.cratescanner170`.

**Not verified here:** anything requiring an Apple Developer account — signing,
archiving, and the upload itself. Those need credentials this machine doesn't
have.

One thing to watch on **external** review: `Info.plist` sets
`NSAllowsArbitraryLoads` so the app can reach the Tailscale worker over plain
`http://`. Internal testing never sees a reviewer, but Beta App Review may ask
about it. The honest answer is that it talks to a private worker on your own
tailnet, not the public internet.
