import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  // Asset pages override this with their own name and size via generateMetadata,
  // so a customer with three scans open can tell the tabs apart.
  title: "CrateScanner (Mesh & 3DGS)",
  description: "Your 3D scans — spin them, measure them, download them.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
