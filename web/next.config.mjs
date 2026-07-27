import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Without this, Turbopack walks up and finds an unrelated lockfile in the home
  // directory and infers the wrong workspace root.
  turbopack: {
    root: dirname(fileURLToPath(import.meta.url)),
  },
  env: {
    // Backend lives on loopback. Override if you run the server elsewhere.
    NEXT_PUBLIC_SERVER_URL: process.env.NEXT_PUBLIC_SERVER_URL ?? "http://127.0.0.1:8765",
  },
};

export default nextConfig;
