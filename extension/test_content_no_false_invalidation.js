/**
 * extension/test_content_no_false_invalidation.js
 *
 * Automated regression test:
 * Confirms content.js does NOT treat "message port closed" or "receiving end does not exist"
 * as extension context invalidation, keeping the download button intact.
 */

const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert");
const test = require("node:test");

const CONTENT_JS_PATH = path.join(__dirname, "content.js");

function loadContentWithMock(mocks = {}) {
  const mockWindow = {
    clearTimeout: () => {},
    setTimeout: () => 1,
    cancelAnimationFrame: () => {},
    addEventListener: () => {},
    location: { href: "https://www.youtube.com/watch?v=test12345" },
    __kerzox_mediaforge_initialized: false,
    ...mocks.window,
  };

  const mockChrome = {
    runtime: {
      id: "mediaforge-extension-id",
      lastError: null,
      sendMessage: (_msg, cb) => cb?.(null),
      ...mocks.runtime,
    },
    ...mocks.chrome,
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
      querySelector: () => null,
      querySelectorAll: () => [],
      classList: { toggle: () => {}, add: () => {}, remove: () => {} },
    }),
    head: { appendChild: () => {} },
    body: { appendChild: () => {} },
    documentElement: { appendChild: () => {} },
    ...mocks.document,
  };

  const mockHistory = { pushState: () => {}, replaceState: () => {} };

  class MockMutationObserver {
    observe() {}
    disconnect() {}
  }

  const code = fs.readFileSync(CONTENT_JS_PATH, "utf8");
  const fn = new Function(
    "window", "chrome", "document", "history", "MutationObserver", "URL",
    code
  );
  fn(mockWindow, mockChrome, mockDocument, mockHistory, MockMutationObserver, URL);

  return {
    mockWindow,
    mockChrome,
    isExtensionContextError: mockWindow.__mf_isExtensionContextError,
  };
}

test("isExtensionContextError rejects 'message port closed' (must NOT invalidate context)", () => {
  const { isExtensionContextError } = loadContentWithMock();
  assert.ok(typeof isExtensionContextError === "function", "isExtensionContextError must be exported on window");

  const err1 = "The message port closed before a response was received.";
  assert.strictEqual(
    isExtensionContextError(err1),
    false,
    "Message port closed must NOT be treated as context invalidation"
  );

  const err2 = new Error("The message port closed before a response was received.");
  assert.strictEqual(
    isExtensionContextError(err2),
    false,
    "Error instance of message port closed must NOT be treated as context invalidation"
  );
});

test("isExtensionContextError rejects 'receiving end does not exist' (must NOT invalidate context)", () => {
  const { isExtensionContextError } = loadContentWithMock();

  const err = "Could not establish connection. Receiving end does not exist.";
  assert.strictEqual(
    isExtensionContextError(err),
    false,
    "Receiving end does not exist must NOT be treated as context invalidation"
  );
});

test("isExtensionContextError detects genuine 'Extension context invalidated'", () => {
  const { isExtensionContextError } = loadContentWithMock();

  const genuineError = "Extension context invalidated.";
  assert.strictEqual(
    isExtensionContextError(genuineError),
    true,
    "Real extension context invalidated error must return true"
  );
});

test("isExtensionContextError returns true when chrome.runtime.id is undefined (unloaded extension)", () => {
  const { isExtensionContextError } = loadContentWithMock({
    runtime: { id: undefined },
  });

  assert.strictEqual(
    isExtensionContextError("Any transient error or null"),
    true,
    "When chrome.runtime.id is missing, must return true indicating invalid context"
  );
});
