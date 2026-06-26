chrome.runtime.onInstalled.addListener(() => {
    console.log("MediaForge v1.0 installed");
});

chrome.runtime.onMessage.addListener((message) => {
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
