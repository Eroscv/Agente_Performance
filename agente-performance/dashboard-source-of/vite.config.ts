import { defineConfig } from "vite";

// Relative base so the build works whether it's served at the domain root
// or under a GitHub Pages project path (https://<user>.github.io/<repo>/...).
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
  },
});
