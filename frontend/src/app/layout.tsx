import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SkySim AI — Autonomous Drone Flight Pre-Visualization & Trajectory Engine",
  description:
    "Spatial 3D trajectory planning, cubic B-spline smoothing, Gemini Ultra framing audit, and FAA Part 107 compliant mission exports (DJI Waypoint 3.0, KML, PX4).",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased">
        {children}
      </body>
    </html>
  );
}
