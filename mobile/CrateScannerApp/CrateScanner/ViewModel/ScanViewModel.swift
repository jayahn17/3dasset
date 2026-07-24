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

/// Which stage of the flow we're in.
enum ScanPhase: Equatable {
    /// Live scanning: mesh streaming in, box placement/fit available.
    case scanning
    /// Frozen: a captured mesh + result exist, ready to review/orbit/export.
    case reviewing
}

@MainActor
final class ScanViewModel: NSObject, ObservableObject {

    // MARK: Published UI state

    /// Current stage of the flow.
    @Published private(set) var phase: ScanPhase = .scanning

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

    /// The anchored ghost box, added to the scene when the user places it.
    private var ghost = GhostBoxEntity()
    private var ghostAnchor: AnchorEntity?

    /// World-space geometry of the LiDAR mesh, keyed by mesh anchor id so we can
    /// replace an anchor's contribution wholesale when ARKit refines it.
    private var meshes: [UUID: CapturedMesh] = [:]

    /// Current ghost box in world space. Nil until placed.
    private var currentBox: AABB?

    // MARK: Session lifecycle

    /// Wire this view model to the ARView and start scene reconstruction.
    func attach(to arView: ARView) {
        self.arView = arView
        // Order matters: the queue must be set before the delegate, or the
        // first callbacks still land on the main queue.
        arView.session.delegateQueue = captureQueue
        arView.session.delegate = self
        runSession(resetting: true)
    }

    /// Configure + run the world-tracking session with mesh reconstruction.
    private func runSession(resetting: Bool) {
        guard let arView else { return }

        let config = ARWorldTrackingConfiguration()
        // The whole point: ask ARKit for the reconstructed LiDAR mesh.
        config.sceneReconstruction = .mesh
        config.frameSemantics.insert(.sceneDepth)
        config.environmentTexturing = .none

        // We render the mesh ourselves via RealityKit's debug visualization so
        // the user literally sees coverage fill in and where the gaps are.
        arView.debugOptions.insert(.showSceneUnderstanding)

        let options: ARSession.RunOptions = resetting
            ? [.resetTracking, .removeExistingAnchors]
            : []
        arView.session.run(config, options: options)

        // Fresh RGB-D recorder on reset; keep accumulating if resuming.
        if resetting || recorder.exporter == nil {
            recorder.exporter = try? SessionExporter(minInterval: 0.1) // ~10 Hz
            rgbdFrameCount = 0
            lastSessionURL = nil
        }
        recorder.isRecording = true
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

    /// Nudge the placed box across the ground plane (x/z) from a drag gesture.
    /// `translation` is in meters of world movement.
    func moveBox(byWorldXZ translation: SIMD2<Float>) {
        guard let box = currentBox else { return }
        setBox(AABB(center: box.center + SIMD3(translation.x, 0, translation.y),
                    extents: box.extents))
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
    func exportLinuxPackage() throws -> URL {
        let base = result.map { String($0.id.uuidString.prefix(8)) } ?? UUID().uuidString.prefix(8).description
        return try PackageExporter.buildZip(
            mesh: capturedMesh,
            measurement: result,
            sessionDirectory: lastSessionURL,
            baseName: base
        )
    }

    /// Zip only the RGB-D session folder (smaller than full package).
    func exportSessionZip() throws -> URL {
        guard let session = lastSessionURL else {
            throw PackageExportError.nothingToExport
        }
        return try PackageExporter.zipForSharing(session)
    }

    /// Return from review to continue scanning the same object (mesh persists).
    func resumeScanning() {
        capturedMesh = nil
        lastSessionURL = nil
        phase = .scanning
        runSession(resetting: false)
    }

    /// Clear everything for a fresh scan.
    func reset() {
        meshes.removeAll()
        capturedVertexCount = 0
        currentBox = nil
        result = nil
        capturedMesh = nil
        lastSessionURL = nil
        rgbdFrameCount = 0
        recorder.exporter = nil
        isBoxPlaced = false
        phase = .scanning
        ghostAnchor?.removeFromParent()
        ghostAnchor = nil
        runSession(resetting: true)
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

        let state = frame.camera.trackingState
        let intensity = frame.lightEstimate?.ambientIntensity ?? 1000
        Task { @MainActor in
            self.updateFeedback(state: state, ambientIntensity: intensity)
            if let recorded { self.rgbdFrameCount = recorded }
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
        // Don't overwrite guidance while frozen in review.
        guard phase == .scanning else { return }

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

    var exporter: SessionExporter? {
        get { lock.lock(); defer { lock.unlock() }; return _exporter }
        set { lock.lock(); _exporter = newValue; lock.unlock() }
    }

    /// False while frozen in review, so a paused session records nothing.
    var isRecording: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _isRecording }
        set { lock.lock(); _isRecording = newValue; lock.unlock() }
    }

    /// Record this frame if enabled. Returns the new frame count, or nil when
    /// nothing was written (throttled, paused, or no depth on this tick) so the
    /// caller can skip a pointless hop to the main actor.
    func record(_ frame: ARFrame) -> Int? {
        lock.lock()
        let exporter = _exporter
        let recording = _isRecording
        lock.unlock()          // never hold the lock across encode + file I/O

        guard recording, let exporter else { return nil }
        let before = exporter.frameCount
        exporter.maybeRecord(frame)
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
