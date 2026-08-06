//
//  ScanViewModel.swift
//  CrateScanner
//
//  The brain of the scanner. Owns the ARKit session, ingests the live LiDAR
//  mesh (vertices *and* faces), translates ARKit's tracking state into human
//  guidance, manages the user-placed ghost box, runs the dimensional
//  extraction, and freezes a captured mesh for review + export.
//
//  Design: this object *is* the ARSessionDelegate (no separate coordinator) so
//  all AR state lives in one testable place. The SwiftUI layer only reads the
//  @Published properties and calls the intent methods (placeBox, fit, capture…).
//

import ARKit
import RealityKit
import Combine
import simd
import UIKit

/// Which stage of the flow we're in.
enum ScanPhase: Equatable {
    /// AR is live (camera preview + tracking warming up) but nothing is being
    /// recorded yet — the user lines up the shot and presses Start.
    case ready
    /// Live scanning: RGB-D frames + mesh streaming in.
    case scanning
    /// Frozen: a captured mesh + result exist, ready to review/orbit/export.
    case reviewing
}

@MainActor
final class ScanViewModel: NSObject, ObservableObject {

    // MARK: Published UI state

    /// Current stage of the flow. Starts in `.ready`: AR is live so the user
    /// can aim, but recording waits for the Start button.
    @Published private(set) var phase: ScanPhase = .ready

    /// Live, human-readable guidance ("Move slower", "Needs more light"…).
    @Published private(set) var feedback: ScanFeedback = .initializing

    /// True once the user has dropped the ghost box into the scene.
    @Published private(set) var isBoxPlaced = false

    /// The most recent measurement, once the user has fit or captured.
    @Published private(set) var result: MeasurementResult?

    /// Per-side crating buffer in inches, driven by the UI slider.
    @Published var paddingInches: Double = 2.0 {
        didSet { result = result?.withPadding(paddingInches) }
    }

    /// Rough count of captured mesh vertices — a simple "scan progress" signal.
    @Published private(set) var capturedVertexCount = 0

    /// The frozen mesh (cropped to the box, world-space meters) available after
    /// `capture()`. Consumed by the review view and the exporter.
    @Published private(set) var capturedMesh: CapturedMesh?

    /// RGB-D session folder (assetpipe schema) finalized on Capture.
    @Published private(set) var lastSessionURL: URL?

    /// How many RGB-D frames were written during this scan (live counter).
    @Published private(set) var rgbdFrameCount = 0

    /// Colour resolution to store (Fast … 4K). Set on the pre-Start screen.
    @Published var quality: CaptureQuality = CaptureSettings.shared.quality {
        didSet {
            CaptureSettings.shared.quality = quality
            if phase == .ready { runSession(resetting: true, record: false) }
        }
    }

    /// Video (stream) vs Photo (manual high-res stills).
    @Published var mode: CaptureMode = CaptureSettings.shared.mode {
        didSet {
            CaptureSettings.shared.mode = mode
            recorder.autoRecord = (mode == .video)
            if phase == .ready { runSession(resetting: true, record: false) }
        }
    }

    /// True briefly while a high-resolution still is being captured (Photo mode),
    /// so the shutter button can show progress.
    @Published private(set) var isCapturingPhoto = false

    /// 12 MP keyframes written this scan (Photo taps, or Hybrid auto-keyframes).
    @Published private(set) var keyframeCount = 0

    /// Live capture readiness — drives the reticle colour and the shutter's
    /// enabled state so blurry frames aren't saved.
    @Published private(set) var readiness: CaptureReadiness = .focusing

    /// Set while the user has stopped walking, telling them which way to go.
    /// Drives both the on-screen banner and the floating arrow in the AR scene.
    @Published private(set) var moveHint: MoveHint?

    /// Guided capture: show a dome of target viewpoints and walk the user to each.
    @Published var guidedEnabled = false {
        didSet { guidedEnabled ? buildGuidedTargets() : clearGuidedTargets() }
    }
    /// Live "Move left / Hold steady…" instruction while guided.
    @Published private(set) var guidanceText = ""
    /// Captured / total target viewpoints.
    @Published private(set) var guidedDone = 0
    @Published private(set) var guidedTotal = 0

    // MARK: AR plumbing

    /// The RealityKit view we drive. Set once by the representable in makeUIView.
    private weak var arView: ARView?

    /// ARKit delivers frames here instead of the main queue. Recording a frame
    /// means a JPEG encode and a depth copy; on the main queue that is a visible
    /// stutter and it caps the achievable frame rate.
    private let captureQueue = DispatchQueue(label: "CrateScanner.arsession.delegate",
                                             qos: .userInitiated)

    /// Records color + LiDAR depth + pose for Linux `assetpipe rgbd`.
    ///
    /// Held in a lock box because it is written from the main actor and read
    /// from `captureQueue` inside the ARSessionDelegate callbacks.
    private let recorder = RecorderBox()

    /// Decides when a frame is sharp + steady enough to keep, and when Detail
    /// mode should auto-fire a keyframe. Lives off the main actor with the
    /// delegate, so it's a lock-guarded reference type like `recorder`.
    private let gate = CaptureGate()

