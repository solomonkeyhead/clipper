// Screenshots of every page, for reviewing the design:
//   node scripts/shots.mjs [baseUrl] [outDir] [theme] [width] [height]
// Drives the installed Microsoft Edge through Playwright (no browser download).
import { chromium } from "playwright-core";

const [base = "http://127.0.0.1:8766", out = ".", theme = "dark", width = "1440", height = "960"] =
  process.argv.slice(2);
const pages = ["/", "/campaigns", "/campaigns/chad-powers-s2", "/clips", "/queue", "/submissions",
               "/stats", "/accounts", "/settings"];

const browser = await chromium.launch({ channel: "msedge" });
const context = await browser.newContext({
  viewport: { width: Number(width), height: Number(height) },
  colorScheme: theme === "light" ? "light" : "dark",
  deviceScaleFactor: 1,
});
await context.addInitScript((t) => localStorage.setItem("clipper.theme", t), theme);
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`${page.url()}: ${e.message}`));
page.on("console", (m) => m.type() === "error" && errors.push(`${page.url()}: ${m.text()}`));

for (const path of pages) {
  await page.goto(base + path, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("main h1", { timeout: 15000 });
  await page.waitForTimeout(1200);          // thumbnails and fonts
  const name = path === "/" ? "home" : path.slice(1).replaceAll("/", "_");
  await page.screenshot({ path: `${out}/ui_${theme}_${width}_${name}.png` });
  console.log("shot", name);
}
if (process.argv.includes("--sheet")) {
  await page.goto(base + "/clips", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("[data-clip]");
  await page.click("[data-clip]");
  await page.waitForSelector("[role=dialog]");
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/ui_${theme}_${width}_sheet.png` });
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+k");
  await page.waitForTimeout(400);
  await page.keyboard.type("chem");
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${out}/ui_${theme}_${width}_palette.png` });
  console.log("shot sheet, palette");
}
console.log(errors.length ? "ERRORS:\n" + errors.join("\n") : "no console errors");
await browser.close();
