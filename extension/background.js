chrome.runtime.onInstalled.addListener(() => {
    console.log("MediaForge v1.0 installed");
});

chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "KERZOX_NOTIFY") {
        chrome.notifications.create({
            type: "basic",
            iconUrl: "icon.svg",
            title: message.title || "MediaForge",
            message: message.message || "Download complete"
        });
    }

    if (message?.type === "KERZOX_OPEN_OPTIONS") {
        chrome.runtime.openOptionsPage();
    }
});
