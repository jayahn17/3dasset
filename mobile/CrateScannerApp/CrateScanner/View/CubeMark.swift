//
//  CubeMark.swift
//  CrateScanner
//
//  The app's mark: an isometric cube being scanned, drawn as vectors so the same
//  artwork is used by the intro screen at any size. It is deliberately the same
//  geometry the app icon is rendered from (tools/make_app_icon.swift), so the
//  icon on the home screen and the hero on the intro page read as one thing.
//
//  Isometric layout — a hexagon split into three rhombi. Unit coordinates run
//  from −1 to 1 about the centre; `size` scales the whole thing.
//

import SwiftUI

struct CubeMark: View {
    var size: CGFloat = 120
    /// Draw the scan dots (the "point cloud" being captured off the top face).
    var showsScanDots = true

    // Hexagon corners, clockwise from the top, plus the centre where the three
    // visible faces meet.
    private static let top      = CGPoint(x:  0.000, y: -1.000)
    private static let upRight  = CGPoint(x:  0.866, y: -0.500)
    private static let lowRight = CGPoint(x:  0.866, y:  0.500)
    private static let bottom   = CGPoint(x:  0.000, y:  1.000)
    private static let lowLeft  = CGPoint(x: -0.866, y:  0.500)
    private static let upLeft   = CGPoint(x: -0.866, y: -0.500)
    private static let middle   = CGPoint(x:  0.000, y:  0.000)

    var body: some View {
        Canvas { context, canvasSize in
            let r = min(canvasSize.width, canvasSize.height) / 2 * 0.86
            let c = CGPoint(x: canvasSize.width / 2, y: canvasSize.height / 2)
            func p(_ unit: CGPoint) -> CGPoint {
                CGPoint(x: c.x + unit.x * r, y: c.y + unit.y * r)
            }
            func face(_ corners: [CGPoint]) -> Path {
                var path = Path()
                path.move(to: p(corners[0]))
                for corner in corners.dropFirst() { path.addLine(to: p(corner)) }
                path.closeSubpath()
                return path
            }

            // Three faces, lit from above: the top catches the light, the right
            // face falls off, the left face is in shadow. That value ordering is
            // what makes a flat hexagon read as a solid.
            let topFace = face([Self.upLeft, Self.top, Self.upRight, Self.middle])
            let rightFace = face([Self.upRight, Self.lowRight, Self.bottom, Self.middle])
            let leftFace = face([Self.upLeft, Self.middle, Self.bottom, Self.lowLeft])

            context.fill(topFace, with: .linearGradient(
                Gradient(colors: [Color(red: 0.42, green: 0.93, blue: 1.00),
                                  Color(red: 0.20, green: 0.66, blue: 1.00)]),
                startPoint: p(Self.top), endPoint: p(Self.middle)))

            context.fill(rightFace, with: .linearGradient(
                Gradient(colors: [Color(red: 0.24, green: 0.55, blue: 0.98),
                                  Color(red: 0.35, green: 0.24, blue: 0.86)]),
                startPoint: p(Self.upRight), endPoint: p(Self.bottom)))

            context.fill(leftFace, with: .linearGradient(
                Gradient(colors: [Color(red: 0.16, green: 0.33, blue: 0.78),
                                  Color(red: 0.20, green: 0.13, blue: 0.55)]),
                startPoint: p(Self.upLeft), endPoint: p(Self.bottom)))

            // Bright edges — the "scanned wireframe" look, and what keeps the
            // shape legible at home-screen size.
            // The three interior edges are the ones the faces share: centre to
            // upper-left, upper-right, and bottom.
            var edges = Path()
            edges.addPath(face([Self.top, Self.upRight, Self.lowRight,
                                Self.bottom, Self.lowLeft, Self.upLeft]))
            edges.move(to: p(Self.upLeft));  edges.addLine(to: p(Self.middle))
            edges.move(to: p(Self.upRight)); edges.addLine(to: p(Self.middle))
            edges.move(to: p(Self.bottom));  edges.addLine(to: p(Self.middle))
            context.stroke(edges, with: .color(.white.opacity(0.92)),
                           style: StrokeStyle(lineWidth: max(1, r * 0.045),
                                              lineCap: .round, lineJoin: .round))

            guard showsScanDots else { return }

            // A scatter of captured points lifting off the top face: the object
            // becoming data, which is the whole job of the app.
            let dots: [(CGFloat, CGFloat, CGFloat)] = [
                (-0.52, -0.74, 0.075), (-0.16, -0.92, 0.055), (0.22, -0.80, 0.065),
                (0.54, -0.62, 0.050), (-0.30, -1.06, 0.045), (0.06, -1.14, 0.038),
            ]
            for (dx, dy, size) in dots {
                let at = p(CGPoint(x: dx, y: dy))
                let d = r * size
                context.fill(Path(ellipseIn: CGRect(x: at.x - d, y: at.y - d,
                                                    width: d * 2, height: d * 2)),
                             with: .color(Color(red: 0.55, green: 0.96, blue: 1.0)))
            }
        }
        .frame(width: size, height: size)
        .shadow(color: Color(red: 0.30, green: 0.70, blue: 1.0).opacity(0.55),
                radius: size * 0.14)
        .accessibilityHidden(true)
    }
}

#Preview {
    VStack(spacing: 24) {
        CubeMark(size: 160)
        CubeMark(size: 60)
    }
    .padding(40)
    .background(Color.black)
}
