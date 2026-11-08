const { chromium, expect } = require("@playwright/test");

async function main() {
  const [baseURL, campaignId] = process.argv.slice(2);
  const token = process.env.BANANLOOP_TEST_TOKEN;
  if (!baseURL || !token || !campaignId) {
    throw new Error("usage: BANANLOOP_TEST_TOKEN=... live_dashboard_probe.js BASE_URL CAMPAIGN_ID");
  }
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(`${baseURL}/`);
    await page.getByLabel("Bearer token").fill(token);
    await page.getByRole("button", { name: "Connect" }).click();
    await expect(page.locator("#connection")).toHaveText("Connected");
    await page.locator("#campaign-select").selectOption(campaignId);

    await expect(page.locator("#summary-grid")).toContainText(campaignId);
    await expect(page.locator("#summary-grid")).toContainText("OPTIMIZATION");
    await expect(page.locator("#summary-grid")).toContainText("FULL");
    await expect(page.locator("#graph-panel")).toBeVisible();
    await expect(page.locator("#graph-nodes")).toContainText("v2-optimizer-proposal-candidate-");
    await expect(page.locator("#graph-nodes")).toContainText("CONSOLIDATION · COMPLETED");
    await expect(page.locator("#phase-state")).toContainText("CONSOLIDATION");
    await expect(page.locator("#lifecycle-items")).toContainText("Integration verification · PASS");
    await expect(page.locator("#lifecycle-items")).toContainText("Evaluator run receipts");
    await expect(page.locator("#token-input")).toHaveValue("");
    expect(await page.locator("body").innerText()).not.toContain(token);
    expect(await page.evaluate(() => location.href)).not.toContain(token);
    expect(await page.evaluate(() => document.cookie)).not.toContain(token);
    expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain(token);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
