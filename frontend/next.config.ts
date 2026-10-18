import type { NextConfig } from "next";

const backendUrl = (
  process.env.PATCHTRACE_BACKEND_URL ||
  (process.env.NODE_ENV === "production" ? "http://127.0.0.1:8000" : "")
).replace(/\/$/, "");

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    // A single-container deployment proxies browser /api calls internally.
    // In local development the browser calls localhost:8000 directly instead.
    return backendUrl ? [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }] : [];
  },
};

export default nextConfig;
