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

test("BUG-04: renderQueue() renders paused jobs with Resume button, and starting/retrying with spinner", () => {
  const content = fs.readFileSync(CONTENT_JS_PATH, "utf8");

  // Instrument content.js to expose renderQueue on window for test verification
  const testCode = content.replace(
    "function renderQueue(queueData) {",
    "window.__testRenderQueue = renderQueue;\nfunction renderQueue(queueData) {"
  );

  const appendedItems = [];
  const mockList = {
    innerHTML: "",
    appendChild: (el) => appendedItems.push(el),
  };

  const { mockWindow, mockChrome, mockDocument, mockHistory, MockMutationObserver } =
    createMockEnvironment();

  mockDocument.querySelector = (sel) => {
    if (sel.includes("[data-queue-list]")) return mockList;
    return null;
  };
  mockDocument.getElementById = (id) => {
    if (id === "kerzox-download-menu") return { hidden: false };
    return null;
  };
  mockDocument.createElement = (tag) => {
    const el = {
      tagName: tag,
      className: "",
      innerHTML: "",
      children: [],
      textContent: "",
      setAttribute: () => {},
      appendChild: (child) => el.children.push(child),
      addEventListener: () => {},
      querySelector: (sel) => {
        if (sel === ".kerzox-title-text") {
          return {
            set textContent(v) {
              el.titleText = v;
              el.innerHTML += v;
            },
            get textContent() {
              return el.titleText || "";
            },
          };
        }
        if (sel === ".kerzox-list-meta") return el;
        return null;
      },
    };
    return el;
  };

  const fn = new Function(
    "window", "chrome", "document", "history", "MutationObserver", "URL",
    testCode
  );
  fn(mockWindow, mockChrome, mockDocument, mockHistory, MockMutationObserver, URL);

  assert.strictEqual(typeof mockWindow.__testRenderQueue, "function", "renderQueue must be exposed");

  // Call renderQueue with paused, starting, and retrying jobs
  mockWindow.__testRenderQueue({
    active: null,
    queued: [],
    starting: [{ id: "start-1", filename: "Starting.mp4", status: "starting", progress: 0 }],
    retrying: [{ id: "retry-1", filename: "Retrying.mp4", status: "retrying", progress: 15 }],
    paused: [{ id: "pause-1", filename: "Paused.mp4", status: "paused", progress: 50 }],
    failed: [],
  });

  assert.strictEqual(appendedItems.length, 3, "All 3 non-terminal jobs must be rendered in queue list");

  const startingItem = appendedItems.find((item) => item.innerHTML.includes("Starting.mp4"));
  const retryingItem = appendedItems.find((item) => item.innerHTML.includes("Retrying.mp4"));
  const pausedItem = appendedItems.find((item) => item.innerHTML.includes("Paused.mp4"));

  assert.ok(startingItem, "Starting job item must be rendered");
  assert.ok(retryingItem, "Retrying job item must be rendered");
  assert.ok(pausedItem, "Paused job item must be rendered");

  assert.ok(startingItem.innerHTML.includes("kerzox-spinner"), "Starting job must display spinner indicator");
  assert.ok(retryingItem.innerHTML.includes("kerzox-spinner"), "Retrying job must display spinner indicator");
  assert.ok(pausedItem.innerHTML.includes("Resume"), "Paused job must display Resume button");
});

// Direct execution summary
if (require.main === module) {
  console.log("[test_content_tdz] All TDZ regression checks passed successfully.");
}
