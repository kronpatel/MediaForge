/**
 * extension/test_background_notify.js
 *
 * Automated regression test for background message handlers:
 * Confirms KERZOX_NOTIFY and KERZOX_OPEN_OPTIONS invoke sendResponse({ success: true })
 * so the message port is not closed without a response.
 */

const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert");
const test = require("node:test");

const BACKGROUND_JS_PATH = path.join(__dirname, "background.js");

function loadBackgroundWithMock(mocks = {}) {
  let messageListener = null;

  const mockChrome = {
    runtime: {
      onInstalled: { addListener: () => {} },
      onMessage: {
        addListener: (fn) => {
          messageListener = fn;
        },
      },
      openOptionsPage: () => Promise.resolve(),
      lastError: null,
      ...mocks.runtime,
    },
    notifications: {
      create: (_options, cb) => {
        cb?.("notification-id");
      },
      ...mocks.notifications,
    },
    storage: {
      local: {
        get: () => Promise.resolve({ kerzoxBackendUrl: "http://127.0.0.1:5000" }),
      },
      ...mocks.storage,
    },
  };

  const code = fs.readFileSync(BACKGROUND_JS_PATH, "utf8");
  const fn = new Function("chrome", code);
  fn(mockChrome);

  return { messageListener, mockChrome };
}

test("background.js: KERZOX_NOTIFY calls sendResponse with success: true", () => {
  const { messageListener } = loadBackgroundWithMock();
  assert.ok(typeof messageListener === "function", "onMessage listener must be registered");

  let responseData = null;
  const sendResponse = (resp) => {
    responseData = resp;
  };

  const returned = messageListener(
    { type: "KERZOX_NOTIFY", title: "Download complete", message: "Video.mp4" },
    {},
    sendResponse
  );

  assert.strictEqual(returned, false, "Synchronous handler should return false or undefined");
  assert.deepStrictEqual(responseData, { success: true }, "Must send { success: true } response");
});

test("background.js: KERZOX_OPEN_OPTIONS calls sendResponse with success: true", () => {
  let openOptionsCalled = false;
  const { messageListener } = loadBackgroundWithMock({
    runtime: {
      openOptionsPage: () => {
        openOptionsCalled = true;
        return Promise.resolve();
      },
    },
  });

  let responseData = null;
  const sendResponse = (resp) => {
    responseData = resp;
  };

  const returned = messageListener(
    { type: "KERZOX_OPEN_OPTIONS" },
    {},
    sendResponse
  );

  assert.strictEqual(returned, false, "Synchronous handler should return false or undefined");
  assert.deepStrictEqual(responseData, { success: true }, "Must send { success: true } response");
  assert.ok(openOptionsCalled, "openOptionsPage must have been called");
});