    /// Watches for the user standing still and works out which way they should
    /// walk to keep orbiting. Main-actor only, like the rest of the UI state.
    private let nudge = MoveNudge()

    /// The floating "go this way" arrow, parked in front of the camera while a
    /// nudge is active. Kept alive across frames and just re-posed each time.
    private var arrowEntity: Entity?
    private var arrowAnchor: AnchorEntity?

    /// Holds the floating AR markers dropped at each keyframe's capture pose, so
    /// the user can see where they've already shot and where coverage is thin.
    private var markersAnchor: AnchorEntity?

    /// Guided-capture target viewpoints and their AR markers.
    private var targets: [CaptureTarget] = []
    private var targetEntities: [ModelEntity] = []
    private var targetsAnchor: AnchorEntity?
    private var highlightedTarget: Int?

    /// The anchored ghost box, added to the scene when the user places it.
    private var ghost = GhostBoxEntity()
    private var ghostAnchor: AnchorEntity?

    /// World-space geometry of the LiDAR mesh, keyed by mesh anchor id so we can
    /// replace an anchor's contribution wholesale when ARKit refines it.
    private var meshes: [UUID: CapturedMesh] = [:]

    /// Current ghost box in world space. Nil until placed.
    private var currentBox: AABB?

    // MARK: Session lifecycle

    /// Wire this view model to the ARView and start the live preview.
    ///
    /// The session runs immediately so the camera and tracking warm up, but
    /// recording does NOT begin — the user aims, then presses Start.
    func attach(to arView: ARView) {
        self.arView = arView
        // Order matters: the queue must be set before the delegate, or the
        // first callbacks still land on the main queue.
        arView.session.delegateQueue = captureQueue
        arView.session.delegate = self
        runSession(resetting: true, record: false)
    }

    /// Begin recording RGB-D + mesh. Called from the Start button. Detail mode
    /// auto-keyframes are driven by the capture gate per frame, not a timer, so
    /// they only fire when the view is sharp and steady.
    func start() {
        guard phase == .ready else { return }
        phase = .scanning
        nudge.reset()
        recorder.isRecording = true
    }

    /// Configure + run the world-tracking session with mesh reconstruction.
    /// - Parameter record: whether to begin recording immediately (false while
    ///   in `.ready`, true when resuming an already-started scan).
    private func runSession(resetting: Bool, record: Bool) {
        guard let arView else { return }

        let config = ARWorldTrackingConfiguration()
        // The whole point: ask ARKit for the reconstructed LiDAR mesh.
        config.sceneReconstruction = .mesh
        config.frameSemantics.insert(.sceneDepth)
        config.environmentTexturing = .none
        // Keep the lens focused on the object — critical for sharp keyframes.
        config.isAutoFocusEnabled = true

        // Pick the capture video format for the chosen quality/mode.
        applyVideoFormat(to: config)

        // We render the mesh ourselves via RealityKit's debug visualization so
        // the user literally sees coverage fill in and where the gaps are.
        arView.debugOptions.insert(.showSceneUnderstanding)

        let options: ARSession.RunOptions = resetting
            ? [.resetTracking, .removeExistingAnchors]
            : []
        arView.session.run(config, options: options)

        recorder.autoRecord = (mode == .video)

        // Fresh RGB-D recorder on reset; keep accumulating if resuming.
        if resetting || recorder.exporter == nil {
            recorder.exporter = try? SessionExporter(minInterval: quality.autoInterval,
                                                     maxColorWidth: quality.maxColorWidth)
            rgbdFrameCount = 0
            keyframeCount = 0
            lastSessionURL = nil
        }
        recorder.isRecording = record
    }

    /// Choose the AR video format for the current quality + mode.
    ///
    /// - Photo mode prefers the format flagged for high-resolution still capture,
    ///   which is what `captureHighResolutionFrame` needs to reach ~12 MP.
    /// - 4K quality in Auto mode selects ARKit's dedicated 4K streaming format.
    /// - Otherwise ARKit's default (≈1920×1440) is used.
    ///
    /// All three return nil on devices that don't support them; we just keep the
    /// default in that case.
    private func applyVideoFormat(to config: ARWorldTrackingConfiguration) {
        if mode == .photo,
           let hi = ARWorldTrackingConfiguration.recommendedVideoFormatForHighResolutionFrameCapturing {
            config.videoFormat = hi
        } else if quality.wants4KVideoFormat,
                  let fourK = ARWorldTrackingConfiguration.recommendedVideoFormatFor4KResolution {
            config.videoFormat = fourK
        }
    }

    /// Capture one 12 MP keyframe (with pose + intrinsics + depth) and record it
    /// on the keyframe track. Used by the manual shutter (Photo mode) and the
    /// automatic timer (Hybrid mode).
    ///
    /// `captureHighResolutionFrame` briefly taps the photo pipeline to return a
    /// ~12 MP `ARFrame` — far larger than the AR video feed — carrying the pose,
    /// intrinsics, timestamp, and sceneDepth in the session's world frame. The
    /// completion runs off the main actor; we extract synchronously (the frame is
    /// valid for the callback) then publish the count.
    func capturePhoto() {
        guard phase == .scanning, mode == .photo || mode == .hybrid || guidedEnabled,
              let arView, !isCapturingPhoto else { return }
        isCapturingPhoto = true
        arView.session.captureHighResolutionFrame { [weak self] frame, _ in
            guard let self else { return }
            let pose = frame?.camera.transform
            let n = frame.flatMap { self.recorder.recordKeyframe($0) }
            Task { @MainActor in
                self.isCapturingPhoto = false
                if let n {
                    self.keyframeCount = n
                    // Drop a marker only when a keyframe was actually written.
                    if let pose { self.addCaptureMarker(at: pose) }
                }
            }
        }
    }

