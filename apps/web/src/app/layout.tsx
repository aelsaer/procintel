import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AuthGate } from "@/components/auth-gate";
import { Providers } from "@/lib/providers";
import "leaflet/dist/leaflet.css";
import "./globals.css";

// Per-request rendering lets Next attach the CSP nonce supplied by proxy.ts
// to every framework and application script.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Procintel",
  description: "Greek public procurement intelligence workspace",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="el">
      <body>
        <Providers>
          <AuthGate>
            <main className="site-main">{children}</main>
          </AuthGate>
        </Providers>
      </body>
    </html>
  );
}
