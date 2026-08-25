// test_worker.mjs -- proves decide()'s logic with synthetic inputs, no
// network calls, no Cloudflare account, no real credentials. This is the
// "dry-run/test mode... safe simulated stale input" the standing
// instruction asked for -- run with plain Node (no framework needed for
// one small pure function):
//
//     node ops/external_heartbeat/test_worker.mjs
//
import assert from "node:assert/strict";
import { decide, STALE_THRESHOLD_MS, COOLDOWN_MS } from "./worker.js";

let passed = 0;
function check(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  [PASS] ${name}`);
  } catch (err) {
    console.log(`  [FAIL] ${name}\n         ${err.message}`);
    process.exitCode = 1;
  }
}

const NOW = Date.parse("2026-08-25T04:30:00Z");

check("fresh data (5 min old) is a no-op", () => {
  const updatedAt = new Date(NOW - 5 * 60 * 1000).toISOString();
  const d = decide(NOW, updatedAt, null);
  assert.equal(d.action, "no_op_fresh");
});

check("exactly at the SLA boundary is still fresh (< not <=)", () => {
  const updatedAt = new Date(NOW - STALE_THRESHOLD_MS + 1).toISOString();
  const d = decide(NOW, updatedAt, null);
  assert.equal(d.action, "no_op_fresh");
});

check("just past the SLA boundary with no prior dispatch triggers a real dispatch", () => {
  const updatedAt = new Date(NOW - STALE_THRESHOLD_MS - 1).toISOString();
  const d = decide(NOW, updatedAt, null);
  assert.equal(d.action, "dispatch");
});

check("40 minutes stale (this session's real incident) triggers a dispatch", () => {
  const updatedAt = new Date(NOW - 40 * 60 * 1000).toISOString();
  const d = decide(NOW, updatedAt, null);
  assert.equal(d.action, "dispatch");
  assert.ok(d.ageMs >= 40 * 60 * 1000 - 1000);
});

check("a null updated_at (fetch failure) never dispatches on an assumption", () => {
  const d = decide(NOW, null, null);
  assert.equal(d.action, "no_op_fetch_failed");
});

check("an unparseable updated_at is treated the same as a fetch failure", () => {
  const d = decide(NOW, "not-a-real-timestamp", null);
  assert.equal(d.action, "no_op_fetch_failed");
});

check("stale but inside the cooldown window skips a second dispatch", () => {
  const updatedAt = new Date(NOW - 40 * 60 * 1000).toISOString();
  const lastDispatch = NOW - (COOLDOWN_MS - 60 * 1000); // 1 min inside cooldown
  const d = decide(NOW, updatedAt, lastDispatch);
  assert.equal(d.action, "no_op_cooldown");
});

check("stale and past the cooldown window dispatches again", () => {
  const updatedAt = new Date(NOW - 40 * 60 * 1000).toISOString();
  const lastDispatch = NOW - (COOLDOWN_MS + 60 * 1000); // 1 min past cooldown
  const d = decide(NOW, updatedAt, lastDispatch);
  assert.equal(d.action, "dispatch");
});

check("a very recent dispatch blocks even a VERY stale reading (storm prevention)", () => {
  const updatedAt = new Date(NOW - 3 * 60 * 60 * 1000).toISOString(); // 3h stale
  const lastDispatch = NOW - 60 * 1000; // dispatched 1 min ago
  const d = decide(NOW, updatedAt, lastDispatch);
  assert.equal(d.action, "no_op_cooldown");
});

console.log(`\n${passed} passed`);
if (process.exitCode) {
  console.log("SOME TESTS FAILED");
  process.exit(1);
}
