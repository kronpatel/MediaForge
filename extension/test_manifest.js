/**
 * extension/test_manifest.js
 *
 * Automated test for BUG-15:
 * Validates manifest.json syntax and host_permissions coverage.
 *
 * How to run:
 *   node --test extension/test_manifest.js
 */

const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert");
const test = require("node:test");

const MANIFEST_PATH = path.join(__dirname, "manifest.json");

test("BUG-15: manifest.json is valid JSON and includes localhost and 127.0.0.1 host_permissions", () => {
  const content = fs.readFileSync(MANIFEST_PATH, "utf8");
  let manifest;
  try {
    manifest = JSON.parse(content);
  } catch (err) {
    assert.fail(`manifest.json is not valid JSON: ${err.message}`);
  }

  assert.ok(Array.isArray(manifest.host_permissions), "host_permissions must be an array");

  assert.ok(
    manifest.host_permissions.includes("http://127.0.0.1:*/*"),
    "host_permissions must include 'http://127.0.0.1:*/*'"
  );

  assert.ok(
    manifest.host_permissions.includes("http://localhost:*/*"),
    "BUG-15: host_permissions must include 'http://localhost:*/*'"
  );
});