    /// Place a floating marker in the AR scene at a keyframe's camera pose — a
    /// green dot with a short stick pointing the way the camera looked. As the
    /// user orbits, the ring of markers shows covered angles; the gaps are where
    /// more shots are needed.
    private func addCaptureMarker(at transform: simd_float4x4) {
        guard let arView else { return }
        if markersAnchor == nil {
            let anchor = AnchorEntity(world: .zero)
            arView.scene.addAnchor(anchor)
            markersAnchor = anchor
        }
        let marker = Self.makeMarkerEntity()
        marker.transform = Transform(matrix: transform)
        markersAnchor?.addChild(marker)
    }

    /// A camera-frustum marker: a translucent pyramid whose apex is the camera
    /// position and whose base opens along the view direction (local −Z), plus a
    /// solid dot at the apex. This shows not just *where* a keyframe was taken but
    /// the *angle* it saw — the standard photogrammetry camera view — so overlap
    /// and gaps in coverage are obvious as the ring of frustums builds up.
    private static func makeMarkerEntity() -> Entity {
        let root = Entity()

        // Small (~3.5 cm) so a ring of many markers reads as a ring, not a blob.
        if let mesh = frustumMesh(depth: 0.035, halfW: 0.022, halfH: 0.017) {
            // Two-sided via reversed-winding geometry (faceCulling is iOS 18+).
            let mat = UnlitMaterial(color: UIColor.systemGreen.withAlphaComponent(0.3))
            root.addChild(ModelEntity(mesh: mesh, materials: [mat]))
        }

        let apex = ModelEntity(
            mesh: .generateSphere(radius: 0.007),
            materials: [SimpleMaterial(color: .systemGreen, isMetallic: false)])
        root.addChild(apex)
        return root
    }

    /// Build a view-frustum pyramid: apex at the origin (camera), rectangular
    /// base at −Z. Custom mesh because cone/pyramid generators are iOS 18+.
    private static func frustumMesh(depth d: Float, halfW w: Float, halfH h: Float) -> MeshResource? {
        let apex = SIMD3<Float>(0, 0, 0)
        let c1 = SIMD3<Float>(-w, -h, -d)
        let c2 = SIMD3<Float>( w, -h, -d)
        let c3 = SIMD3<Float>( w,  h, -d)
        let c4 = SIMD3<Float>(-w,  h, -d)

        var positions: [SIMD3<Float>] = []
        var indices: [UInt32] = []
        // Emit each triangle both ways so the frustum is visible from inside and
        // out without the iOS 18 faceCulling API.
        func tri(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>) {
            let base = UInt32(positions.count)
            positions.append(contentsOf: [a, b, c])
            indices.append(contentsOf: [base, base + 1, base + 2,
                                        base, base + 2, base + 1])
        }
        // Four side faces from the apex to each base edge …
        tri(apex, c1, c2); tri(apex, c2, c3); tri(apex, c3, c4); tri(apex, c4, c1)
        // … and the base quad (the "image plane").
        tri(c1, c2, c3); tri(c1, c3, c4)

        var desc = MeshDescriptor(name: "frustum")
        desc.positions = MeshBuffers.Positions(positions)
        desc.primitives = .triangles(indices)
        return try? MeshResource.generate(from: [desc])
    }

    // MARK: Guided capture

    /// Generate the dome of target viewpoints around the object and show them in
    /// AR. Center/radius come from the placed box if there is one, else a point
    /// ~1 m in front of the camera.
    private func buildGuidedTargets() {
        guard let arView else { return }
        clearGuidedTargets()

        let center: SIMD3<Float>
        let radius: Float
        if let box = currentBox {
            center = box.center
            radius = simd_length(box.extents) / 2 + 0.4
        } else {
            let cam = arView.cameraTransform
            center = cam.translation + (-cam.matrix.forward) * 1.0
            radius = 0.6
        }

        targets = GuidedCapture.makeTargets(center: center, radius: radius)
        guidedTotal = targets.count
        guidedDone = 0
        highlightedTarget = nil

        let anchor = AnchorEntity(world: .zero)
        for t in targets {
            let e = Self.makeTargetEntity(color: .systemBlue)
            e.transform = Self.lookAtTransform(position: t.position, forward: t.forward)
            anchor.addChild(e)
            targetEntities.append(e)
        }
        arView.scene.addAnchor(anchor)
        targetsAnchor = anchor
        guidanceText = "Walk to the highlighted marker"
    }

    private func clearGuidedTargets() {
        targetsAnchor?.removeFromParent()
        targetsAnchor = nil
        targetEntities.removeAll()
        targets.removeAll()
        guidedTotal = 0
        guidedDone = 0
        highlightedTarget = nil
        guidanceText = ""
    }

