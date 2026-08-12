import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Scan assets",
  description: "Processed 3D scans — download the file that matches your tool.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
