import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_TIMEOUT_MS, normalizeTimeoutMs, safeFailure, normalizeBaseUrl } from "../helpers.js";

test("safeFailure hides raw errors", () => {
  const result = safeFailure(new Error("connect ECONNREFUSED 127.0.0.1:8765"));
  assert.equal(result.content[0].type, "text");
  assert.match(result.content[0].text, /fusion-memory doctor/);
  assert.doesNotMatch(result.content[0].text, /ECONNREFUSED/);
});

test("normalizeBaseUrl trims trailing slash", () => {
  assert.equal(normalizeBaseUrl("http://127.0.0.1:8765/"), "http://127.0.0.1:8765");
});

test("normalizeTimeoutMs falls back for invalid values", () => {
  assert.equal(normalizeTimeoutMs(undefined), DEFAULT_TIMEOUT_MS);
  assert.equal(normalizeTimeoutMs("not-a-number"), DEFAULT_TIMEOUT_MS);
  assert.equal(normalizeTimeoutMs(0), DEFAULT_TIMEOUT_MS);
  assert.equal(normalizeTimeoutMs(-50), DEFAULT_TIMEOUT_MS);
});

test("normalizeTimeoutMs clamps runtime bounds", () => {
  assert.equal(normalizeTimeoutMs(20), 100);
  assert.equal(normalizeTimeoutMs("2500"), 2000);
  assert.equal(normalizeTimeoutMs(750), 750);
});
