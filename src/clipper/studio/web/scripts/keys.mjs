// Keyboard smoke test against a running Control Center (read-only: opens, moves, closes).
//   node scripts/keys.mjs [baseUrl]
import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://127.0.0.1:8766";
const browser = await chromium.launch({ channel: "msedge" });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 960 } })).newPage();
const results = [];
const check = (name, ok) => results.push(`${ok ? "PASS" : "FAIL"}  ${name}`);

await page.goto(base + "/clips", { waitUntil: "domcontentloaded" });
await page.waitForSelector("[data-clip]");

await page.keyboard.press("Control+k");
await page.keyboard.type("chem");
await page.waitForTimeout(300);
const first = await page.locator("[cmdk-item]").first().innerText();
check(`palette: "chem" puts the chemistry clip first (${first.split("\n")[0]})`, /chemistry/i.test(first));
const items = await page.locator("[cmdk-item]").count();
check(`palette: no loose matches (${items} results)`, items <= 3);
await page.keyboard.press("Escape");

await page.keyboard.press("j");
await page.keyboard.press("j");
const focused = await page.locator("[data-clip].ring-1").count();
check("J moves the focus ring", focused === 1);
await page.keyboard.press("Enter");
await page.waitForSelector("[role=dialog]");
const title1 = await page.locator("[role=dialog] h2").innerText();
await page.keyboard.press("j");
await page.waitForTimeout(200);
const title2 = await page.locator("[role=dialog] h2").innerText();
check(`Enter opens the clip, J goes to the next (${title1} -> ${title2})`, title1 !== title2);
await page.keyboard.press("Escape");
await page.waitForTimeout(300);
check("Esc closes it", (await page.locator("[role=dialog]").count()) === 0);

await page.keyboard.press("g");
await page.keyboard.press("u");
await page.waitForURL("**/submissions");
check("G then U goes to Submissions", page.url().endsWith("/submissions"));
await page.keyboard.press("?");
await page.waitForSelector("text=Keyboard shortcuts");
check("? opens the shortcut sheet", true);

console.log(results.join("\n"));
await browser.close();
