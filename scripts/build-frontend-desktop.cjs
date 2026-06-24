#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const path = require("node:path");

const rootDir = path.resolve(__dirname, "..");
const frontendDir = path.join(rootDir, "frontend");
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";

const result = spawnSync(npmCommand, ["run", "build"], {
  cwd: frontendDir,
  env: {
    ...process.env,
    VITE_API_BASE_URL: "http://127.0.0.1:8765",
  },
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error);
  process.exit(1);
}

process.exit(result.status ?? 1);
