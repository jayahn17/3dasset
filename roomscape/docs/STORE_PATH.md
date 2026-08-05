# Shipping it — Meta Horizon Store, and the web path that needs no store

> Process details here are from a mid-2026 knowledge cutoff and Meta reorganizes
> its developer program regularly (App Lab folded into the main store; release
> channels and review requirements have both changed). **Verify against
> developers.meta.com before planning around any specific step.** The shape of
> the process is stable; the names are not.

---

## The web path ships first, and might be enough

The Quest browser supports WebXR. A PlayCanvas page served over **HTTPS** with
a valid certificate can enter immersive VR with no store, no review, no APK, no
signing, no developer account. Load a URL, tap "Enter VR", you are in the room.

This is why D3 in the game plan puts WebXR before native. Concretely it means:

- P0 can reach a headset in days rather than weeks.
- Iteration is a page refresh, not a build-sign-sideload cycle.
- If the store path stalls for any reason — review, policy, time — the product
  still exists and is still shareable with a link.

Requirements: real TLS (self-signed will not enter XR), and correct CORS/COEP
headers if we use `SharedArrayBuffer` for the sort, which a fast WASM sorter
will want.

---

## Native, if and when

**Prerequisites, in order:**

1. A Meta developer account with an **organization** and identity/payment
   verification. Verification has historically taken days — start it during P3,
   not when the build is ready.
2. Create the app entry, choose the platform (Quest), get the App ID.
3. Build an Android APK targeting OpenXR, signed with a release keystore.
   (`Keep the keystore backed up.` Losing it means the app can never be updated.)
4. Upload to a **release channel** (alpha/beta) for internal testing — this
   requires no public review and is how a build gets onto a headset that was
   never plugged into our dev machine. This is the real P5 exit criterion.
5. Public release requires store review: content rating, age rating, store
   assets (icon, cover, trailer, screenshots), privacy policy URL, and a
   **Data Use Checkup** declaring every permission and what the data is used
   for.

**The parts that are specifically about *us*:**

- **Camera / Passthrough Camera API (P6).** Apps that read passthrough camera
  frames face additional scrutiny and must declare it in the Data Use Checkup.
  Meta's policy has consistently required that camera frames not leave the
  device without explicit user consent — and our whole architecture uploads
  them to our server. That is permissible with disclosure, but it must be
  disclosed accurately, and the consent UI has to be real, not a checkbox
  buried in settings. Write that flow while building P6, not during review.
- **User-generated spatial content.** If scenes are shareable, we inherit
  content-policy obligations: reporting, takedown, and a stance on scans of
  spaces the capturer had no right to scan. Keeping `visibility='private'` for
  v1 sidesteps this entirely, and I would.
- **Third-party licenses screen.** Apache-2.0 and MIT both require carrying
  notices. Generate it from `LICENSE_AUDIT.md`. See [OSS_LICENSE_MAP.md](OSS_LICENSE_MAP.md).
- **Performance review.** Meta has historically held store apps to frame-rate
  and comfort standards. Our 72 Hz / <1% stale-frame bar in
  [RENDER_TARGETS.md](RENDER_TARGETS.md) exists partly for this reason.

---

## iOS, for the viewer

The iPad already runs our capture app as a development build. Distributing the
*viewer* through the App Store adds:

- Apple Developer Program membership (annual fee).
- `NSCameraUsageDescription` and `NSPhotoLibraryUsageDescription` strings that
  describe the real purpose — the capture app already needs these
  (`mobile/CrateScannerApp/CrateScanner/Info.plist`).
- A **privacy manifest** (`PrivacyInfo.xcprivacy`) declaring collected data
  types and required-reason APIs.
- App Privacy answers in App Store Connect that match what the app actually
  does — for us: photos/camera data, precise location if we ever record it,
  and identifiers.

For a personal tool, TestFlight (up to 100 internal / 10k external testers) is
almost certainly sufficient and skips full review pain.

---

## Naming

**Do not ship as "Hyperscape."** It is Meta's product name for this exact
category on this exact platform, and the store submission is reviewed by Meta.
"Roomscape" is a placeholder — run a real name check before store assets get
made, since renaming after the fact touches the bundle ID, the keystore, the
store listing, and every URL.

---

## Realistic sequencing

| When | What |
|---|---|
| P3 starts | begin Meta developer org verification (it is slow and blocks nothing else) |
| P3 | HTTPS + WebXR viewer live; this *is* the shipping product for a while |
| P4 | privacy policy URL, retention policy written down, delete path tested |
| P5 | APK + release channel; internal testers; store assets |
| P5 end | public review submission — only if we actually want public distribution |
| P6 | Data Use Checkup updated for Passthrough Camera API; consent flow shipped |
