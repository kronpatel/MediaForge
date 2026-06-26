(function () {
    "use strict";

    if (window.__kerzox_mediaforge_initialized) return;
    window.__kerzox_mediaforge_initialized = true;

    const API_BASE_URL = "http://127.0.0.1:5000";
    const VERSION = "1.0";
    const BUTTON_ID = "kerzox-download-button";
    const MENU_ID = "kerzox-download-menu";
    const STYLE_ID = "kerzox-download-style";
    const TITLE_SLOT_ID = "kerzox-title-download-slot";

    const DOWNLOAD_OPTIONS = [
        { mode: "mp3", label: "MP3", detail: "Audio, 320 kbps", icon: "music" },
        { mode: "1080p", label: "MP4 1080p", detail: "Video capped at 1080p", icon: "video" },
        { mode: "4k", label: "MP4 4K", detail: "Best available video", icon: "spark" },
        { mode: "8k", label: "Download 8K", detail: "Best video up to 8K", icon: "spark" },
        { mode: "playlist_mp3", label: "Playlist MP3", detail: "Full playlist audio", icon: "list" },
        { mode: "playlist_video", label: "Playlist Video", detail: "Full playlist video", icon: "layers" }
    ];

    let currentPageKey = "";
    let retryTimer = null;
    let statusTimer = null;
    let panelTimer = null;
    let activeJobId = "";
    const activeJobs = new Set();
    const notifiedJobIds = new Set();
    let activeTimeouts = [];
    let observer = null;
    let pollFailureCount = 0;
    const MAX_POLL_FAILURES = 5;

    function clearAllTimeouts() {
        activeTimeouts.forEach((id) => window.clearTimeout(id));
        activeTimeouts = [];
        window.clearTimeout(retryTimer);
    }

    function connectObserver() {
        disconnectObserver();
        observer = new MutationObserver(scheduleInject);
        observer.observe(document.documentElement, {
            childList: true,
            subtree: true
        });
    }

    function disconnectObserver() {
        if (observer) {
            observer.disconnect();
            observer = null;
        }
    }

    function getPageInfo() {
        const url = new URL(window.location.href);
        const videoId = url.searchParams.get("v") || "";
        const playlistId = url.searchParams.get("list") || "";
        const shortsMatch = url.pathname.match(/^\/shorts\/([^/?#]+)/);
        const titleNode = findTitleContainer();

        return {
            videoId: videoId || shortsMatch?.[1] || "",
            playlistId,
            title: titleNode?.textContent?.trim() || "YouTube media",
            isWatch: url.pathname === "/watch" && Boolean(videoId),
            isShort: Boolean(shortsMatch),
            isPlaylist: url.pathname === "/playlist" && Boolean(playlistId),
            href: window.location.href
        };
    }

    function getDownloadUrl(mode) {
        const page = getPageInfo();

        if (mode.startsWith("playlist_") && page.playlistId) {
            return `https://www.youtube.com/playlist?list=${page.playlistId}`;
        }

        if (page.isWatch && page.videoId) {
            return `https://www.youtube.com/watch?v=${page.videoId}`;
        }

        if (page.isShort && page.videoId) {
            return `https://www.youtube.com/shorts/${page.videoId}`;
        }

        if (page.isPlaylist && page.playlistId) {
            return `https://www.youtube.com/playlist?list=${page.playlistId}`;
        }

        return "";
    }

    function isSupportedPage() {
        const page = getPageInfo();
        return page.isWatch || page.isShort || page.isPlaylist;
    }

    function icon(name) {
        const icons = {
            chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
            clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
            download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>',
            history: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 3v6h6"/><path d="M12 7v5l3 2"/></svg>',
            layers: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/></svg>',
            list: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/></svg>',
            music: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
            queue: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16"/><path d="M4 12h10"/><path d="M4 18h7"/><path d="m15 16 2 2 4-4"/></svg>',
            settings: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.23.6.8 1 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1Z"/></svg>',
            spark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2v6"/><path d="M12 16v6"/><path d="m4.93 4.93 4.24 4.24"/><path d="m14.83 14.83 4.24 4.24"/><path d="M2 12h6"/><path d="M16 12h6"/><path d="m4.93 19.07 4.24-4.24"/><path d="m14.83 9.17 4.24-4.24"/></svg>',
            video: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 10 21 7v10l-6-3"/><rect x="3" y="6" width="12" height="12" rx="2"/></svg>'
        };

        return icons[name] || icons.download;
    }

    function ensureStyles() {
        if (document.getElementById(STYLE_ID)) return;

        const style = document.createElement("style");
        style.id = STYLE_ID;
        style.textContent = `
            #${BUTTON_ID}, #${MENU_ID} button {
                font-family: Roboto, Arial, sans-serif;
            }

            #${BUTTON_ID} {
                align-items: center;
                background: linear-gradient(180deg, #1f1f1f, #111);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 18px;
                box-shadow: 0 5px 16px rgba(0, 0, 0, 0.28);
                color: #fff;
                cursor: pointer;
                display: inline-flex;
                font: 700 14px/20px Roboto, Arial, sans-serif;
                gap: 7px;
                height: 36px;
                justify-content: center;
                margin-left: 8px;
                min-width: 104px;
                padding: 0 13px;
                transition: background 160ms ease, transform 160ms ease, box-shadow 160ms ease;
                white-space: nowrap;
            }

            #${BUTTON_ID}:hover,
            #${BUTTON_ID}[aria-expanded="true"] {
                background: #2b2b2b;
                box-shadow: 0 8px 24px rgba(0, 0, 0, 0.36);
                transform: translateY(-1px);
            }

            #${BUTTON_ID} svg,
            #${MENU_ID} svg {
                fill: none;
                height: 17px;
                stroke: currentColor;
                stroke-linecap: round;
                stroke-linejoin: round;
                stroke-width: 2;
                width: 17px;
            }

            #${BUTTON_ID} .kerzox-chevron {
                height: 14px;
                width: 14px;
            }

            #${TITLE_SLOT_ID} {
                display: flex;
                margin-top: 10px;
            }

            ytd-watch-metadata #${TITLE_SLOT_ID} #${BUTTON_ID} {
                margin-left: 0;
            }

            #${MENU_ID} {
                background: #101010;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 14px;
                box-shadow: 0 24px 70px rgba(0, 0, 0, 0.58), 0 0 0 1px rgba(255, 255, 255, 0.04) inset;
                color: #fff;
                font: 400 13px/18px Roboto, Arial, sans-serif;
                height: min(690px, calc(100vh - 24px));
                max-width: calc(100vw - 24px);
                opacity: 0;
                overflow: hidden;
                position: fixed;
                transform: translateY(-4px) scale(0.98);
                transform-origin: top right;
                transition: opacity 150ms ease, transform 150ms ease;
                width: 378px;
                z-index: 2147483647;
                display: flex;
                flex-direction: column;
            }

            #${MENU_ID}[data-theme="midnight"] {
                background: #111827;
                border-color: rgba(148, 163, 184, 0.24);
            }

            #${MENU_ID}[data-theme="midnight"] .kerzox-option,
            #${MENU_ID}[data-theme="midnight"] .kerzox-list-item,
            #${MENU_ID}[data-theme="midnight"] .kerzox-progress-card,
            #${MENU_ID}[data-theme="midnight"] .kerzox-tab {
                background: #172033;
            }

            #${MENU_ID}[data-theme="midnight"] .kerzox-modal-content {
                background: #172033;
                border-color: rgba(148, 163, 184, 0.24);
            }

            #${MENU_ID}[data-theme="contrast"] {
                background: #000;
                border-color: rgba(255, 255, 255, 0.42);
            }

            #${MENU_ID}[data-theme="contrast"] .kerzox-option,
            #${MENU_ID}[data-theme="contrast"] .kerzox-list-item,
            #${MENU_ID}[data-theme="contrast"] .kerzox-progress-card,
            #${MENU_ID}[data-theme="contrast"] .kerzox-tab {
                background: #080808;
                border-color: rgba(255, 255, 255, 0.28);
            }

            #${MENU_ID}[data-theme="contrast"] .kerzox-modal-content {
                background: #080808;
                border-color: rgba(255, 255, 255, 0.42);
            }

            #${MENU_ID}[hidden] {
                display: none;
            }

            #${MENU_ID}.is-open {
                opacity: 1;
                transform: translateY(0) scale(1);
            }

            #${MENU_ID}.opens-above {
                transform-origin: bottom right;
            }

            .kerzox-menu-body {
                display: flex;
                flex-direction: column;
                height: 100%;
                overflow: hidden;
            }

            .kerzox-menu-header {
                display: grid;
                gap: 12px;
                grid-template-columns: 104px 1fr;
                padding: 14px;
                flex-shrink: 0;
            }

            .kerzox-thumb {
                aspect-ratio: 16 / 9;
                background: #222;
                border-radius: 10px;
                object-fit: cover;
                width: 104px;
                flex-shrink: 0;
            }

            .kerzox-title {
                color: #fff;
                display: -webkit-box;
                font-size: 14px;
                font-weight: 800;
                line-height: 19px;
                margin: 0 0 7px;
                overflow: hidden;
                -webkit-box-orient: vertical;
                -webkit-line-clamp: 2;
                height: 38px;
            }

            .kerzox-subtitle {
                color: #aaa;
                font-size: 12px;
                margin: 0;
            }

            .kerzox-tabs {
                border-top: 1px solid rgba(255, 255, 255, 0.09);
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                padding: 8px;
                gap: 6px;
                flex-shrink: 0;
            }

            .kerzox-tab {
                align-items: center;
                background: #1a1a1a;
                border: 1px solid transparent;
                border-radius: 9px;
                color: #bdbdbd;
                cursor: pointer;
                display: inline-flex;
                font-size: 12px;
                font-weight: 800;
                gap: 6px;
                height: 34px;
                justify-content: center;
            }

            .kerzox-tab.is-active {
                background: #262626;
                border-color: rgba(255, 255, 255, 0.14);
                color: #fff;
            }

            .kerzox-panel {
                display: none;
                flex-direction: column;
                flex: 1;
                overflow-y: auto;
                min-height: 0;
                padding: 0 10px 10px;
                box-sizing: border-box;
            }

            .kerzox-section-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 10px;
                flex-shrink: 0;
                padding-top: 4px;
            }

            .kerzox-section-title {
                color: #fff;
                font-size: 13px;
                font-weight: 800;
            }

            .kerzox-clear-button {
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #aaa;
                cursor: pointer;
                font-size: 11px;
                font-weight: 800;
                padding: 4px 10px;
                transition: background 140ms ease, border-color 140ms ease, color 140ms ease;
            }

            .kerzox-clear-button:hover {
                background: rgba(255, 255, 255, 0.06);
                border-color: rgba(255, 255, 255, 0.24);
                color: #fff;
            }

            .kerzox-modal {
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.75);
                backdrop-filter: blur(4px);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 100;
                animation: kerzox-fade-in 150ms ease-out forwards;
            }

            .kerzox-modal[hidden] {
                display: none;
            }

            .kerzox-modal-content {
                background: #161616;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 16px;
                width: 280px;
                text-align: center;
                box-shadow: 0 16px 48px rgba(0, 0, 0, 0.6);
            }

            .kerzox-modal-text {
                color: #fff;
                font-size: 13px;
                font-weight: 800;
                line-height: 18px;
                margin: 0 0 16px;
            }

            .kerzox-modal-actions {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }

            .kerzox-confirm-btn {
                align-items: center;
                background: #ff4d4d;
                border: 1px solid transparent;
                border-radius: 9px;
                color: #fff;
                cursor: pointer;
                display: inline-flex;
                font-size: 12px;
                font-weight: 800;
                height: 34px;
                justify-content: center;
                transition: background 140ms ease;
            }

            .kerzox-confirm-btn:hover {
                background: #ff3333;
            }

            @keyframes kerzox-fade-in {
                from {
                    opacity: 0;
                    transform: translateY(4px);
                }
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }

            .kerzox-panel.is-active {
                display: flex;
                animation: kerzox-fade-in 180ms ease-out forwards;
            }

            /* Custom dark theme scrollbar */
            .kerzox-panel::-webkit-scrollbar {
                width: 6px;
            }
            .kerzox-panel::-webkit-scrollbar-track {
                background: rgba(255, 255, 255, 0.03);
                border-radius: 3px;
            }
            .kerzox-panel::-webkit-scrollbar-thumb {
                background: rgba(255, 255, 255, 0.16);
                border-radius: 3px;
            }
            .kerzox-panel::-webkit-scrollbar-thumb:hover {
                background: rgba(255, 255, 255, 0.28);
            }

            .kerzox-actions {
                display: grid;
                gap: 7px;
            }

            .kerzox-option,
            .kerzox-icon-action {
                align-items: center;
                background: #191919;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                color: #fff;
                cursor: pointer;
                display: grid;
                min-height: 48px;
                transition: background 140ms ease, border-color 140ms ease, transform 140ms ease;
            }

            .kerzox-option {
                gap: 2px 10px;
                grid-template-columns: 32px 1fr;
                padding: 10px;
                text-align: left;
            }

            .kerzox-option:hover,
            .kerzox-icon-action:hover {
                background: #252525;
                border-color: rgba(255, 255, 255, 0.16);
                transform: translateY(-1px);
            }

            .kerzox-option:disabled {
                cursor: wait;
                opacity: 0.62;
                transform: none;
            }

            .kerzox-option-icon {
                align-items: center;
                background: #2b2b2b;
                border-radius: 9px;
                display: inline-flex;
                grid-row: span 2;
                height: 32px;
                justify-content: center;
                width: 32px;
            }

            .kerzox-option-label {
                font-size: 13px;
                font-weight: 800;
                line-height: 17px;
            }

            .kerzox-option-detail {
                color: #aaa;
                font-size: 12px;
                line-height: 16px;
            }

            .kerzox-progress-card {
                background: #151515;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 12px;
                margin-top: 10px;
                padding: 12px;
                flex-shrink: 0;
            }

            .kerzox-status-row {
                align-items: center;
                display: flex;
                gap: 8px;
                margin-bottom: 10px;
            }

            .kerzox-spinner {
                animation: kerzox-spin 0.8s linear infinite;
                border: 2px solid rgba(255, 255, 255, 0.18);
                border-radius: 50%;
                border-top-color: #fff;
                display: inline-block;
                flex: 0 0 auto;
                height: 16px;
                width: 16px;
                visibility: hidden;
                opacity: 0;
                transition: opacity 150ms ease;
            }

            #${MENU_ID}.is-loading .kerzox-spinner {
                visibility: visible;
                opacity: 1;
            }

            .kerzox-status-text {
                color: #e6e6e6;
                flex: 1;
                font-size: 12px;
                overflow-wrap: anywhere;
            }

            .kerzox-progress-percent {
                font-size: 22px;
                font-weight: 900;
                line-height: 26px;
            }

            .kerzox-progress-track {
                background: #2a2a2a;
                border-radius: 999px;
                height: 9px;
                margin: 8px 0 10px;
                overflow: hidden;
            }

            .kerzox-progress-fill {
                background: linear-gradient(90deg, #fff, #bcbcbc);
                border-radius: inherit;
                height: 100%;
                transition: width 220ms ease;
                width: 0%;
            }

            .kerzox-progress-meta {
                color: #bdbdbd;
                display: grid;
                font-size: 12px;
                gap: 6px;
                grid-template-columns: repeat(3, 1fr);
            }

            .kerzox-progress-meta span {
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }

            .kerzox-progress-meta span:nth-child(1) {
                text-align: left;
            }

            .kerzox-progress-meta span:nth-child(2) {
                text-align: center;
            }

            .kerzox-progress-meta span:nth-child(3) {
                text-align: right;
            }

            .kerzox-list {
                display: grid;
                gap: 7px;
            }

            .kerzox-list-item {
                background: #171717;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                padding: 10px;
            }

            .kerzox-list-title {
                color: #fff;
                font-size: 12px;
                font-weight: 800;
                line-height: 17px;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }

            .kerzox-list-meta {
                color: #aaa;
                display: flex;
                flex-wrap: wrap;
                font-size: 11px;
                gap: 6px;
                margin-top: 4px;
            }

            .kerzox-empty {
                color: #aaa;
                font-size: 12px;
                padding: 12px;
                text-align: center;
            }

            .kerzox-footer {
                align-items: center;
                border-top: 1px solid rgba(255, 255, 255, 0.09);
                display: flex;
                gap: 8px;
                justify-content: space-between;
                padding: 10px;
                flex-shrink: 0;
            }

            .kerzox-icon-action {
                display: inline-flex;
                font-size: 12px;
                font-weight: 800;
                gap: 7px;
                height: 36px;
                min-height: 36px;
                padding: 0 10px;
            }

            @keyframes kerzox-spin {
                to {
                    transform: rotate(360deg);
                }
            }

            @media (max-width: 480px) {
                #${MENU_ID} {
                    left: 10px !important;
                    right: 10px;
                    width: auto !important;
                }

                .kerzox-menu-header {
                    grid-template-columns: 88px 1fr;
                }

                .kerzox-thumb {
                    width: 88px;
                }

                .kerzox-progress-meta {
                    grid-template-columns: 1fr;
                }

                .kerzox-progress-meta span:nth-child(1),
                .kerzox-progress-meta span:nth-child(2),
                .kerzox-progress-meta span:nth-child(3) {
                    text-align: left;
                }
            }
        `;

        document.documentElement.appendChild(style);
    }

    function removeInjectedUi() {
        document.getElementById(BUTTON_ID)?.remove();
        document.getElementById(MENU_ID)?.remove();
        document.getElementById(TITLE_SLOT_ID)?.remove();
        clearAllTimeouts();
        window.clearTimeout(statusTimer);
        window.clearTimeout(panelTimer);
        activeJobId = "";
        activeJobs.clear();
    }

    function createButton() {
        const button = document.createElement("button");
        button.id = BUTTON_ID;
        button.type = "button";
        button.title = "Download with MediaForge";
        button.setAttribute("aria-haspopup", "menu");
        button.setAttribute("aria-expanded", "false");
        button.innerHTML = `${icon("download")}<span>MediaForge</span><span class="kerzox-chevron">${icon("chevron")}</span>`;
        button.addEventListener("click", toggleMenu);
        return button;
    }

    function createMenu() {
        document.getElementById(MENU_ID)?.remove();

        const page = getPageInfo();
        const menu = document.createElement("div");
        menu.id = MENU_ID;
        menu.dataset.theme = "dark";
        menu.hidden = true;
        menu.setAttribute("role", "menu");
        menu.addEventListener("click", (event) => event.stopPropagation());

        const thumbUrl = page.videoId
            ? `https://i.ytimg.com/vi/${page.videoId}/hqdefault.jpg`
            : "https://i.ytimg.com/img/no_thumbnail.jpg";

        menu.innerHTML = `
            <div class="kerzox-menu-body">
                <div class="kerzox-menu-header">
                    <img class="kerzox-thumb" alt="" src="${thumbUrl}">
                    <div>
                        <p class="kerzox-title"></p>
                        <p class="kerzox-subtitle">MediaForge v${VERSION}</p>
                    </div>
                </div>
                <div class="kerzox-tabs">
                    <button class="kerzox-tab is-active" type="button" data-tab="downloads">${icon("download")}Download</button>
                    <button class="kerzox-tab" type="button" data-tab="queue">${icon("queue")}Queue</button>
                    <button class="kerzox-tab" type="button" data-tab="history">${icon("history")}History</button>
                </div>
                <div class="kerzox-panel is-active" data-panel="downloads">
                    <div class="kerzox-actions"></div>
                    <div class="kerzox-progress-card">
                        <div class="kerzox-status-row">
                            <span class="kerzox-spinner"></span>
                            <span class="kerzox-status-text">Ready</span>
                        </div>
                        <div class="kerzox-progress-percent">0%</div>
                        <div class="kerzox-progress-track"><div class="kerzox-progress-fill"></div></div>
                        <div class="kerzox-progress-meta">
                            <span data-progress-speed>Speed --</span>
                            <span data-progress-eta>ETA --</span>
                            <span data-progress-size>Size --</span>
                        </div>
                    </div>
                </div>
                <div class="kerzox-panel" data-panel="queue">
                    <div class="kerzox-list" data-queue-list><div class="kerzox-empty">No queued downloads</div></div>
                </div>
                <div class="kerzox-panel" data-panel="history">
                    <div class="kerzox-section-header">
                        <span class="kerzox-section-title">Download History</span>
                        <button id="kerzox-clear-history" class="kerzox-clear-button" type="button">Clear History</button>
                    </div>
                    <div class="kerzox-list" data-history-list><div class="kerzox-empty">No download history</div></div>
                </div>
                <div class="kerzox-footer">
                    <div style="display: flex; flex-direction: column; gap: 2px;">
                        <span class="kerzox-subtitle" style="font-weight: 800; color: #fff;">MediaForge v1.0</span>
                        <span class="kerzox-subtitle">Crafted by KERZOX</span>
                    </div>
                    <button class="kerzox-icon-action" type="button" data-settings>${icon("settings")}Settings</button>
                </div>
            </div>
            <div class="kerzox-modal" id="kerzox-confirm-modal" hidden>
                <div class="kerzox-modal-content">
                    <p class="kerzox-modal-text">Are you sure you want to clear all download history?</p>
                    <div class="kerzox-modal-actions">
                        <button id="kerzox-confirm-cancel" class="kerzox-tab" type="button">Cancel</button>
                        <button id="kerzox-confirm-clear" class="kerzox-confirm-btn" type="button">Clear History</button>
                    </div>
                </div>
            </div>
        `;

        menu.querySelector(".kerzox-title").textContent = page.title;
        menu.querySelector("[data-settings]").addEventListener("click", openSettings);
        menu.querySelectorAll(".kerzox-tab").forEach((tab) => {
            tab.addEventListener("click", () => activateTab(tab.dataset.tab));
        });

        // Setup confirmation modal handlers
        const confirmModal = menu.querySelector("#kerzox-confirm-modal");
        const clearHistoryBtn = menu.querySelector("#kerzox-clear-history");
        const confirmCancelBtn = menu.querySelector("#kerzox-confirm-cancel");
        const confirmClearBtn = menu.querySelector("#kerzox-confirm-clear");

        clearHistoryBtn?.addEventListener("click", () => {
            if (confirmModal) confirmModal.hidden = false;
        });

        confirmCancelBtn?.addEventListener("click", () => {
            if (confirmModal) confirmModal.hidden = true;
        });

        confirmClearBtn?.addEventListener("click", clearHistory);

        const actions = menu.querySelector(".kerzox-actions");
        DOWNLOAD_OPTIONS.forEach((option) => actions.appendChild(createDownloadAction(option)));

        document.body.appendChild(menu);
        applyTheme(menu);
        return menu;
    }

    function createDownloadAction(option) {
        const action = document.createElement("button");
        action.className = "kerzox-option";
        action.type = "button";
        action.setAttribute("role", "menuitem");
        action.dataset.mode = option.mode;
        action.innerHTML = `
            <span class="kerzox-option-icon">${icon(option.icon)}</span>
            <span class="kerzox-option-label"></span>
            <span class="kerzox-option-detail"></span>
        `;
        action.querySelector(".kerzox-option-label").textContent = option.label;
        action.querySelector(".kerzox-option-detail").textContent = option.detail;
        action.addEventListener("click", () => startDownload(option.mode));
        return action;
    }

    function findActionContainer() {
        const selectors = [
            "ytd-watch-metadata #actions #top-level-buttons-computed",
            "ytd-watch-metadata #actions-inner #top-level-buttons-computed",
            "ytd-watch-metadata #top-level-buttons-computed",
            "ytd-video-primary-info-renderer #top-level-buttons-computed",
            "#top-level-buttons-computed"
        ];

        return selectors.map((selector) => document.querySelector(selector)).find(Boolean);
    }

    function findTitleContainer() {
        const selectors = [
            "ytd-watch-metadata h1.ytd-watch-metadata",
            "ytd-watch-metadata h1",
            "ytd-video-primary-info-renderer h1",
            "#above-the-fold #title",
            "#title h1"
        ];

        return selectors.map((selector) => document.querySelector(selector)).find(Boolean);
    }

    function injectButton() {
        if (!isSupportedPage()) {
            currentPageKey = "";
            removeInjectedUi();
            return true; // Unsupported page; do not retry.
        }

        const pageKey = getPageInfo().href;
        const existingButton = document.getElementById(BUTTON_ID);
        const existingMenu = document.getElementById(MENU_ID);

        if (existingButton && existingMenu && currentPageKey === pageKey) {
            return true;
        }

        removeInjectedUi();
        ensureStyles();

        const button = createButton();
        createMenu();

        const actionContainer = findActionContainer();
        if (actionContainer) {
            actionContainer.appendChild(button);
            currentPageKey = pageKey;
            return true;
        }

        const titleContainer = findTitleContainer();
        if (titleContainer) {
            const slot = document.createElement("div");
            slot.id = TITLE_SLOT_ID;
            slot.appendChild(button);
            titleContainer.insertAdjacentElement("afterend", slot);
            currentPageKey = pageKey;
            return true;
        }

        return false;
    }

    function positionMenu() {
        const button = document.getElementById(BUTTON_ID);
        const menu = document.getElementById(MENU_ID);
        if (!button || !menu) return;

        const wasHidden = menu.hidden;
        if (wasHidden) {
            menu.hidden = false;
            menu.style.visibility = "hidden";
        }

        const rect = button.getBoundingClientRect();
        const width = Math.min(378, window.innerWidth - 20);
        menu.style.width = `${width}px`;

        // Clear inline maxHeight to measure natural offsetHeight
        menu.style.maxHeight = "";

        const menuHeight = menu.offsetHeight || 620;
        const belowTop = rect.bottom + 10;
        const aboveTop = rect.top - menuHeight - 10;
        const hasSpaceBelow = belowTop + menuHeight <= window.innerHeight - 10;
        const top = hasSpaceBelow ? belowTop : Math.max(10, aboveTop);
        const left = Math.min(Math.max(10, rect.right - width), window.innerWidth - width - 10);

        menu.classList.toggle("opens-above", !hasSpaceBelow);
        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
        menu.style.maxHeight = `${Math.min(menuHeight, window.innerHeight - 20)}px`;

        if (wasHidden) {
            menu.hidden = true;
            menu.style.visibility = "";
        }
    }

    function toggleMenu(event) {
        event.stopPropagation();

        const button = document.getElementById(BUTTON_ID);
        const menu = document.getElementById(MENU_ID) || createMenu();
        const willOpen = menu.hidden;

        if (willOpen) {
            refreshMenuMedia();
            refreshPanels();
            positionMenu();
            menu.hidden = false;
            requestAnimationFrame(() => menu.classList.add("is-open"));
        } else {
            closeMenu();
        }

        button?.setAttribute("aria-expanded", String(willOpen));
    }

    function closeMenu() {
        const button = document.getElementById(BUTTON_ID);
        const menu = document.getElementById(MENU_ID);

        if (menu) {
            menu.classList.remove("is-open");
            window.setTimeout(() => {
                if (!menu.classList.contains("is-open")) {
                    menu.hidden = true;
                }
            }, 160);
        }

        button?.setAttribute("aria-expanded", "false");
    }

    function refreshMenuMedia() {
        const menu = document.getElementById(MENU_ID);
        if (!menu) return;

        const page = getPageInfo();
        const image = menu.querySelector(".kerzox-thumb");
        const title = menu.querySelector(".kerzox-title");

        if (image && page.videoId) {
            image.src = `https://i.ytimg.com/vi/${page.videoId}/hqdefault.jpg`;
        }

        if (title) {
            title.textContent = page.title;
        }
    }

    function activateTab(tabName) {
        const menu = document.getElementById(MENU_ID);
        if (!menu) return;

        menu.querySelectorAll(".kerzox-tab").forEach((tab) => {
            tab.classList.toggle("is-active", tab.dataset.tab === tabName);
        });
        menu.querySelectorAll(".kerzox-panel").forEach((panel) => {
            panel.classList.toggle("is-active", panel.dataset.panel === tabName);
        });
        refreshPanels();
        positionMenu();
    }

    function setLoading(isLoading) {
        const menu = document.getElementById(MENU_ID);
        const buttons = menu?.querySelectorAll(".kerzox-option") || [];

        menu?.classList.toggle("is-loading", isLoading);
        buttons.forEach((button) => {
            button.disabled = isLoading;
        });
    }

    function updateProgress(job) {
        const menu = document.getElementById(MENU_ID);
        if (!menu) return;

        const progress = Math.max(0, Math.min(100, Number(job?.progress || 0)));
        const statusText = menu.querySelector(".kerzox-status-text");
        const percentText = menu.querySelector(".kerzox-progress-percent");
        const fill = menu.querySelector(".kerzox-progress-fill");
        const speed = menu.querySelector("[data-progress-speed]");
        const eta = menu.querySelector("[data-progress-eta]");
        const size = menu.querySelector("[data-progress-size]");

        const targetMessage = job?.message || "Ready";
        if (statusText && statusText.textContent !== targetMessage) {
            statusText.textContent = targetMessage;
        }
        
        const targetPercent = `${Math.round(progress)}%`;
        if (percentText && percentText.textContent !== targetPercent) {
            percentText.textContent = targetPercent;
        }
        
        const targetFill = `${progress}%`;
        if (fill && fill.style.width !== targetFill) {
            fill.style.width = targetFill;
        }

        const targetSpeed = job?.speed ? job.speed : "Speed --";
        if (speed && speed.textContent !== targetSpeed) {
            speed.textContent = targetSpeed;
        }

        const targetEta = job?.eta ? `ETA ${job.eta}` : "ETA --";
        if (eta && eta.textContent !== targetEta) {
            eta.textContent = targetEta;
        }

        const targetSize = job?.downloaded ? `${job.downloaded}${job.total ? ` / ${job.total}` : ""}` : "Size --";
        if (size && size.textContent !== targetSize) {
            size.textContent = targetSize;
        }
    }

    function setStatus(message, isLoading = false) {
        updateProgress({ message, progress: 0 });
        setLoading(isLoading);
    }

    async function startDownload(mode) {
        const url = getDownloadUrl(mode);

        if (!url) {
            setStatus("No supported YouTube video or playlist found.");
            return;
        }

        window.clearTimeout(statusTimer);
        activeJobId = "";
        activateTab("downloads");
        setStatus("Adding download to queue...", true);

        try {
            const response = await fetch(`${API_BASE_URL}/download`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, mode })
            });
            const data = await response.json().catch(() => ({}));

            if (!response.ok || !data.success) {
                setStatus(data.message || "Download request failed.");
                return;
            }

            activeJobId = data.job_id;
            activeJobs.add(data.job_id);
            updateProgress(data.job);
            setLoading(true);
            refreshPanels();
            pollStatus();
        } catch (error) {
            console.error("Kerzox backend error:", error);
            setStatus("Backend not running at http://127.0.0.1:5000");
        }
    }

    async function pollStatus() {
        if (!activeJobId) return;

        window.clearTimeout(statusTimer);

        try {
            const response = await fetch(`${API_BASE_URL}/status/${activeJobId}`);
            const data = await response.json().catch(() => ({}));

            if (!response.ok || !data.success || !data.job) {
                throw new Error(data.message || "Invalid status response");
            }

            pollFailureCount = 0;
            const job = data.job;
            updateProgress(job);
            setLoading(["queued", "running", "downloading", "retrying"].includes(job.status));

            if (job.status === "completed") {
                activeJobs.delete(activeJobId);
                activeJobId = "";
                notifyDownload("MediaForge download complete", job.filename || `${job.label} completed`, job.id);
                refreshPanels();
                return;
            }

            if (job.status === "failed") {
                activeJobs.delete(activeJobId);
                activeJobId = "";
                updateProgress({ ...job, message: job.error || "Download failed" });
                notifyDownload("MediaForge download failed", job.error || "Download failed", job.id);
                refreshPanels();
                return;
            }

            statusTimer = window.setTimeout(pollStatus, 1200);
        } catch (error) {
            console.error("Kerzox status error:", error);
            pollFailureCount++;

            if (pollFailureCount >= MAX_POLL_FAILURES) {
                activeJobs.delete(activeJobId);
                activeJobId = "";
                setStatus("Connection lost. Download status untracked.");
                setLoading(false);
                refreshPanels();
            } else {
                setStatus(`Backend offline. Retrying... (${pollFailureCount}/${MAX_POLL_FAILURES})`);
                statusTimer = window.setTimeout(pollStatus, 2000);
            }
        }
    }

    async function refreshPanels() {
        const menu = document.getElementById(MENU_ID);
        if (!menu || menu.hidden) return;

        try {
            const response = await fetch(`${API_BASE_URL}/queue`);
            const data = await response.json().catch(() => ({}));
            const queueData = data.queue || {};

            renderQueue(queueData);
            renderHistory(queueData.history || []);

            if (activeJobs.size > 0) {
                const historyList = queueData.history || [];
                const failedList = queueData.failed || [];

                historyList.forEach((job) => {
                    if (activeJobs.has(job.id)) {
                        if (job.status === "completed") {
                            activeJobs.delete(job.id);
                            notifyDownload("MediaForge download complete", job.filename || `${job.label} completed`, job.id);
                        } else if (job.status === "failed") {
                            activeJobs.delete(job.id);
                            notifyDownload("MediaForge download failed", job.error || "Download failed", job.id);
                        }
                    }
                });

                failedList.forEach((job) => {
                    if (activeJobs.has(job.id)) {
                        activeJobs.delete(job.id);
                        notifyDownload("MediaForge download failed", job.error || "Download failed", job.id);
                    }
                });
            }
        } catch (error) {
            console.error("Kerzox panel refresh error:", error);
        }

        window.clearTimeout(panelTimer);
        panelTimer = window.setTimeout(refreshPanels, 3000);
    }

    function renderQueue(queueData) {
        const list = document.querySelector(`#${MENU_ID} [data-queue-list]`);
        if (!list) return;

        const items = [];
        if (queueData.active) items.push({ ...queueData.active, queueLabel: "Active" });
        (queueData.queued || []).forEach((job) => items.push({ ...job, queueLabel: "Pending" }));
        (queueData.failed || []).slice(0, 3).forEach((job) => items.push({ ...job, queueLabel: "Failed" }));

        if (!items.length) {
            list.innerHTML = '<div class="kerzox-empty">No queued downloads</div>';
            return;
        }

        list.innerHTML = "";
        items.slice(0, 8).forEach((job) => list.appendChild(createListItem({
            title: job.filename || job.label || job.mode,
            meta: [job.queueLabel, job.status, `${Math.round(Number(job.progress || 0))}%`]
        })));
    }

    function renderHistory(history) {
        const list = document.querySelector(`#${MENU_ID} [data-history-list]`);
        if (!list) return;

        if (!history.length) {
            list.innerHTML = '<div class="kerzox-empty">No download history</div>';
            return;
        }

        list.innerHTML = "";
        history.slice(0, 8).forEach((job) => list.appendChild(createListItem({
            title: job.filename || job.label || job.mode,
            meta: [job.label || job.mode, job.status, formatDate(job.completed_at || job.started_at || job.queued_at)]
        })));
    }

    function createListItem({ title, meta }) {
        const item = document.createElement("div");
        item.className = "kerzox-list-item";
        item.innerHTML = `
            <div class="kerzox-list-title"></div>
            <div class="kerzox-list-meta"></div>
        `;
        item.querySelector(".kerzox-list-title").textContent = title || "MediaForge download";
        const metaNode = item.querySelector(".kerzox-list-meta");
        meta.filter(Boolean).forEach((value) => {
            const span = document.createElement("span");
            span.textContent = value;
            metaNode.appendChild(span);
        });
        return item;
    }

    function formatDate(value) {
        if (!value) return "";

        try {
            return new Date(value).toLocaleString();
        } catch {
            return value;
        }
    }

    function notifyDownload(title, message, jobId) {
        if (!jobId || notifiedJobIds.has(jobId)) return;
        notifiedJobIds.add(jobId);

        chrome.runtime?.sendMessage?.({
            type: "KERZOX_NOTIFY",
            title,
            message
        });
    }

    function openSettings() {
        chrome.runtime?.sendMessage?.({ type: "KERZOX_OPEN_OPTIONS" });
    }

    let isClearingHistory = false;
    async function clearHistory() {
        if (isClearingHistory) return;
        isClearingHistory = true;

        const confirmModal = document.getElementById("kerzox-confirm-modal");
        if (confirmModal) confirmModal.hidden = true;

        try {
            const response = await fetch(`${API_BASE_URL}/history/clear`, {
                method: "POST"
            });
            const data = await response.json().catch(() => ({}));

            if (!response.ok || !data.success) {
                setStatus(data.message || "Failed to clear history.");
                const list = document.querySelector(`#${MENU_ID} [data-history-list]`);
                if (list) {
                    list.innerHTML = `<div class="kerzox-empty" style="color: #ff4d4d;">${data.message || "Failed to clear history."}</div>`;
                }
                return;
            }

            setStatus("History cleared successfully");
            const list = document.querySelector(`#${MENU_ID} [data-history-list]`);
            if (list) {
                list.innerHTML = '<div class="kerzox-empty">History cleared successfully</div>';
            }
            await refreshPanels();
        } catch (error) {
            console.error("Kerzox clear history error:", error);
            setStatus("Backend not running at http://127.0.0.1:5000");
            const list = document.querySelector(`#${MENU_ID} [data-history-list]`);
            if (list) {
                list.innerHTML = '<div class="kerzox-empty" style="color: #ff4d4d;">Backend not running. Could not clear history.</div>';
            }
        } finally {
            isClearingHistory = false;
        }
    }

    async function applyTheme(menu = document.getElementById(MENU_ID)) {
        if (!menu || !chrome.storage?.local) return;

        try {
            const stored = await chrome.storage.local.get(["kerzoxTheme"]);
            menu.dataset.theme = stored.kerzoxTheme || "dark";
        } catch {
            menu.dataset.theme = "dark";
        }
    }

    function scheduleInject() {
        clearAllTimeouts();
        const id = window.setTimeout(() => {
            if (injectButton()) return;

            const t1 = window.setTimeout(() => { if (injectButton()) clearAllTimeouts(); }, 500);
            const t2 = window.setTimeout(() => { if (injectButton()) clearAllTimeouts(); }, 1500);
            const t3 = window.setTimeout(() => { if (injectButton()) clearAllTimeouts(); }, 3000);
            activeTimeouts.push(t1, t2, t3);
        }, 120);
        activeTimeouts.push(id);
    }

    function watchYouTubeNavigation() {
        const onNavigate = () => {
            currentPageKey = "";
            clearAllTimeouts();
            
            if (isSupportedPage()) {
                connectObserver();
                scheduleInject();
            } else {
                disconnectObserver();
                removeInjectedUi();
            }
        };

        window.addEventListener("yt-navigate-finish", onNavigate);
        window.addEventListener("popstate", onNavigate);
        window.addEventListener("resize", positionMenu);
        window.addEventListener("scroll", positionMenu, true);
        document.addEventListener("click", closeMenu);

        const originalPushState = history.pushState;
        const originalReplaceState = history.replaceState;

        history.pushState = function (...args) {
            const result = originalPushState.apply(this, args);
            onNavigate();
            return result;
        };

        history.replaceState = function (...args) {
            const result = originalReplaceState.apply(this, args);
            onNavigate();
            return result;
        };
    }

    function start() {
        watchYouTubeNavigation();
        if (isSupportedPage()) {
            connectObserver();
            scheduleInject();
        } else {
            removeInjectedUi();
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
})();
