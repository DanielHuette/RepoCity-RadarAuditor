import preact from "@preact/preset-vite";
import { defineConfig } from "vitest/config";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(({ mode }) => ({
  plugins: [
    preact(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
      },
      manifest: {
        name: "RepoCity RadarAuditor",
        short_name: "RadarAuditor",
        description: "Sichtbarkeits-Audits, Berichte und Angebote",
        theme_color: "#04060B",
        background_color: "#04060B",
        display: "standalone",
        lang: "de",
        icons: [
          { src: "icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any maskable" },
        ],
      },
    }),
  ],
  // Tests read the check catalogue and the scorer sources next to app/ to stay in step with them.
  server: mode === "test" ? { fs: { allow: [".."] } } : undefined,
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "node",
  },
}));
