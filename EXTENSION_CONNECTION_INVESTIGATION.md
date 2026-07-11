# Extension Connection Bug Investigation Report — MediaForge v1.2.0

This report details the root cause, file references, and recommended fixes for the connection failure between the browser extension and the backend service.

---

## 1. Root Cause Analysis

The root cause of the connection failure is a **CORS (Cross-Origin Resource Sharing) block** enforced by the browser. 

1. **Origin of Fetch Request:** The browser extension initiates all download, status, and queue fetches directly from the content script (`content.js`), which is injected and runs in the context of the YouTube webpage. Therefore, the browser tags these outgoing HTTP fetch requests with the origin header:
   `Origin: https://www.youtube.com`
2. **CORS Restrictions in Backend:** The Flask backend's CORS configuration restricts allowed cross-origin requests to local loopbacks (`127.0.0.1` and `localhost`) and raw extension protocol IDs. It does not include YouTube's domain.
3. **CORS Preflight Failure:** Because `https://www.youtube.com` is missing from the Flask allowed origins list, the backend preflight OPTIONS request returns without the required `Access-Control-Allow-Origin` header, causing the browser to block the fetch and throw `TypeError: Failed to fetch`.

---

## 2. Code Review & Verification Checklist

- **Where `API_BASE_URL` is defined:**
  - [extension/content.js](file:///d:/MediaForge/extension/content.js#L8) — Line 8: `let API_BASE_URL = DEFAULT_BACKEND_URL;`
  - [extension/settings.js](file:///d:/MediaForge/extension/settings.js#L2) — Line 2: `let API_BASE_URL = DEFAULT_BACKEND_URL;`
- **Which backend URL is actually used:**
  - `http://127.0.0.1:5000` (by default via `DEFAULT_BACKEND_URL`), or a custom URL loaded from `chrome.storage.local.get(["kerzoxBackendUrl"])`.
- **Whether extension still assumes localhost:5000:**
  - Yes. The extension relies on a hardcoded default port `5000` inside `DEFAULT_BACKEND_URL` and has no automatic fallback port checking mechanism.
- **Whether dynamic backend port support is broken:**
  - Yes. In [companion/backend_manager.py](file:///d:/MediaForge/companion/backend_manager.py#L225-L228), if port 5000 is occupied by an unrelated service, the application sets its status to `STOPPED` and aborts. There is no fallback logic to scan or bind to alternative ports (like `5001`, `5002`, etc.) in the backend manager or Flask application.
- **Whether `settings.js` correctly loads `backend_url`:**
  - Yes, on line 64: `backendUrl.value = settings.backend_url || API_BASE_URL;` (fetches from `${API_BASE_URL}/settings`).
- **Whether `content.js` receives updated backend URL:**
  - Yes, on line 25, it correctly updates `API_BASE_URL` dynamically when `chrome.storage.onChanged` fires.
- **Whether `background.js` forwards configuration correctly:**
  - `background.js` does not participate in configuration forwarding (only notifications and options page triggers).
- **Verify manifest permissions:**
  - [extension/manifest.json](file:///d:/MediaForge/extension/manifest.json#L10-L13) permits `"http://127.0.0.1:*/*"` but lacks `"http://localhost:*/*"`.
- **Verify `fetch()` URL construction:**
  - Constructed correctly as `${API_BASE_URL}/<endpoint>` across all requests.
- **Verify backend endpoints still match extension requests:**
  - Yes, all route mappings in [backend/app.py](file:///d:/MediaForge/backend/app.py) match the extension requests exactly.

---

## 3. Reference to Bug Location

### CORS Failure Location
- **Exact File:** [backend/app.py](file:///d:/MediaForge/backend/app.py)
- **Exact Function:** Flask app initialization block
- **Exact Line Numbers:** L37–L42
- **Current Implementation:**
  ```python
  CORS(app, origins=[
      re.compile(r"^http://127\.0\.0\.1:\d+$"),
      re.compile(r"^http://localhost:\d+$"),
      re.compile(r"^chrome-extension://[a-z0-9]{32}$"),
      re.compile(r"^moz-extension://[a-f0-9\-]+$"),
  ])
  ```

---

## 4. Recommended Fix

To resolve the connection failure, the CORS allowed origins list and the manifest host permissions must be updated:

1. **Allow YouTube Origin in Flask app (CORS):**
   Add regular expressions matching YouTube's web domains to `CORS(app, origins=[...])` in [backend/app.py](file:///d:/MediaForge/backend/app.py):
   ```python
   CORS(app, origins=[
       re.compile(r"^http://127\.0\.0\.1:\d+$"),
       re.compile(r"^http://localhost:\d+$"),
       re.compile(r"^chrome-extension://[a-z0-9]{32}$"),
       re.compile(r"^moz-extension://[a-f0-9\-]+$"),
       re.compile(r"^https?://(www\.)?youtube\.com$"),
   ])
   ```

2. **Permit Localhost in Manifest Host Permissions:**
   Add `"http://localhost:*/*"` to `host_permissions` in [extension/manifest.json](file:///d:/MediaForge/extension/manifest.json) to allow the extension settings page to contact the backend if the user configures `localhost`:
   ```json
     "host_permissions": [
       "https://www.youtube.com/*",
       "http://127.0.0.1:*/*",
       "http://localhost:*/*"
     ]
   ```
