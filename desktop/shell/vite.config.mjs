import { defineConfig } from "vite";

export default defineConfig({
  root: "shell",
  base: "./",
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
});
