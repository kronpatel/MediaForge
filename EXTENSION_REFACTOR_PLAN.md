# Extension Refactor Plan — MediaForge v1.2.0

This refactor plan outlines the migration of browser extension network requests from the content script (`content.js`) to the background service worker (`background.js`) to resolve CORS constraints securely in Manifest V3.

---

## 1. Background Script Audit

### Current `background.js` Setup
- **Message Listeners:** One `chrome.runtime.onMessage.addListener` checking for `KERZOX_NOTIFY` and `KERZOX_OPEN_OPTIONS`.
- **Runtime Listeners:** One `chrome.runtime.onInstalled.addListener` logging initialization.
- **Fetch Calls:** **0** (No active network calls).
- **Notification Handlers:** One `chrome.notifications.create` handler.

### Feasibility Study
Yes, `background.js` can serve as the single backend communication gateway. By implementing an asynchronous message proxy handler, the service worker can parse request metadata, execute fetches using extension host permissions, and return responses back to the content script using Chrome's built-in message ports.

---

## 2. Extension Network Call Map

| Fetch Endpoint | Called By | Method | Context | CORS Safe |
| :--- | :--- | :--- | :--- | :--- |
| `/download` | `content.js` | `POST` | Webpage DOM (`youtube.com`) | **NO** (Blocked by webpage origin) |
| `/status/<id>` | `content.js` | `GET` | Webpage DOM (`youtube.com`) | **NO** (Blocked by webpage origin) |
| `/queue` | `content.js` | `GET` | Webpage DOM (`youtube.com`) | **NO** (Blocked by webpage origin) |
| `/history/clear` | `content.js` | `POST` | Webpage DOM (`youtube.com`) | **NO** (Blocked by webpage origin) |
| `/settings` | `settings.js` | `GET`/`POST`| Options Page (`chrome-extension://...`)| **YES** (Extension origin is allowed) |
| `/settings/reset`| `settings.js` | `POST` | Options Page (`chrome-extension://...`)| **YES** (Extension origin is allowed) |
| `/settings/select`| `settings.js` | `POST` | Options Page (`chrome-extension://...`)| **YES** (Extension origin is allowed) |

---

## 3. Communication Flow Design

```
[ YouTube Page DOM ]
       │
       ▼ (User clicks download)
[ content.js ] 
       │
       ▼ chrome.runtime.sendMessage({ type: "KERZOX_API_REQUEST", endpoint, method, body })
[ background.js (Service Worker Context) ]
       │
       ▼ fetch("http://127.0.0.1:5000" + endpoint, { method, body })
[ Flask Backend Server ]
       │
       ▼ JSON response ({ success: true, ... })
[ background.js (Service Worker Context) ]
       │
       ▼ sendResponse({ success: true, data })
[ content.js ]
       │
       ▼ (Parses response)
[ Injected UI Dashboard Panel Update ]
```

No direct fetch calls to the backend will remain within `content.js`.

---

## 4. Migration Checklist

### Call 1: Download Request
- **Old Location:** `content.js` L1110–1114
- **New Location:** `background.js`
- **Message Payload:** `{ type: "KERZOX_API_REQUEST", endpoint: "/download", method: "POST", body: { url, mode } }`
- **Response Payload:** `{ success: true, data: { success: true, job_id, job } }`
- **Required Listener:** `background.js` maps endpoint `/download` to POST fetch, dynamically pulling `kerzoxBackendUrl` from storage.
- **Required UI Update:** Sets `activeJobId`, registers job status, sets loading indicators, and starts status polling.

### Call 2: Job Status Poll
- **Old Location:** `content.js` L1140–1143
- **New Location:** `background.js`
- **Message Payload:** `{ type: "KERZOX_API_REQUEST", endpoint: "/status/" + jobId, method: "GET" }`
- **Response Payload:** `{ success: true, data: { success: true, job } }`
- **Required Listener:** `background.js` executes GET fetch to the job status endpoint.
- **Required UI Update:** Feeds progress values, speeds, ETAs, and handles success or failure UI transitions.

### Call 3: Queue Refresh
- **Old Location:** `content.js` L1192–1194
- **New Location:** `background.js`
- **Message Payload:** `{ type: "KERZOX_API_REQUEST", endpoint: "/queue", method: "GET" }`
- **Response Payload:** `{ success: true, data: { success: true, queue } }`
- **Required Listener:** `background.js` executes GET fetch to the queue list endpoint.
- **Required UI Update:** Clears and redrafts elements inside the active downloads pane.

### Call 4: Clear History
- **Old Location:** `content.js` L1318–1320
- **New Location:** `background.js`
- **Message Payload:** `{ type: "KERZOX_API_REQUEST", endpoint: "/history/clear", method: "POST" }`
- **Response Payload:** `{ success: true, data: { success: true } }`
- **Required Listener:** `background.js` executes POST fetch to clear history.
- **Required UI Update:** Displays success status and clears list nodes inside the history UI panel.

---

## 5. Backward Compatibility & Verification

To verify that no functionality regresses:
- **Download Buttons:** Confirm click handlers fire messages and receive valid job IDs to start downloads.
- **Queue Polling:** Confirm background thread polling cycles correctly process periodic status responses.
- **Progress Updates:** Confirm UI progress bar and transfer speed nodes receive exact payload parameters.
- **Settings page:** Options page continues to load and edit target configurations directly (as it resides in the extension origin and does not encounter CORS limitations).
- **Notifications:** Notifications are still triggered by sending `KERZOX_NOTIFY` messages to the service worker.

---

## 6. Final Verdict

**READY TO IMPLEMENT**
