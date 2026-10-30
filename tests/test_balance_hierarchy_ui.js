/* Run with Node: test UI hierarchy and DOM structure in #behavior tab.
 * Validates real source files: index.html DOM hierarchy & collapsed drawer/calendar/details,
 * app.js energy-balance-details and pv-clip-details collapsed folds & duplicate AC table removal,
 * and style.css theme-aware drawer and heatmap rules.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

console.log("--- Running Behavior Section Hierarchy & Source UI Tests ---");

const htmlSource = fs.readFileSync(path.join(__dirname, "../feasibility/web/index.html"), "utf8");
const appSource = fs.readFileSync(path.join(__dirname, "../feasibility/web/app.js"), "utf8");
const cssSource = fs.readFileSync(path.join(__dirname, "../feasibility/web/style.css"), "utf8");

// =========================================================================
// 1. REAL SOURCE: index.html #behavior DOM Hierarchy & Collapsed State
// =========================================================================
const behStart = htmlSource.indexOf("<section id=\"behavior\"");
assert.ok(behStart !== -1, "Section #behavior exists in index.html");
const behEnd = htmlSource.indexOf("<section id=\"prices\"", behStart);
assert.ok(behEnd !== -1, "End of section #behavior located");
const behaviorHtml = htmlSource.slice(behStart, behEnd);

// Structural element IDs
const idxAnnualFlow = behaviorHtml.indexOf("id=\"annualEnergyFlowArticle\"");
const idxTypicalDay = behaviorHtml.indexOf("id=\"typicalDayArticle\"");
const idxClipping = behaviorHtml.indexOf("id=\"pvClippingArticle\"");
const idxSwitchHour = behaviorHtml.indexOf("id=\"switchHourOptimizer\"");
const idxMonthHour = behaviorHtml.indexOf("id=\"monthHourHeatmapArticle\"");
const idxDayHourFold = behaviorHtml.indexOf("id=\"dayhourExplorerFold\"");
const idxHvacDrawer = behaviorHtml.indexOf("id=\"hvacLoadDrawer\"");

assert.ok(idxAnnualFlow !== -1, "annualEnergyFlowArticle found in #behavior");
assert.ok(idxTypicalDay !== -1, "typicalDayArticle found in #behavior");
assert.ok(idxClipping !== -1, "pvClippingArticle found in #behavior");
assert.ok(idxSwitchHour !== -1, "switchHourOptimizer found in #behavior");
assert.ok(idxMonthHour !== -1, "monthHourHeatmapArticle found in #behavior");
assert.ok(idxDayHourFold !== -1, "dayhourExplorerFold found in #behavior");
assert.ok(idxHvacDrawer !== -1, "hvacLoadDrawer found in #behavior");

// Strict DOM Order Verification
assert.ok(idxAnnualFlow < idxTypicalDay, "1. Annual Sankey precedes 24h Hourly Profile");
assert.ok(idxTypicalDay < idxClipping, "2. 24h Hourly Profile precedes PV Clipping Article");
assert.ok(idxClipping < idxSwitchHour, "3. PV Clipping Article precedes Switch-Hour Optimizer");
assert.ok(idxSwitchHour < idxMonthHour, "4. Switch-Hour Optimizer precedes Month-Hour Heatmap");
assert.ok(idxMonthHour < idxDayHourFold, "5. Month-Hour Heatmap precedes Dayhour Calendar Explorer");
assert.ok(idxDayHourFold < idxHvacDrawer, "6. Dayhour Calendar Explorer precedes bottom HVAC Drawer");
console.log("✔ Test 1: DOM Hierarchy ordering in index.html verified");

// Details Collapsed State Verification in index.html
assert.doesNotMatch(behaviorHtml, /<details[^>]*id="dayhourExplorerFold"[^>]*open/i, "dayhourExplorerFold must be collapsed (no open attribute)");
assert.doesNotMatch(behaviorHtml, /<details[^>]*id="hvacLoadDrawer"[^>]*open/i, "hvacLoadDrawer must be collapsed (no open attribute)");
console.log("✔ Test 2: Collapsed details state verified (calendar fold and HVAC drawer)");

// Balance View Toggle Buttons and Visual Containers
const annualBtnMatch = behaviorHtml.match(/<button[^>]*data-balance-view=["']annual["'][^>]*>([\s\S]*?)<\/button>/i);
assert.ok(annualBtnMatch, "Annual balance view button exists");
assert.match(annualBtnMatch[0], /class="[^"]*active[^"]*"/, "Annual toggle button has active class");
assert.match(annualBtnMatch[0], /aria-pressed=["']true["']/, "Annual toggle button has aria-pressed=\"true\"");
assert.match(annualBtnMatch[1], /Annual\s+Sankey/, "Annual toggle button label is Annual Sankey");

const monthlyBtnMatch = behaviorHtml.match(/<button[^>]*data-balance-view=["']monthly["'][^>]*>([\s\S]*?)<\/button>/i);
assert.ok(monthlyBtnMatch, "Monthly balance view button exists");
assert.match(monthlyBtnMatch[0], /aria-pressed=["']false["']/, "Monthly toggle button has aria-pressed=\"false\"");
assert.match(monthlyBtnMatch[1], /Monthly\s+Balance\s+\(Jan–Dec\)/, "Monthly toggle button label is Monthly Balance (Jan–Dec)");

const annualVisMatch = behaviorHtml.match(/<div[^>]*id="annualEnergyFlowVisual"[^>]*>/);
assert.ok(annualVisMatch, "annualEnergyFlowVisual container exists");
assert.doesNotMatch(annualVisMatch[0], /hidden/, "annualEnergyFlowVisual is visible by default");

const monthlyVisMatch = behaviorHtml.match(/<div[^>]*id="monthlyBalanceVisual"[^>]*>/);
assert.ok(monthlyVisMatch, "monthlyBalanceVisual container exists");
assert.match(monthlyVisMatch[0], /hidden/, "monthlyBalanceVisual is hidden by default");
console.log("✔ Test 3: Balance toggle buttons and adjacent visual containers verified");

// HVAC Drawer Content and Summary
const hvacDrawerMatch = behaviorHtml.match(/<details[^>]*id="hvacLoadDrawer"[\s\S]*?<\/details>/);
assert.ok(hvacDrawerMatch, "hvacLoadDrawer details container exists");
const hvacContent = hvacDrawerMatch[0];
assert.match(hvacContent, /<summary id="hvacLoadSummary">Electrified HVAC load model<\/summary>/, "HVAC drawer summary label matches");
assert.match(hvacContent, /id="hpContextNoticeBehavior"[^>]*hidden/, "hpContextNoticeBehavior preserved inside drawer");
assert.match(hvacContent, /id="hpBaselineNoticeBehavior"[^>]*hidden/, "hpBaselineNoticeBehavior preserved inside drawer");
assert.match(hvacContent, /id="heatPumpReportPanel"[^>]*hidden/, "heatPumpReportPanel preserved inside drawer");
assert.match(hvacContent, /id="acHeatingComparisonArticle"[^>]*hidden/, "acHeatingComparisonArticle preserved inside drawer");
console.log("✔ Test 4: HVAC Drawer elements and hidden attributes preserved");

// =========================================================================
// 2. REAL SOURCE: app.js Collapsed Details Folds & Duplicate AC Table Removal
// =========================================================================
// Collapsed Energy balance details
assert.match(appSource, /<details\s+class="energy-balance-details">/, "Native details tag used for energy balance fold in app.js");
assert.doesNotMatch(appSource, /<details\s+class="energy-balance-details"\s+open>/, "Energy balance details fold must start collapsed in app.js");
assert.match(appSource, /<summary>Energy balance details<\/summary>/, "Summary for energy balance matches in app.js");

// Collapsed PV clipping details
assert.match(appSource, /<details\s+class="pv-clip-details">/, "Native details tag used for PV clipping in app.js");
assert.doesNotMatch(appSource, /<details\s+class="pv-clip-details"\s+open>/, "PV clipping details must start collapsed in app.js");
assert.match(appSource, /<summary>Monthly clipping,\s*usable PV &amp; SoC breakdown table/, "Summary for PV clipping details matches in app.js");
console.log("✔ Test 5: Collapsed Energy balance and PV clipping details folds verified in app.js");

// Duplicate AC Table Removed
const acFnStart = appSource.indexOf("function renderAcHeatingComparison(");
assert.ok(acFnStart !== -1, "renderAcHeatingComparison defined in app.js");
const acFnEnd = appSource.indexOf("\nfunction ", acFnStart + 20);
const acFnSource = appSource.slice(acFnStart, acFnEnd !== -1 ? acFnEnd : undefined);
assert.doesNotMatch(acFnSource, /<table[\s>]/, "No duplicate HTML table rendered in renderAcHeatingComparison");
assert.doesNotMatch(acFnSource, /<th[\s>]/, "No table headers rendered in renderAcHeatingComparison");
assert.match(acFnSource, /ac-heating-grid/, "AC heating uses card grid layout");
console.log("✔ Test 6: Duplicate AC table removed from renderAcHeatingComparison in app.js");

// =========================================================================
// 3. REAL SOURCE: style.css Theme-Aware Drawer and Heatmap Styles
// =========================================================================
assert.match(cssSource, /#hvacLoadDrawer\s*\{[^}]*border:[^;]*var\(--line/, "HVAC drawer uses theme-aware --line border");
assert.match(cssSource, /#hvacLoadDrawer>summary\s*\{[^}]*color:\s*var\(--ink\)/, "HVAC drawer summary uses theme-aware --ink");
assert.match(cssSource, /#hvacLoadDrawer>summary::marker\s*\{[^}]*color:\s*var\(--green\)/, "HVAC drawer summary marker uses theme-aware --green");

assert.match(cssSource, /#monthHourHeatmap\s+svg\s*\{[^}]*--heatmap-low:/, "Heatmap defines base theme CSS variable --heatmap-low");
assert.match(cssSource, /\[data-theme="dark"\]\s+#monthHourHeatmap\s+svg\s*\{[^}]*--heatmap-low:/, "Heatmap defines dark theme variable --heatmap-low");
assert.match(cssSource, /\[data-theme="dark"\]\s+#monthHourHeatmap\s+svg\s*\{[^}]*--heatmap-high:/, "Heatmap defines dark theme variable --heatmap-high");
assert.match(cssSource, /\[data-theme="dark"\]\s+#monthHourHeatmap\s+svg\[data-dispatch-metric="clipping"\]/, "Heatmap dark theme styles clipping metric");
console.log("✔ Test 7: Theme-aware drawer and heatmap styles verified in style.css");

console.log("\nALL BEHAVIOR HIERARCHY AND SOURCE TESTS PASSED SUCCESSFULLY!");
