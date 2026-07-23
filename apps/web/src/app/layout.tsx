import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AuthGate } from "@/components/auth-gate";
import { Providers } from "@/lib/providers";
import "leaflet/dist/leaflet.css";
import "./globals.css";

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
