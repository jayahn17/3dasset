#!/usr/bin/env swift
//
//  make_app_icon.swift
//  CrateScanner
//
//  Renders the app icon — the same isometric scanned cube that CubeMark.swift
//  draws inside the app — to a 1024×1024 PNG for the asset catalog.
//
//  The icon is committed, so you only need this when the artwork changes:
//
//      cd mobile/CrateScannerApp
//      swift tools/make_app_icon.swift \
//          CrateScanner/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png
//
//  Kept as source rather than a binary-only PNG because "make the icon slightly
//  bluer" should be a one-line edit, not a round trip through a design tool.
//
//  Geometry note: the drawing is flipped to a top-left origin so the unit
//  coordinates match CubeMark.swift line for line.
//

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let side: CGFloat = 1024

// MARK: - Palette (matches CubeMark.swift)

func rgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: r, green: g, blue: b, alpha: a)
}

let backgroundTop = rgb(0.05, 0.07, 0.16)
let backgroundBottom = rgb(0.09, 0.06, 0.24)
let topFaceLight = rgb(0.42, 0.93, 1.00)
let topFaceDark = rgb(0.20, 0.66, 1.00)
let rightFaceLight = rgb(0.24, 0.55, 0.98)
let rightFaceDark = rgb(0.35, 0.24, 0.86)
let leftFaceLight = rgb(0.16, 0.33, 0.78)
let leftFaceDark = rgb(0.20, 0.13, 0.55)
let dotColor = rgb(0.55, 0.96, 1.00)
let glowColor = rgb(0.30, 0.70, 1.00, 0.55)

// MARK: - Context

let colorSpace = CGColorSpaceCreateDeviceRGB()
guard let context = CGContext(data: nil,
                              width: Int(side), height: Int(side),
                              bitsPerComponent: 8, bytesPerRow: 0,
                              space: colorSpace,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    fatalError("could not create the bitmap context")
}
context.interpolationQuality = .high
context.setAllowsAntialiasing(true)

// Top-left origin, y downward — same frame CubeMark draws in.
context.translateBy(x: 0, y: side)
context.scaleBy(x: 1, y: -1)

func gradient(_ from: CGColor, _ to: CGColor) -> CGGradient {
    CGGradient(colorsSpace: colorSpace, colors: [from, to] as CFArray, locations: [0, 1])!
}

/// Fill a path with a linear gradient running between two points.
func fill(_ path: CGPath, from: CGPoint, to: CGPoint, _ start: CGColor, _ end: CGColor) {
    context.saveGState()
    context.addPath(path)
    context.clip()
    context.drawLinearGradient(gradient(start, end), start: from, end: to,
                               options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
    context.restoreGState()
}

// MARK: - Background

fill(CGPath(rect: CGRect(x: 0, y: 0, width: side, height: side), transform: nil),
     from: .zero, to: CGPoint(x: side, y: side),
     backgroundTop, backgroundBottom)

// MARK: - Cube

// Unit hexagon, clockwise from the top, plus the centre where the faces meet.
let unitTop      = CGPoint(x:  0.000, y: -1.000)
let unitUpRight  = CGPoint(x:  0.866, y: -0.500)
let unitLowRight = CGPoint(x:  0.866, y:  0.500)
let unitBottom   = CGPoint(x:  0.000, y:  1.000)
let unitLowLeft  = CGPoint(x: -0.866, y:  0.500)
let unitUpLeft   = CGPoint(x: -0.866, y: -0.500)
let unitMiddle   = CGPoint(x:  0.000, y:  0.000)

let radius = side * 0.31
// Sat a little low so the scan dots have room above the cube.
let centre = CGPoint(x: side / 2, y: side * 0.545)

func point(_ unit: CGPoint) -> CGPoint {
    CGPoint(x: centre.x + unit.x * radius, y: centre.y + unit.y * radius)
}

func face(_ corners: [CGPoint]) -> CGPath {
    let path = CGMutablePath()
    path.move(to: point(corners[0]))
    for corner in corners.dropFirst() { path.addLine(to: point(corner)) }
    path.closeSubpath()
    return path
}

// A soft glow so the cube sits in light rather than on a flat field.
context.saveGState()
let glow = CGGradient(colorsSpace: colorSpace,
                      colors: [glowColor, rgb(0.30, 0.70, 1.00, 0)] as CFArray,
                      locations: [0, 1])!
context.drawRadialGradient(glow,
                           startCenter: centre, startRadius: 0,
                           endCenter: centre, endRadius: radius * 2.1,
                           options: [])
context.restoreGState()

fill(face([unitUpLeft, unitTop, unitUpRight, unitMiddle]),
     from: point(unitTop), to: point(unitMiddle), topFaceLight, topFaceDark)

fill(face([unitUpRight, unitLowRight, unitBottom, unitMiddle]),
     from: point(unitUpRight), to: point(unitBottom), rightFaceLight, rightFaceDark)

fill(face([unitUpLeft, unitMiddle, unitBottom, unitLowLeft]),
     from: point(unitUpLeft), to: point(unitBottom), leftFaceLight, leftFaceDark)

// Bright edges: the wireframe read, and what keeps the shape legible at 60 px.
// The three interior edges are the ones the faces actually share — centre to
// upper-left, upper-right, and bottom. Running them to the other three vertices
// instead cuts across the faces and the solid falls apart into six triangles.
let edges = CGMutablePath()
edges.addPath(face([unitTop, unitUpRight, unitLowRight, unitBottom, unitLowLeft, unitUpLeft]))
edges.move(to: point(unitUpLeft));  edges.addLine(to: point(unitMiddle))
edges.move(to: point(unitUpRight)); edges.addLine(to: point(unitMiddle))
edges.move(to: point(unitBottom));  edges.addLine(to: point(unitMiddle))

context.setStrokeColor(rgb(1, 1, 1, 0.92))
context.setLineWidth(radius * 0.045)
context.setLineCap(.round)
context.setLineJoin(.round)
context.addPath(edges)
context.strokePath()

// MARK: - Scan dots

// The object becoming data — a few captured points lifting off the top face.
let dots: [(CGFloat, CGFloat, CGFloat)] = [
    (-0.52, -0.74, 0.075), (-0.16, -0.92, 0.055), (0.22, -0.80, 0.065),
    (0.54, -0.62, 0.050), (-0.30, -1.06, 0.045), (0.06, -1.14, 0.038),
]
context.setFillColor(dotColor)
for (dx, dy, size) in dots {
    let at = point(CGPoint(x: dx, y: dy))
    let d = radius * size
    context.fillEllipse(in: CGRect(x: at.x - d, y: at.y - d, width: d * 2, height: d * 2))
}

// MARK: - Write

let outputPath = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "AppIcon-1024.png"
let url = URL(fileURLWithPath: outputPath)

guard let image = context.makeImage(),
      let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
    fatalError("could not encode \(outputPath)")
}
CGImageDestinationAddImage(destination, image, nil)
guard CGImageDestinationFinalize(destination) else {
    fatalError("could not write \(outputPath)")
}
print("wrote \(outputPath) — \(Int(side))×\(Int(side))")