    /// Each frame while guided: point the user at the nearest unshot target and
    /// auto-capture when they arrive sharp + steady.
    private func updateGuidance(cameraTransform m: simd_float4x4, ready: Bool) {
        guard phase == .scanning, recorder.isRecording, !targets.isEmpty else { return }
        let camPos = m.translation
        let camFwd = -SIMD3<Float>(m.columns.2.x, m.columns.2.y, m.columns.2.z)

        guard let i = GuidedCapture.nearestPending(to: camPos, in: targets) else {
            guidanceText = "All viewpoints captured ✓"
            return
        }
        if highlightedTarget != i { highlight(i) }

        if GuidedCapture.reached(targets[i], camPos: camPos, camForward: camFwd) {
            if ready, !isCapturingPhoto {
                targets[i].done = true
                Self.tint(targetEntities[i], color: .systemGreen, alpha: 0.35)
                guidedDone += 1
                highlightedTarget = nil          // re-highlight the next one
                capturePhoto()
            } else {
                guidanceText = "Hold steady…"
            }
        } else {
            guidanceText = GuidedCapture.instruction(to: targets[i], cameraTransform: m)
        }
    }

    private func highlight(_ i: Int) {
        // Dim the previous next, brighten the new one.
        if let prev = highlightedTarget, prev < targetEntities.count, !targets[prev].done {
            Self.tint(targetEntities[prev], color: .systemBlue, alpha: 0.16)
        }
        if i < targetEntities.count {
            Self.tint(targetEntities[i], color: .systemYellow, alpha: 0.4)
        }
        highlightedTarget = i
    }

    private static func tint(_ entity: ModelEntity, color: UIColor, alpha: CGFloat) {
        entity.model?.materials = [UnlitMaterial(color: color.withAlphaComponent(alpha))]
    }

