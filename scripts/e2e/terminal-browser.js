const { chromium } = require("playwright-core");
const until = async (fn, ms, what) => { const end = Date.now() + ms; for (;;) { try { const v = await fn(); if (v) return v; } catch (e) {} if (Date.now() > end) throw new Error("timeout waiting for " + what); await new Promise(r => setTimeout(r, 150)); } };
(async () => {
  const [port, key] = process.argv.slice(2);
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH, args: ["--no-sandbox"] });
  const page = await browser.newPage({ viewport: { width: 1100, height: 700 } });
  const problems = [];
  page.on("console", m => { if (["error", "warning"].includes(m.type())) problems.push(m.type() + ": " + m.text()); });
  page.on("pageerror", e => problems.push("pageerror: " + e.message));
  await page.goto(`http://127.0.0.1:${port}/app?key=${key}`);
  console.log("url after key entry:", page.url());
  await until(async () => await page.locator("#wizard").isVisible(), 8000, "first-run wizard");
  await page.locator('#wizard [aria-label="닫기"]').click();      // the wizard can be dismissed
  await until(async () => !(await page.locator("#wizard").isVisible()), 3000, "wizard closed");           // key must be gone from the URL
  const tabs = await page.$$eval(".side-nav [data-view]", els => els.map(e => e.textContent.replace(/\s+/g, "")));
  console.log("sidebar views:", tabs.join(" | "));
  if (await page.locator("#view-terminal").isVisible()) throw new Error("hidden terminal view is visible on the chat view");
  // the framed pages load (same-origin iframes under the CSP)
  await page.click('.side-nav [data-view="settings"]');
  const setupFrame = page.frameLocator("#view-settings");
  console.log("settings iframe heading:", await setupFrame.locator("h1").first().textContent());
  await page.click('.side-nav [data-view="terminal"]');
  await page.waitForSelector(".xterm-rows", { timeout: 10000 });
  await until(async () => (await page.locator("#term-status").textContent()).includes("연결됨"), 10000, "terminal connected");
  await page.click(".xterm");
  await page.keyboard.type("echo E2E_$((6*7))_OK", { delay: 15 });
  await page.keyboard.press("Enter");
  await until(async () => /E2E_42_OK/.test((await page.locator(".xterm-rows").innerText()).replace(/\s+/g, "")), 10000, "echo output");
  console.log("terminal echo: E2E_42_OK seen on screen");
  await page.keyboard.type("stty size; pwd", { delay: 10 });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(600);
  const rows = (await page.locator(".xterm-rows").innerText()).split("\n").filter(Boolean).slice(-4);
  console.log("last screen lines:\n  " + rows.join("\n  "));
  await page.setViewportSize({ width: 800, height: 500 });      // resize -> fit -> server stty
  await page.waitForTimeout(500);
  await page.keyboard.type("stty size", { delay: 10 });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(600);
  console.log("after resize:", (await page.locator(".xterm-rows").innerText()).split("\n").filter(Boolean).slice(-2).join(" / "));
  
  // second visit without cookie must be locked and must not receive the token
  const fresh = await browser.newContext();
  const p2 = await fresh.newPage();
  await p2.goto(`http://127.0.0.1:${port}/app`);
  console.log("fresh context state:", await p2.getAttribute("body", "data-terminal"), "| token attr empty:", (await p2.getAttribute("body", "data-term-token")) === "");
  console.log("console problems:", problems.length ? problems.join("\n") : "none");
  await browser.close();
})().catch(e => { console.error("E2E FAILED:", e.message); process.exit(1); });
