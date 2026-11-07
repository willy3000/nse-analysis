import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "NSE Intelligence",
  description: "Investment research terminal for the Nairobi Securities Exchange (decision support only).",
};

// Applies the stored theme before first paint (dark by default) to avoid a flash.
const themeScript = `try{var t=localStorage.getItem('nse.theme');if(t!=='light')document.documentElement.classList.add('dark')}catch(e){document.documentElement.classList.add('dark')}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen font-sans text-sm">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