    /// A translucent target frustum (no apex dot — these are goals, not captures).
    private static func makeTargetEntity(color: UIColor) -> ModelEntity {
        let mesh = frustumMesh(depth: 0.08, halfW: 0.05, halfH: 0.038)
            ?? .generateSphere(radius: 0.03)
        return ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: color.withAlphaComponent(0.16))])
    }

    /// Transform placing an entity at `position` with local −Z aimed at `forward`.
    private static func lookAtTransform(position: SIMD3<Float>, forward: SIMD3<Float>) -> Transform {
        let q = simd_quatf(from: SIMD3<Float>(0, 0, -1), to: simd_normalize(forward))
        return Transform(scale: .one, rotation: q, translation: position)
    }

    // MARK: Move nudge — "you've stopped, go this way"

    /// Per-frame: has the user gone anywhere lately, and if not, which way should
    /// they walk? Guided capture is already walking them to a specific marker, so
    /// we stay quiet in that mode rather than issue two sets of directions.
    private func updateMoveNudge(time: TimeInterval, cameraTransform m: simd_float4x4) {
        guard phase == .scanning, !guidedEnabled else {
            if moveHint != nil { moveHint = nil }
            hideArrow()
            return
        }

        let hint = nudge.evaluate(time: time,
                                  cameraTransform: m,
                                  pivot: currentBox?.center,
                                  isRecording: recorder.isRecording)
        if hint != moveHint { moveHint = hint }

        if let hint {
            showArrow(direction: hint.worldDirection, cameraTransform: m)
        } else {
            hideArrow()
        }
    }

    /// Park the arrow in the lower third of the view, aimed along `direction`.
    /// It rides with the camera rather than being pinned to a world point, so it
    /// stays visible however the user turns.
    private func showArrow(direction: SIMD3<Float>, cameraTransform m: simd_float4x4) {
        guard let arView else { return }
        if arrowAnchor == nil {
            let anchor = AnchorEntity(world: .zero)
            let arrow = Self.makeArrowEntity()
            anchor.addChild(arrow)
            arView.scene.addAnchor(anchor)
            arrowAnchor = anchor
            arrowEntity = arrow
        }
        let camPos = m.translation
        let camFwd = simd_normalize(-m.forward)
        let camUp = simd_normalize(SIMD3<Float>(m.columns.1.x, m.columns.1.y, m.columns.1.z))
        // 75 cm ahead and a little low: readable, but clear of the object itself.
        let place = camPos + camFwd * 0.75 - camUp * 0.22
        arrowEntity?.transform = Transform(scale: .one,
                                           rotation: Self.yaw(towards: direction),
                                           translation: place)
        arrowEntity?.isEnabled = true
    }

    /// Rotation about Y that aims the arrow's local −Z along a horizontal
    /// direction. Deliberately not `simd_quatf(from:to:)` like the capture
    /// frustums use: the nudge direction is world-horizontal, so it lands exactly
    /// opposite the −Z base whenever the user should walk toward world +Z, and
    /// that antiparallel case has no well-defined rotation axis.
    private static func yaw(towards direction: SIMD3<Float>) -> simd_quatf {
        let angle = atan2(-direction.x, -direction.z)
        return simd_quatf(angle: angle, axis: SIMD3<Float>(0, 1, 0))
    }

    private func hideArrow() {
        arrowEntity?.isEnabled = false
    }

    /// A chunky arrow pointing along local −Z — a shaft plus a pyramid head — so
    /// `lookAtTransform` can aim it the same way it aims the capture frustums.
    /// Unlit yellow to match the "attention" colour used by the guidance banner.
    private static func makeArrowEntity() -> Entity {
        let root = Entity()
        let color = UIColor.systemYellow

        let shaft = ModelEntity(
            mesh: .generateBox(size: SIMD3<Float>(0.028, 0.028, 0.11)),
            materials: [UnlitMaterial(color: color.withAlphaComponent(0.85))])
        shaft.position = SIMD3<Float>(0, 0, -0.055)
        root.addChild(shaft)

        // frustumMesh opens toward −Z with its apex at the origin; turning it
        // half a revolution puts the point out front, leading the shaft.
        if let head = frustumMesh(depth: 0.09, halfW: 0.055, halfH: 0.055) {
            let tip = ModelEntity(mesh: head, materials: [UnlitMaterial(color: color)])
            tip.orientation = simd_quatf(angle: .pi, axis: SIMD3<Float>(0, 1, 0))
            tip.position = SIMD3<Float>(0, 0, -0.20)
            root.addChild(tip)
        }
        return root
    }

    /// Pause the session (e.g. when leaving the screen or entering review).
    func pause() {
        arView?.session.pause()
    }

    // MARK: User intents

    /// Drop the ghost box roughly where the user is aiming. We raycast to a
    /// surface (usually the floor) under screen center; if nothing is hit we
    /// place it a couple meters in front of the camera. Exact placement isn't
    /// critical — "Fit to Object" snaps it to the machine afterward.
    func placeBox() {
        guard let arView else { return }

        let center = arView.bounds.mid
        var origin: SIMD3<Float>

        if let hit = arView.raycast(from: center,
                                    allowing: .estimatedPlane,
                                    alignment: .any).first {
            origin = hit.worldTransform.translation
            // Lift the box so it sits above the surface it landed on.
            origin.y += 0.5
        } else {
            // Fallback: 2 m along the camera's forward (−Z) axis.
            let cam = arView.cameraTransform
            let forward = -cam.matrix.forward
            origin = cam.translation + forward * 2.0
        }

        // A sensible default starting volume the user can grow/shrink or fit.
        setBox(AABB(center: origin, extents: SIMD3<Float>(1.0, 1.0, 1.0)))
        isBoxPlaced = true
    }

    /// Move the placed box to wherever the user tapped.
    ///
    /// This replaced a drag gesture: dragging asked the user to keep a finger on
    /// the glass while holding an iPad steady and walking, and it mapped screen
    /// pixels to metres through a fudge factor. A tap raycasts to the real
    /// surface under the finger, so one touch puts the box where they meant.
    /// Ignored when the ray hits nothing — better to do nothing than to fling the
    /// box to a guessed depth.
    func moveBox(toScreenPoint point: CGPoint) {
        guard let arView, let box = currentBox else { return }
        guard let hit = arView.raycast(from: point,
                                       allowing: .estimatedPlane,
                                       alignment: .any).first else { return }
        var origin = hit.worldTransform.translation
        // Sit the box on the surface rather than half-buried in it.
        origin.y += box.extents.y / 2
        setBox(AABB(center: origin, extents: box.extents))
    }

    /// Resize the placed box to explicit full extents (meters), e.g. from sliders.
    func resizeBox(extents: SIMD3<Float>) {
        guard let box = currentBox else { return }
        setBox(AABB(center: box.center, extents: simd_max(extents, .init(repeating: 0.05))))
    }

    /// Live fit preview: snap the box to the tight AABB of the LiDAR vertices
    /// inside it and update the dimension readout. Non-destructive — the user
    /// can keep scanning and fit again.
    func fitToObject() {
        guard let box = currentBox else { return }
        let inside = allVertices().filter { box.contains($0) }

        guard let fitted = AABB.fitting(inside), !fitted.isEmpty else {
            feedback = .noGeometry
            return
        }
        setBox(fitted)
        result = MeasurementResult(extentsMeters: fitted.extents, padding: paddingInches)
    }

    /// Finalize: combine every captured anchor mesh, crop it to the placed box,
    /// compute the object's tight dimensions, freeze it for review/export, and
    /// pause live scanning. This is the "Capture" action.
    func capture() {
        recorder.isRecording = false

        // 1. The RGB-D session is the deliverable — Linux nvblox builds the 3D
        //    asset from it and the crate is designed off that afterwards. So it
        //    is finalized FIRST and unconditionally. Earlier this ran last and
        //    behind a `guard currentBox != nil`, which threw away a perfectly
        //    good recording whenever no box was placed or the LiDAR crop came
        //    back empty.
        if let exporter = recorder.exporter {
            do {
                lastSessionURL = try exporter.finalize(objectHint: "scanned object")
                rgbdFrameCount = exporter.frameCount
            } catch {
                lastSessionURL = nil
            }
        }

        // 2. The on-device mesh and dimensions are an optional convenience.
        var combined = CapturedMesh()
        for mesh in meshes.values { combined.append(mesh) }

        if let box = currentBox {
            // A box was placed: crop to it and report object dimensions.
            let cropped = combined.cropped(to: box)
            if !cropped.isEmpty,
               let fitted = AABB.fitting(cropped.vertices), !fitted.isEmpty {
                setBox(fitted)
                result = MeasurementResult(extentsMeters: fitted.extents,
                                           padding: paddingInches)
                capturedMesh = cropped
            }
        } else {
            // No box: keep the whole scan for preview. Deliberately no
            // MeasurementResult — the bounds would describe the room, not an
            // object, and crating is computed from the fused asset on Linux.
            capturedMesh = combined.isEmpty ? nil : combined
        }

        // 3. Only a total miss is a failure.
        guard capturedMesh != nil || lastSessionURL != nil else {
            feedback = .noGeometry
            recorder.isRecording = true
            return
        }

        phase = .reviewing
        moveHint = nil
        hideArrow()
        nudge.reset()
        pause()
    }

    /// Export the frozen mesh to a temp file (STL/OBJ/USDZ) for the share sheet.
    /// Throws a `MeshExportError` if there's nothing captured or the format is
    /// unsupported on this device.
    func exportCapturedMesh(as format: MeshExportFormat) throws -> URL {
        guard let mesh = capturedMesh else { throw MeshExportError.emptyMesh }
        let base = result.map { "CrateScan-\($0.id.uuidString.prefix(8))" } ?? "CrateScan"
        return try MeshExporter.export(mesh, as: format, baseName: String(base))
    }

    /// One zip for Linux: mesh.obj + measurement.json + session/ (RGB-D).
    /// Named `crate_<date>_<hr>_<min>_<sec>` so uploads sort by time and read
    /// plainly in Drive / the worker inbox.
    func exportLinuxPackage() throws -> URL {
        return try PackageExporter.buildZip(
            mesh: capturedMesh,
            measurement: result,
            sessionDirectory: lastSessionURL,
            baseName: Self.timestampName()
        )
    }

    /// e.g. "crate_20260725_14_30_52".
    static func timestampName() -> String {
        let fmt = DateFormatter()
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.dateFormat = "yyyyMMdd_HH_mm_ss"
        return "crate_\(fmt.string(from: Date()))"
    }

    /// Zip only the RGB-D session folder (smaller than full package).
    func exportSessionZip() throws -> URL {
        guard let session = lastSessionURL else {
            throw PackageExportError.nothingToExport
        }
        return try PackageExporter.zipForSharing(session)
    }

    /// Build the Linux package and copy it into the user's saved destination
    /// (e.g. an iCloud Drive folder in Files). Off the main actor — zipping ~440
    /// frames takes a moment. Returns the destination's display name on success.
    nonisolated func exportAndAutoSave() async throws -> String {
        let zip = try await MainActor.run { try self.exportLinuxPackage() }
        return try DestinationStore.shared.copy(zip)
    }

    /// Build the Linux package and upload it to Google Drive
    /// (`GoogleDriveConfig.folderName`, currently CrateScans).
    nonisolated func exportAndSyncToDrive() async throws {
        let zip = try await MainActor.run { try self.exportLinuxPackage() }
        _ = try await GoogleDriveSync.shared.upload(zip)
    }

    /// Save this scan's high-res photos into a Photos album for KIRI Engine.
    /// Prefers the 12 MP keyframes (Detail/Photo mode); falls back to the RGB-D
    /// stream images. The album is nil when the user granted only add-only
    /// access — the photos are in the camera roll instead.
    nonisolated func exportPhotosForKiri() async throws -> PhotoExportResult {
        guard let session = await MainActor.run(body: { self.lastSessionURL }) else {
            throw PhotoExportError.noImages
        }
        let fm = FileManager.default
        let keyframes = session.appendingPathComponent("keyframes")
        let images = session.appendingPathComponent("images")
        let hasKeyframes = ((try? fm.contentsOfDirectory(at: keyframes,
            includingPropertiesForKeys: nil)) ?? []).contains { $0.pathExtension.lowercased() == "jpg" }
        let folder = hasKeyframes ? keyframes : images
        let album = "CrateScanner \(session.lastPathComponent)"
        return try await PhotoLibraryExporter.export(imagesIn: folder, albumName: album)
    }

    /// Build the Linux package and POST it to the Tailscale worker. Returns the
    /// worker's name for the scan (used to poll fusion status).
    nonisolated func exportAndSendToWorker() async throws -> WorkerUploadResult {
        let zip = try await MainActor.run { try self.exportLinuxPackage() }
        return try await WorkerUploader.upload(zip, toWorker: WorkerSettings.shared.url)
    }

    /// Return from review to continue scanning the same object (mesh persists).
    /// Already started once, so recording resumes without another Start tap.
    func resumeScanning() {
        capturedMesh = nil
        lastSessionURL = nil
        phase = .scanning
        runSession(resetting: false, record: true)
    }

    /// Clear everything for a fresh scan and return to the pre-Start state.
    func reset() {
        gate.reset()
        nudge.reset()
        moveHint = nil
        readiness = .focusing
        guidedEnabled = false          // didSet clears the target markers
        meshes.removeAll()
        capturedVertexCount = 0
        currentBox = nil
        result = nil
        capturedMesh = nil
        lastSessionURL = nil
        rgbdFrameCount = 0
        keyframeCount = 0
        recorder.exporter = nil
        isBoxPlaced = false
        phase = .ready
        ghostAnchor?.removeFromParent()
        ghostAnchor = nil
        markersAnchor?.removeFromParent()
        markersAnchor = nil
        arrowAnchor?.removeFromParent()
        arrowAnchor = nil
        arrowEntity = nil
        runSession(resetting: true, record: false)
    }

    // MARK: Internal helpers

    /// All captured vertices across every anchor, world space.
    private func allVertices() -> [SIMD3<Float>] {
        meshes.values.flatMap { $0.vertices }
    }

    /// Update our stored box + the on-screen ghost entity together.
    private func setBox(_ box: AABB) {
        currentBox = box

        // Lazily create the anchor the first time.
        if ghostAnchor == nil, let arView {
            let anchor = AnchorEntity(world: box.center)
            anchor.addChild(ghost)
            arView.scene.addAnchor(anchor)
            ghostAnchor = anchor
        }

        ghostAnchor?.position = box.center
        ghost.update(extents: box.extents)

        // Keep the readout in sync when the user manually resizes after a fit.
        if result != nil {
            result = MeasurementResult(extentsMeters: box.extents, padding: paddingInches)
        }

        // Re-center the guided targets on the object once it's boxed/fitted.
        if guidedEnabled { buildGuidedTargets() }
    }
}

