# Extension Network Analysis Report — MediaForge v1.2.0

This report provides the network capture and analysis details for browser extension communication failures with the MediaForge backend.

---

## 1. Browser Network Capture

All AJAX fetch requests initiated from the extension's content script `content.js` (running on `https://www.youtube.com/watch?v=dQw4w9WgXcQ`) are directed to `http://127.0.0.1:5000`.

### Captured Preflight Request
- **Method:** `OPTIONS`
- **URL:** `http://127.0.0.1:5000/download`
- **Status Code:** `200 OK` (Flask handles OPTIONS requests and returns 200, but fails to attach CORS headers for unrecognized origins).
- **Request Headers:**
  - `Origin: https://www.youtube.com`
  - `Access-Control-Request-Method: POST`
  - `Access-Control-Request-Headers: content-type`
- **Response Headers:**
  - `Server: Werkzeug/3.1.8 Python/3.14.5`
  - `Allow: OPTIONS, POST`
  - `Connection: close`
  - *(Missing: `Access-Control-Allow-Origin`, `Access-Control-Allow-Headers`)*
- **Response Body:** None

---

## 2. Failure Type

- **Primary Failure:** **CORS Rejection & OPTIONS Preflight Blocked**
  - The fetch to `/download` is a complex request (POST with JSON payload), requiring a preflight `OPTIONS` request. 
  - Because the response to the `OPTIONS` request does not return `Access-Control-Allow-Origin: https://www.youtube.com`, the browser blocks the preflight pre-emptively and refuses to dispatch the actual `POST` request.

---

## 3. Chrome Console Stack Trace

```
Access to fetch at 'http://127.0.0.1:5000/download' from origin 'https://www.youtube.com' has been blocked by CORS policy: Response to preflight request doesn't pass access control check: No 'Access-Control-Allow-Origin' header is present on the requested resource.

TypeError: Failed to fetch
    at download (content.js:1110)
    at HTMLButtonElement.onclick (content.js:344)
```

---

## 4. Extension Fetch Calls

- **Active Jobs Poll / Status Request:**
  - **URL:** `http://127.0.0.1:5000/status/<job_id>`
  - **Method:** `GET`
  - **Origin:** `https://www.youtube.com`
  - **CORS Result:** Blocked (GET lacks preflight but is blocked by browser due to missing `Access-Control-Allow-Origin` on response).
- **Queue Status Request:**
  - **URL:** `http://127.0.0.1:5000/queue`
  - **Method:** `GET`
  - **Origin:** `https://www.youtube.com`
  - **CORS Result:** Blocked (CORS validation fails on response).

---

## 5. CORS Verification

- **OPTIONS Request Existed:** **YES**
- **Verification Analysis:**
  - `Access-Control-Allow-Origin` is **missing** when request carries `Origin: https://www.youtube.com`.
  - `Access-Control-Allow-Headers` is **missing**.
  - `Access-Control-Allow-Methods` is **missing**.
  - Conversely, requests carrying `Origin: chrome-extension://...` successfully receive correct headers.

---

## 6. Final Verdict

```
Backend Running: YES
Backend Reachable: YES
Browser Request Sent: YES (OPTIONS preflight sent, POST blocked)
HTTP Status: 200 (for OPTIONS, POST not sent)
Root Cause: Flask backend restricts CORS origins in backend/app.py and does not allow requests originating from 'https://www.youtube.com'.
Recommended Fix: Add re.compile(r"^https?://(www\.)?youtube\.com$") to CORS(app, origins=[...]) in backend/app.py.
```
