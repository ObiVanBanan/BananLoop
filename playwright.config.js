const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:8765",
    browserName: "chromium",
    headless: true,
    viewport: { width: 1280, height: 900 },
  },
  webServer: {
    command: "python3 tests/browser/serve_dashboard.py",
    url: "http://127.0.0.1:8765/",
    reuseExistingServer: false,
    timeout: 30000,
    env: { PYTHONPATH: "src" },
  },
});
