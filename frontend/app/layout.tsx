import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PatchTrace — Contributions with receipts",
  description: "Evidence-backed open-source contribution workflow.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
