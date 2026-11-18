/**
 * extension/test_content_tdz.js
 *
 * Automated regression test for BUG-03:
 * Temporal Dead Zone (TDZ) ReferenceError in extension/content.js
 *
 * How to run:
 *   node extension/test_content_tdz.js
 *   or
 *   node --test extension/test_content_tdz.js
 *
 * Test cases:
 * 1. Confirms the current extension/content.js safely handles early extension
 *    context invalidation without throwing ReferenceError (PASS).
 * 2. Confirms that placing state variable declarations lower down (pre-fix bug layout)
 *    reproduces and catches the ReferenceError (FAIL detection assertion).
 */

const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert");
const test = require("node:test");

const CONTENT_JS_PATH = path.join(__dirname, "content.js");

function createMockEnvironment(onSendMessage) {
  const mockWindow = {
    clearTimeout: (_id) => {},
    setTimeout: (_fn, _ms) => 1,
    cancelAnimationFrame: (_id) => {},
    addEventListener: (_event, _handler) => {},
    location: { href: "https://www.youtube.com/watch?v=tdz_test_video" },
    __kerzox_mediaforge_initialized: false,
  };

  const mockChrome = {
    runtime: {
      lastError: null,
      sendMessage: onSendMessage || ((_msg, cb) => cb?.(null)),
    },
  };

  const mockDocument = {
    querySelector: (_sel) => null,
    querySelectorAll: (_sel) => [],
    getElementById: (_id) => null,
    addEventListener: (_event, _handler) => {},
    createElement: (_tag) => ({
      setAttribute: () => {},
      appendChild: () => {},
      addEventListener: () => {},
    }),
    head: { appendChild: () => {} },
    body: { appendChild: () => {} },
    documentElement: { appendChild: () => {} },
  };

  const mockHistory = { pushState: () => {} };

  class MockMutationObserver {
    observe() {}
    disconnect() {}
  }

  return { mockWindow, mockChrome, mockDocument, mockHistory, MockMutationObserver };
}

test("BUG-03: Fixed content.js handles early context invalidation without TDZ ReferenceError", () => {
  const originalCode = fs.readFileSync(CONTENT_JS_PATH, "utf8");

  // Inject an early safeRuntimeSendMessage call right after its definition (before main setup)
  const injectTarget = 'function apiRequest(endpoint, method = "GET", body = null) {';
  assert.ok(originalCode.includes(injectTarget), "Target anchor must exist in content.js");

  const instrumentedCode = originalCode.replace(
    injectTarget,
    'let __tdz_captured_err = null;\n' +
    'safeRuntimeSendMessage({}, (res, err) => { __tdz_captured_err = err; });\n' +
    'if (__tdz_captured_err instanceof ReferenceError) { throw __tdz_captured_err; }\n' +
    injectTarget
  );

  const { mockWindow, mockChrome, mockDocument, mockHistory, MockMutationObserver } =
    createMockEnvironment((_msg, cb) => {
      // Simulate Chrome indicating extension context was invalidated
      mockChrome.runtime.lastError = { message: "Extension context invalidated" };
      cb(null);
    });

  let threw = false;
  let thrownError = null;

  try {
    const fn = new Function(
      "window", "chrome", "document", "history", "MutationObserver", "URL",
      instrumentedCode
    );
    fn(mockWindow, mockChrome, mockDocument, mockHistory, MockMutationObserver, URL);
  } catch (err) {
    threw = true;
    thrownError = err;
  }

  assert.strictEqual(threw, false, `Expected no error, but caught: ${thrownError?.stack}`);
});

test("BUG-03: Pre-fix buggy layout throws ReferenceError on early context invalidation", () => {
  const currentCode = fs.readFileSync(CONTENT_JS_PATH, "utf8");

  // Reconstruct pre-fix layout by moving declarations back down to line ~136
  const topDeclarations =
    '    let extensionContextInvalid = false;\n' +
    '    let statusTimer = null;\n' +
    '    let panelTimer = null;\n' +
    '    let retryTimer = null;\n' +
    '    let activeTimeouts = [];\n' +
    '    let currentPageKey = "";\n' +
    '    let activeJobId = "";\n' +
    '    const activeJobs = new Set();\n' +
    '    const notifiedJobIds = new Set();\n' +
    '    let observer = null;\n' +
    '    let pollFailureCount = 0;\n' +
    '    const MAX_POLL_FAILURES = 5;\n\n' +
    '    // Instagram floating button positioning state.\n' +
    '    let igPositionRafId = null;\n' +
    '    let igResizeObserver = null;\n' +
    '    let igTrackedVideo = null;';

  assert.ok(currentCode.includes(topDeclarations), "Top declarations block must exist in fixed code");

  // In pre-fix code, only extensionContextInvalid was at the top
  const preFixTop = '    let extensionContextInvalid = false;';

  // And the rest were placed down before clearAllTimeouts()
  const preFixBottom =
    '    let currentPageKey = "";\n' +
    '    let retryTimer = null;\n' +
    '    let statusTimer = null;\n' +
    '    let panelTimer = null;\n' +
    '    let activeJobId = "";\n' +
    '    const activeJobs = new Set();\n' +
    '    const notifiedJobIds = new Set();\n' +
    '    let activeTimeouts = [];\n' +
    '    let observer = null;\n' +
    '    let pollFailureCount = 0;\n' +
    '    const MAX_POLL_FAILURES = 5;\n\n' +
    '    // Instagram floating button positioning state.\n' +
    '    let igPositionRafId = null;\n' +
    '    let igResizeObserver = null;\n' +
    '    let igTrackedVideo = null;\n\n' +
    '    function clearAllTimeouts() {';

  const bottomAnchor = '    function clearAllTimeouts() {';

  const buggyCode = currentCode
    .replace(topDeclarations, preFixTop)
    .replace(bottomAnchor, preFixBottom);

  const injectTarget = 'function apiRequest(endpoint, method = "GET", body = null) {';
  const instrumentedBuggyCode = buggyCode.replace(
    injectTarget,
    'let __tdz_captured_err = null;\n' +
    'safeRuntimeSendMessage({}, (res, err) => { __tdz_captured_err = err; });\n' +
    'if (__tdz_captured_err instanceof ReferenceError) { throw __tdz_captured_err; }\n' +
    injectTarget
  );

  const { mockWindow, mockChrome, mockDocument, mockHistory, MockMutationObserver } =
    createMockEnvironment((_msg, cb) => {
      mockChrome.runtime.lastError = { message: "Extension context invalidated" };
      cb(null);
    });

  let threw = false;
  let thrownError = null;

  try {
    const fn = new Function(
      "window", "chrome", "document", "history", "MutationObserver", "URL",
      instrumentedBuggyCode
    );
    fn(mockWindow, mockChrome, mockDocument, mockHistory, MockMutationObserver, URL);
  } catch (err) {
    threw = true;
    thrownError = err;
  }

  assert.strictEqual(threw, true, "Buggy code MUST throw an error when declarations are in TDZ");
  assert.strictEqual(
    thrownError?.name,
    "ReferenceError",
    `Expected ReferenceError, but got ${thrownError?.name}: ${thrownError?.message}`
  );
  assert.ok(
    thrownError?.message.includes("before initialization"),
    `Expected initialization error message, got: ${thrownError?.message}`
  );
});

// Direct execution summary
if (require.main === module) {
  console.log("[test_content_tdz] All TDZ regression checks passed successfully.");
}
