const { test, expect } = require("@playwright/test");

const fullId = "c-11111111111111111111111111111111";
const legacyId = "legacy";
const now = "2026-11-03T00:00:00+00:00";

function makeSummary(campaignId, values = {}) {
  return {
    campaign_id: campaignId,
    origin: campaignId === legacyId ? "legacy" : "managed",
    active: campaignId === fullId,
    status: campaignId === legacyId ? "PAUSED_NEEDS_INPUT" : "GOAL_MET",
    schema_version: campaignId === legacyId ? 1 : 2,
    mode: campaignId === legacyId ? "LEGACY" : "EXPLORATION",
    profile: campaignId === legacyId ? null : "FULL",
    flag_hash: campaignId === legacyId ? null : "a".repeat(64),
    approved_spec_hash: "b".repeat(64),
    integrity_valid: true,
    updated_at: now,
    ...values,
  };
}

function makeSnapshot(campaignId) {
  const summary = makeSummary(campaignId);
  return {
    schema_version: summary.schema_version,
    campaign_id: campaignId,
    status: summary.status,
    graph_revision: 1,
    nodes: [], edges: [], groups: [],
    as_of: now, source: "PERSISTED",
  };
}

function makeGraph(campaignId) {
  return {
    schema_version: 2, campaign_id: campaignId, status: "GOAL_MET",
    graph_revision: 1, nodes: [], edges: [], groups: [], as_of: now,
    source: "PERSISTED",
  };
}

function makeObservability(campaignId) {
  return {
    campaign_id: campaignId,
    queue: {
      available: true, states: { READY: 0, LEASED: 0, DISPATCHED: 0, UNKNOWN: 0, COMPLETED: 0, CANCELLED: 0 },
      leases: { active: 0, expired: 0, max_fence: 0 }, transitions: {},
      transition_window: "latest 10000 campaign events", worker_event_cursor: 0,
      source: "PERSISTED", as_of: now, event_cursor: 0,
    },
    budget: { available: false, dimensions: null, source: "PERSISTED", as_of: now, event_cursor: 0 },
    scheduler: {
      available: true, reason: "ALL_NODES_COMPLETE", candidate_count: 0,
      blocked_node_count: 0, source: "PERSISTED", as_of: now, event_cursor: 0,
      worker_event_cursor: 0,
    },
    source: "PERSISTED", as_of: now, event_cursor: 0,
  };
}

function makeLifecycle(campaignId) {
  return {
    campaign_id: campaignId, available: true, proposals: [], decisions: [], children: [],
    verifier_receipts: [], integration_receipts: [], protocol_handoffs: [],
    phase_state: null, evaluator_run_receipt_count: 0,
    evaluator_state: { verifier: { APPROVE: 0, REJECT: 0 }, integration: { PASS: 0, FAIL: 0 }, run_receipts: 0 },
    truncated: {},
    source: "PERSISTED", as_of: now, event_cursor: 0,
  };
}

async function installApiMocks(page) {
  const calls = new Map();
  let blockedSnapshot = null;
  let releaseBlockedSnapshot = null;
  let blockedSnapshotEntered = null;
  let resolveSnapshotEntered = null;
  const gatePromise = () => new Promise((resolve) => { releaseBlockedSnapshot = resolve; });

  await page.route("**/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (path === "/v1/campaigns" && method === "GET") {
      return route.fulfill({ json: {
        campaigns: [makeSummary(fullId), makeSummary(legacyId)],
        next_cursor: null, as_of: now, source: "PERSISTED",
      } });
    }
    const match = path.match(/^\/v1\/campaigns\/([^/]+)(?:\/(.*))?$/);
    if (!match) return route.fulfill({ status: 404, json: { error: "not_found" } });
    const [, campaignId, endpoint = "snapshot"] = match;
    if (endpoint === "snapshot" && method === "GET") {
      if (blockedSnapshot === campaignId) {
        blockedSnapshot = null;
        resolveSnapshotEntered();
        await gatePromise();
      }
      return route.fulfill({ json: makeSnapshot(campaignId) });
    }
    if (endpoint === "graph" && method === "GET") {
      if (campaignId === legacyId) return route.fulfill({ status: 404, json: { error: "legacy_graph_unavailable" } });
      return route.fulfill({ json: makeGraph(campaignId) });
    }
    if (endpoint === "observability" && method === "GET") {
      return route.fulfill({ json: makeObservability(campaignId) });
    }
    if (endpoint === "lifecycle" && method === "GET") {
      return route.fulfill({ json: makeLifecycle(campaignId) });
    }
    if (endpoint === "research" && method === "GET") {
      return route.fulfill({ json: {
        campaign_id: campaignId, scopes: [], next_cursor: null, as_of: now, source: "PERSISTED",
      } });
    }
    if (endpoint === "events" && method === "GET") {
      const key = `${campaignId}:${url.searchParams.get("after") || "0"}`;
      calls.set(key, (calls.get(key) || 0) + 1);
      if (campaignId === fullId && key.endsWith(":0") && calls.get(key) === 1) {
        return route.fulfill({ status: 410, json: { error: "cursor_expired", cursor_floor: 5, current_cursor: 8 } });
      }
      const after = Number(url.searchParams.get("after") || 0);
      return route.fulfill({ json: {
        events: after === 5 ? [{ seq: 6, type: "GRAPH_MUTATED", created_at: now, payload: { graph_revision: 3 } }] : [],
        cursor: 8, cursor_floor: 5, source: "PERSISTED", as_of: now,
      } });
    }
    if (endpoint.startsWith("commands/") && method === "POST") {
      return route.fulfill({ json: {
        command_id: "command-browser-test", request_id: "request-browser-test", status: "ACCEPTED",
      } });
    }
    return route.fulfill({ status: 404, json: { error: "not_found" } });
  });
  return {
    delayNextSnapshot(campaignId) {
      blockedSnapshot = campaignId;
      blockedSnapshotEntered = new Promise((resolve) => { resolveSnapshotEntered = resolve; });
    },
    waitForBlockedSnapshot() { return blockedSnapshotEntered; },
    release() { if (releaseBlockedSnapshot) releaseBlockedSnapshot(); },
  };
}

