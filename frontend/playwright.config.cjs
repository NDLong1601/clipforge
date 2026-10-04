const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './e2e', workers: 1, fullyParallel: false, retries: 0, timeout: 35000,
  reporter: [['list'], ['json', { outputFile: process.env.CLIPFORGE_BROWSER_REPORT }]],
  outputDir: process.env.CLIPFORGE_BROWSER_ARTIFACTS,
  use: { baseURL: process.env.CLIPFORGE_BROWSER_URL, channel: process.env.CLIPFORGE_BROWSER_CHANNEL || 'chrome',
    headless: true, viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
