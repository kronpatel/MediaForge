const DEFAULT_BACKEND_URL = "http://127.0.0.1:5000";
const API_REQUEST_TIMEOUT_MS = 30000;

chrome.runtime.onInstalled.addListener(() => {
    console.log("MediaForge v1.2.1 installed");
});

async function getBackendUrl() {
    try {
        const stored = await chrome.storage.local.get(["kerzoxBackendUrl"]);
        return stored.kerzoxBackendUrl || DEFAULT_BACKEND_URL;
    } catch {
        return DEFAULT_BACKEND_URL;
    }
}

async function apiRequest(endpoint, method = "GET", body = null) {
    const baseUrl = await getBackendUrl();
    const url = baseUrl + endpoint;

    const fetchOptions = {
        method,
        headers: {}
    };

    if (body !== null && method !== "GET") {
        fetchOptions.headers["Content-Type"] = "application/json";
        fetchOptions.body = typeof body === "string" ? body : JSON.stringify(body);
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), API_REQUEST_TIMEOUT_MS);
    fetchOptions.signal = controller.signal;

    try {
        const response = await fetch(url, fetchOptions);
        clearTimeout(timeoutId);

        let data;
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
            data = await response.json();
        } else {
            const text = await response.text();
            try {
                data = JSON.parse(text);
            } catch {
                return {
                    success: false,
                    error: `Unexpected response from server (HTTP ${response.status})`
                };
            }
        }

        if (!response.ok) {
            return {
                success: false,
                error: data?.message || data?.error || `HTTP ${response.status}: ${response.statusText}`
            };
        }

        return { success: true, data };
    } catch (error) {
        clearTimeout(timeoutId);

        if (error.name === "AbortError") {
            return { success: false, error: "Request timed out. Backend may be offline." };
        }

        if (error instanceof TypeError && error.message.includes("fetch")) {
            return { success: false, error: `Backend offline at ${url}` };
        }

        return { success: false, error: error.message || "Unknown network error" };
    }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "KERZOX_API_REQUEST") {
        apiRequest(message.endpoint, message.method || "GET", message.body ?? null)
            .then(sendResponse)
            .catch((error) => {
                sendResponse({ success: false, error: error.message || "Request failed" });
            });
        return true;
    }

    if (message?.type === "KERZOX_NOTIFY") {
        try {
            chrome.notifications.create({
                type: "basic",
                iconUrl: "icon.png",
                title: message.title || "MediaForge",
                message: message.message || "Download complete"
            }, () => {
                if (chrome.runtime.lastError) {
                    console.error("Notification error:", chrome.runtime.lastError.message);
                }
            });
        } catch (error) {
            console.error("Failed to create notification:", error);
        }
    }

    if (message?.type === "KERZOX_OPEN_OPTIONS") {
        chrome.runtime.openOptionsPage();
    }
});
