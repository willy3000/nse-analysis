/** @type {import('next').NextConfig} */
const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  {
    key: "Content-Security-Policy",
    value:
      "default-src 'self'; script-src 'self' 'unsafe-inline'" +
      (process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "") +
      "; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
  },
];

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  // The browser only ever talks to this origin; /api is proxied server-side to FastAPI (ADR-0005).
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
      { source: "/health/:path*", destination: `${API_ORIGIN}/health/:path*` },
    ];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