// MARK: - ARSessionDelegate

extension ScanViewModel: ARSessionDelegate {

    /// New mesh anchors: record their geometry.
    nonisolated func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        ingest(anchors)
    }

    /// Updated mesh anchors: replace their geometry (ARKit refines over time).
    nonisolated func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        ingest(anchors)
    }

    /// Removed anchors: drop their contribution.
    nonisolated func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        let ids = anchors.compactMap { ($0 as? ARMeshAnchor)?.identifier }
        Task { @MainActor in
            for id in ids { self.meshes[id] = nil }
            self.recomputeVertexCount()
        }
    }

    /// Translate ARKit tracking state into user guidance on every frame.
    /// Also samples RGB-D for the Linux pipeline while scanning.
    nonisolated func session(_ session: ARSession, didUpdate frame: ARFrame) {
        // Runs on `captureQueue`. Record synchronously, while the frame's pixel
        // buffers are still valid and owned by us — an ARFrame must never be
        // captured by a Task that outlives this call, or ARKit's buffer pool
        // drains and frame delivery stops.
        let recorded = recorder.record(frame)

        // Judge focus + steadiness on this live frame. In Detail mode the gate
        // also tells us when to auto-fire a sharp keyframe. Read the mode from
        // the thread-safe settings — `self.mode` is main-actor isolated.
        let autoKeyframes = CaptureSettings.shared.mode == .hybrid
        let (ready, autoFire) = gate.evaluate(
            frame, isRecording: recorder.isRecording, autoKeyframes: autoKeyframes)

        let state = frame.camera.trackingState
        let intensity = frame.lightEstimate?.ambientIntensity ?? 1000
        let camTransform = frame.camera.transform
        let timestamp = frame.timestamp
        Task { @MainActor in
            self.updateFeedback(state: state, ambientIntensity: intensity)
            if let recorded { self.rgbdFrameCount = recorded }
            self.readiness = ready
            if self.guidedEnabled {
                // Guided drives its own captures at target viewpoints.
                self.updateGuidance(cameraTransform: camTransform, ready: ready.isReady)
            } else if autoFire {
                self.capturePhoto()
            }
            // Nag only when they've actually stopped covering new angles.
            self.updateMoveNudge(time: timestamp, cameraTransform: camTransform)
        }
    }

    /// Extract world-space geometry from any mesh anchors in the batch.
    /// We stop ingesting once frozen so the captured mesh stays stable.
    private nonisolated func ingest(_ anchors: [ARAnchor]) {
        var batch: [(UUID, CapturedMesh)] = []
        for case let anchor as ARMeshAnchor in anchors {
            batch.append((anchor.identifier, Self.capturedMesh(from: anchor)))
        }
        guard !batch.isEmpty else { return }
        Task { @MainActor in
            guard self.phase == .scanning else { return }
            for (id, mesh) in batch { self.meshes[id] = mesh }
            self.recomputeVertexCount()
        }
    }

    private func recomputeVertexCount() {
        capturedVertexCount = meshes.values.reduce(0) { $0 + $1.vertices.count }
    }

    /// Build a world-space CapturedMesh (vertices + triangle indices) from an
    /// ARMeshAnchor. ARKit hands us raw Metal buffers; we walk them using each
    /// geometry source's stride/offset/index size to stay correct across OS
    /// versions and index widths.
    private nonisolated static func capturedMesh(from anchor: ARMeshAnchor) -> CapturedMesh {
        let geometry = anchor.geometry
        let transform = anchor.transform

        // --- Vertices (transform to world space) ---
        let vSource = geometry.vertices
        let vBuffer = vSource.buffer.contents()
        var vertices = [SIMD3<Float>]()
        vertices.reserveCapacity(vSource.count)
        for i in 0..<vSource.count {
            let ptr = vBuffer.advanced(by: vSource.offset + vSource.stride * i)
            let local = ptr.assumingMemoryBound(to: SIMD3<Float>.self).pointee
            let world = transform * SIMD4<Float>(local, 1)
            vertices.append(SIMD3<Float>(world.x, world.y, world.z))
        }

        // --- Faces (triangle index list) ---
        let faces = geometry.faces
        let indexCount = faces.count * faces.indexCountPerPrimitive
        let fBuffer = faces.buffer.contents()
        let bytesPerIndex = faces.bytesPerIndex
        var indices = [UInt32]()
        indices.reserveCapacity(indexCount)
        for i in 0..<indexCount {
            let ptr = fBuffer.advanced(by: i * bytesPerIndex)
            if bytesPerIndex == 2 {
                indices.append(UInt32(ptr.assumingMemoryBound(to: UInt16.self).pointee))
            } else {
                indices.append(ptr.assumingMemoryBound(to: UInt32.self).pointee)
            }
        }

        return CapturedMesh(vertices: vertices, indices: indices)
    }

    /// Map tracking state + light level to a single guidance message.
    private func updateFeedback(state: ARCamera.TrackingState, ambientIntensity: CGFloat) {
        // Runs in .ready too, so the banner reflects tracking/light before the
        // user presses Start. Only review freezes it.
        guard phase != .reviewing else { return }

        switch state {
        case .notAvailable:
            feedback = .trackingUnavailable
        case .limited(.excessiveMotion):
            feedback = .moveSlower
        case .limited(.insufficientFeatures):
            feedback = .needsMoreLightOrDetail
        case .limited(.initializing), .limited(.relocalizing):
            feedback = .initializing
        case .limited:
            feedback = .initializing
        case .normal:
            // Even with good tracking, a dim room hurts LiDAR + guidance overlays.
            if ambientIntensity < 500 {
                feedback = .needsMoreLightOrDetail
            } else if isBoxPlaced {
                feedback = .fitReady
            } else {
                feedback = .scanning
            }
        }
    }
}

