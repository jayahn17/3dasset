//
//  ShareSheet.swift
//  CrateScanner
//
//  Thin SwiftUI wrapper around UIActivityViewController so we can hand the
//  exported mesh file (STL/OBJ/USDZ) to the system share sheet — AirDrop,
//  Files, Mail, etc. For the MVP this is how a measurement "leaves" the device
//  in place of a backend upload.
//

import SwiftUI
import UIKit

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
