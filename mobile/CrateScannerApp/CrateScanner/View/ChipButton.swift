//
//  ChipButton.swift
//  CrateScanner
//
//  The app's one-tap selector: a labelled pill that fills in when it's the
//  chosen option. Used wherever a picker or a slider used to be (capture
//  quality, crate padding) so that choosing something is always a single tap on
//  a large target rather than a drag.
//
//  Written as its own type with explicit types on the styling, because inlining
//  this chain of ternaries inside a ForEach inside a stack is exactly the shape
//  that pushes SwiftUI's type checker into "unable to type-check in reasonable
//  time".
//

import SwiftUI

struct ChipButton: View {
    let title: String
    let isSelected: Bool
    let action: () -> Void

    private var background: Color { isSelected ? Color.accentColor : Color.clear }
    private var foreground: Color { isSelected ? Color.white : Color.primary }
    private var border: Color { Color.primary.opacity(isSelected ? 0 : 0.18) }

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(foreground)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(background, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(border))
    }
}

#Preview {
    HStack(spacing: 8) {
        ChipButton(title: "Fast", isSelected: false, action: {})
        ChipButton(title: "High", isSelected: true, action: {})
        ChipButton(title: "4K", isSelected: false, action: {})
    }
    .padding()
}
