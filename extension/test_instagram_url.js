/**
 * extension/test_instagram_url.js
 *
 * Automated regression test for BUG-16:
 * Instagram /tv/ URL Pattern matching in extension/content.js
 *
 * How to run:
 *   node --test extension/test_instagram_url.js
 */

const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert");
const test = require("node:test");

const CONTENT_JS_PATH = path.join(__dirname, "content.js");

test("BUG-16: Instagram URL regex in content.js matches /reel/, /reels/, /p/, /tv/ and rejects /stories/", () => {
  const content = fs.readFileSync(CONTENT_JS_PATH, "utf8");

  // Verify the regex pattern literal is in content.js
  assert.ok(
    content.includes("/^\\/(reel|reels|p|tv)\\/([^/?#]+)/"),
    "Updated Instagram regex must exist in content.js"
  );

  const regex = /^\/(reel|reels|p|tv)\/([^/?#]+)/;

  // Test /reel/<id>
  const reelMatch = "/reel/C123_abc".match(regex);
  assert.ok(reelMatch, "/reel/ path must match");
  assert.strictEqual(reelMatch[1], "reel");
  assert.strictEqual(reelMatch[2], "C123_abc");

  // Test /reels/<id>
  const reelsMatch = "/reels/C456_def".match(regex);
  assert.ok(reelsMatch, "/reels/ path must match");
  assert.strictEqual(reelsMatch[1], "reels");
  assert.strictEqual(reelsMatch[2], "C456_def");

  // Test /p/<id>
  const postMatch = "/p/B789_ghi".match(regex);
  assert.ok(postMatch, "/p/ path must match");
  assert.strictEqual(postMatch[1], "p");
  assert.strictEqual(postMatch[2], "B789_ghi");

  // Test /tv/<id> (BUG-16)
  const tvMatch = "/tv/D999_xyz".match(regex);
  assert.ok(tvMatch, "BUG-16: /tv/ path must match");
  assert.strictEqual(tvMatch[1], "tv");
  assert.strictEqual(tvMatch[2], "D999_xyz");

  // Non-video paths must NOT match
  assert.strictEqual("/stories/user/123/".match(regex), null, "/stories/ must not match");
  assert.strictEqual("/explore/".match(regex), null, "/explore/ must not match");
  assert.strictEqual("/direct/t/123/".match(regex), null, "/direct/ must not match");
});

test("BUG-16: getPageInfo() on an Instagram /tv/ URL identifies video and id", () => {
  const content = fs.readFileSync(CONTENT_JS_PATH, "utf8");

  // Instrument to expose getPageInfo on window for test verification
  const testCode = content.replace(
    "function getPageInfo() {",
    "window.__testGetPageInfo = getPageInfo;\nfunction getPageInfo() {"
  );

  const mockWindow = {
    clearTimeout: () => {},
    setTimeout: () => 1,
    cancelAnimationFrame: () => {},
    addEventListener: () => {},
    location: { href: "https://www.instagram.com/tv/C999_xyz/?igshid=abc" },
    __kerzox_mediaforge_initialized: false,
  };

  const mockChrome = {
    runtime: { lastError: null, sendMessage: () => {} },
  };

  const mockDocument = {
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    addEventListener: () => {},
    createElement: () => ({
      setAttribute: () => {},
      appendChild: () => {},
      addEventListener: () => {},
    }),
    head: { appendChild: () => {} },
    body: { appendChild: () => {} },
    documentElement: { appendChild: () => {} },
  };

  const fn = new Function(
    "window", "chrome", "document", "history", "MutationObserver", "URL",
    testCode
  );
  fn(mockWindow, mockChrome, mockDocument, { pushState: () => {} }, class { observe() {} disconnect() {} }, URL);

  assert.strictEqual(typeof mockWindow.__testGetPageInfo, "function");

  const page = mockWindow.__testGetPageInfo();
  assert.strictEqual(page.isInstagram, true, "Must be identified as Instagram");
  assert.strictEqual(page.isInstagramVideo, true, "BUG-16: /tv/ URL must be recognized as isInstagramVideo");
  assert.strictEqual(page.instagramId, "C999_xyz", "Must extract Instagram media id correctly");
});