// MARK: - Recorder isolation

/// Thread-safe holder for the RGB-D recorder.
///
/// `ScanViewModel` is `@MainActor`, but ARKit delivers frames on `captureQueue`
/// so that encoding never blocks the UI. The exporter is therefore reachable
/// from both, and this box owns that hand-off explicitly rather than leaving it
/// to an unchecked cross-actor property access.
final class RecorderBox: @unchecked Sendable {
    private let lock = NSLock()
    private var _exporter: SessionExporter?
    private var _isRecording = false
    private var _autoRecord = true

    var exporter: SessionExporter? {
        get { lock.lock(); defer { lock.unlock() }; return _exporter }
        set { lock.lock(); _exporter = newValue; lock.unlock() }
    }

    /// False while frozen in review, so a paused session records nothing.
    var isRecording: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _isRecording }
        set { lock.lock(); _isRecording = newValue; lock.unlock() }
    }

    /// True in Video mode (stream frames). False in Photo mode, where frames are
    /// only written on an explicit shutter tap via `recordPhoto`.
    var autoRecord: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _autoRecord }
        set { lock.lock(); _autoRecord = newValue; lock.unlock() }
    }

    /// Video-mode streaming path. Returns the new frame count, or nil when nothing
    /// was written (throttled, paused, photo mode, or no depth this tick) so the
    /// caller can skip a pointless hop to the main actor.
    func record(_ frame: ARFrame) -> Int? {
        lock.lock()
        let exporter = _exporter
        let recording = _isRecording && _autoRecord
        lock.unlock()          // never hold the lock across encode + file I/O

        guard recording, let exporter else { return nil }
        return writing(exporter, frame, force: false)
    }

    /// Photo-mode path: record this frame unconditionally (bypasses the rate
    /// throttle). Used for high-resolution still frames.
    func recordPhoto(_ frame: ARFrame) -> Int? {
        lock.lock()
        let exporter = _exporter
        let recording = _isRecording
        lock.unlock()

        guard recording, let exporter else { return nil }
        return writing(exporter, frame, force: true)
    }

    /// Hybrid-mode path: store a 12 MP keyframe alongside the RGB-D stream.
    /// Returns the new keyframe count.
    func recordKeyframe(_ frame: ARFrame) -> Int? {
        lock.lock()
        let exporter = _exporter
        let recording = _isRecording
        lock.unlock()

        guard recording, let exporter else { return nil }
        exporter.recordKeyframe(frame)
        return exporter.keyframeCount
    }

    private func writing(_ exporter: SessionExporter, _ frame: ARFrame, force: Bool) -> Int? {
        let before = exporter.frameCount
        exporter.record(frame, force: force)
        let after = exporter.frameCount
        return after > before ? after : nil
    }
}

// MARK: - simd / geometry helpers

extension float4x4 {
    /// The forward (−Z in view space) direction encoded in this transform.
    var forward: SIMD3<Float> {
        SIMD3<Float>(columns.2.x, columns.2.y, columns.2.z)
    }
    /// The translation component.
    var translation: SIMD3<Float> {
        SIMD3<Float>(columns.3.x, columns.3.y, columns.3.z)
    }
}

extension CGRect {
    var mid: CGPoint { CGPoint(x: midX, y: midY) }
}