async function expectAxeClean(page) {
  const fs = require("node:fs");
  const axeSource = fs.readFileSync(require.resolve("axe-core"), "utf8");
  await page.route("**/__axe.js", (route) => route.fulfill({
    status: 200, contentType: "text/javascript", body: axeSource,
  }));
  await page.addScriptTag({ url: "/__axe.js" });
  const axe = await page.evaluate(async () => window.axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
  }));
  expect(axe.violations, JSON.stringify(axe.violations, null, 2)).toEqual([]);
}

test("keyboard, campaign switch, event recovery, controls, axe, and narrow viewport", async ({ page }) => {
  const mocks = await installApiMocks(page);
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.locator("#token-input")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Connect" })).toBeFocused();
  expect(await page.evaluate(() => getComputedStyle(document.activeElement).outlineWidth)).toBe("3px");

  await page.getByLabel("Bearer token").fill("browser-test-token");
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.locator("#connection")).toHaveText("Connected");
  await expect(page.locator("#notice")).toContainText("Some older events are outside retention");
  await expect(page.locator("#event-list")).toContainText("#6");
  await expect(page.locator("#summary-grid")).toContainText("FULL");
  await expect(page.locator("#lifecycle-items")).toContainText("Evaluator lifecycle totals");
  await expect(page.locator("#lifecycle-items")).toContainText("run receipts 0");
  expect(await page.evaluate(() => location.href)).not.toContain("browser-test-token");
  expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain("browser-test-token");

  await expectAxeClean(page);
  await page.setViewportSize({ width: 320, height: 800 });
  const viewport = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    overflowing: [...document.querySelectorAll("body *")]
      .map((element) => ({
        tag: element.tagName, id: element.id, className: String(element.className || ""),
        right: Math.round(element.getBoundingClientRect().right),
        text: (element.textContent || "").trim().slice(0, 80),
      }))
      .filter((element) => element.right > document.documentElement.clientWidth + 1)
      .slice(0, 20),
  }));
  expect(viewport.scrollWidth, JSON.stringify(viewport, null, 2)).toBeLessThanOrEqual(320);
  await page.setViewportSize({ width: 1280, height: 900 });

  mocks.delayNextSnapshot(fullId);
  const delayedSnapshot = page.waitForRequest((request) =>
    request.url().includes(`/v1/campaigns/${fullId}/snapshot`));
  await page.locator("#campaign-select").selectOption(fullId);
  await delayedSnapshot;
  await mocks.waitForBlockedSnapshot();
  await page.locator("#campaign-select").selectOption(legacyId);
  mocks.release();
  await expect(page.locator("#summary-grid")).toContainText("PAUSED_NEEDS_INPUT");
  await expect(page.locator("#summary-grid")).not.toContainText("GOAL_MET");
  await page.getByRole("button", { name: "Request pause" }).click();
  await expect(page.locator("#control-result")).toContainText("Intent ACCEPTED");

  await expect(page.locator("#controls")).toBeVisible();
});
