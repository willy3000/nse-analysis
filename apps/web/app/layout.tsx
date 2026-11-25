import type { Metadata } from "next";
import type { ReactNode } from "react";

import { themeBootstrapScript } from "@/lib/theme";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "NSE Intelligence",
  description: "Investment research terminal for the Nairobi Securities Exchange (decision support only).",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applies the stored theme before first paint (dark by default) so the page never flashes light. */}
        <script dangerouslySetInnerHTML={{ __html: themeBootstrapScript }} />
      </head>
      <body className="min-h-screen font-sans text-sm">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
