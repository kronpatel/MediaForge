const DEFAULT_BACKEND_URL = "http://127.0.0.1:5000";
let API_BASE_URL = DEFAULT_BACKEND_URL;

const downloadFolder = document.getElementById("downloadFolder");
const ffmpegPath = document.getElementById("ffmpegPath");
const backendUrl = document.getElementById("backendUrl");
const theme = document.getElementById("theme");
const statusNode = document.getElementById("status");
const versionNode = document.querySelector("[data-version]");

document.getElementById("saveFolder").addEventListener("click", saveFolder);
document.getElementById("resetFolder").addEventListener("click", resetFolder);
document.getElementById("chooseFolder").addEventListener("click", chooseFolder);
document.getElementById("saveBackendUrl").addEventListener("click", saveBackendUrl);
theme.addEventListener("change", saveTheme);

init();

async function init() {
    await loadStoredBackendUrl();
    loadSettings();
}

async function loadStoredBackendUrl() {
    try {
        const stored = await chrome.storage.local.get(["kerzoxBackendUrl"]);
        if (stored.kerzoxBackendUrl) {
            API_BASE_URL = stored.kerzoxBackendUrl;
        }
    } catch {
        // chrome.storage unavailable; keep default
    }
}

async function loadSettings() {
    setStatus("Loading settings...");

    try {
        const response = await fetch(`${API_BASE_URL}/settings`);
        const data = await response.json();

        if (!response.ok || !data.success) {
            setStatus(data.message || "Backend settings unavailable.");
            await loadLocalFolder();
            await loadLocalTheme();
            return;
        }

        applySettings(data.settings);
        await chrome.storage.local.set({ kerzoxDownloadFolder: data.settings.download_folder || "" });
        await loadLocalTheme(data.settings.theme);
        setStatus("Settings loaded.");
    } catch (error) {
        console.error(error);
        setStatus(`Backend not running at ${API_BASE_URL}`);
        await loadLocalFolder();
        await loadLocalTheme();
    }
}

function applySettings(settings) {
    downloadFolder.value = settings.download_folder || "";
    ffmpegPath.value = settings.ffmpeg_path || "";
    backendUrl.value = settings.backend_url || API_BASE_URL;
    versionNode.textContent = `v${settings.version || "1.2.3"}`;
}

async function saveBackendUrl() {
    const newUrl = backendUrl.value.trim();
    if (!newUrl) {
        setStatus("Backend URL cannot be empty.");
        return;
    }
    try {
        await chrome.storage.local.set({ kerzoxBackendUrl: newUrl });
        API_BASE_URL = newUrl;
        setStatus(`Backend URL saved to ${newUrl}. Reconnecting...`);
        await loadSettings();
    } catch (error) {
        console.error(error);
        setStatus("Could not save Backend URL.");
    }
}

async function saveFolder() {
    setStatus("Saving download folder...");

    try {
        const response = await fetch(`${API_BASE_URL}/settings`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                download_folder: downloadFolder.value,
                theme: theme.value
            })
        });
        const data = await response.json();

        if (!response.ok || !data.success) {
            setStatus(data.message || "Could not save folder.");
            return;
        }

        applySettings(data.settings);
        await chrome.storage.local.set({ kerzoxDownloadFolder: data.settings.download_folder || "" });
        await saveTheme();
        setStatus("Download folder saved.");
    } catch (error) {
        console.error(error);
        setStatus(`Backend not running at ${API_BASE_URL}. Folder was not saved.`);
    }
}

async function resetFolder() {
    setStatus("Resetting download folder...");

    try {
        const response = await fetch(`${API_BASE_URL}/settings/reset-folder`, { method: "POST" });
        const data = await response.json();

        if (!response.ok || !data.success) {
            setStatus(data.message || "Could not reset folder.");
            return;
        }

        applySettings(data.settings);
        await chrome.storage.local.set({ kerzoxDownloadFolder: data.settings.download_folder || "" });
        setStatus("Download folder reset.");
    } catch (error) {
        console.error(error);
        setStatus(`Backend not running at ${API_BASE_URL}. Folder was not reset.`);
    }
}

async function chooseFolder() {
    setStatus("Opening folder picker...");

    try {
        const response = await fetch(`${API_BASE_URL}/settings/select-folder`, { method: "POST" });
        const data = await response.json();

        if (!response.ok || !data.success) {
            setStatus(data.message || "No folder selected.");
            return;
        }

        applySettings(data.settings);
        await chrome.storage.local.set({ kerzoxDownloadFolder: data.settings.download_folder || "" });
        setStatus("Download folder selected.");
    } catch (error) {
        console.error(error);
        setStatus("Folder picker unavailable. Enter the path manually.");
    }
}

async function loadLocalTheme(fallbackTheme = "dark") {
    const stored = await chrome.storage.local.get(["kerzoxTheme"]);
    theme.value = stored.kerzoxTheme || fallbackTheme || "dark";
    document.body.dataset.theme = theme.value;
}

async function loadLocalFolder() {
    const stored = await chrome.storage.local.get(["kerzoxDownloadFolder"]);
    if (stored.kerzoxDownloadFolder) {
        downloadFolder.value = stored.kerzoxDownloadFolder;
    }
}

async function saveTheme() {
    await chrome.storage.local.set({ kerzoxTheme: theme.value });
    document.body.dataset.theme = theme.value;

    try {
        await fetch(`${API_BASE_URL}/settings`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ theme: theme.value })
        });
    } catch {
    }

    setStatus("Theme saved.");
}

function setStatus(message) {
    statusNode.textContent = message;
}
