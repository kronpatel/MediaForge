"""
browser – Browser detection, registry, profile discovery, session detection,
extension installation, and launch infrastructure.

Public API
----------
BrowserLauncher
    Static methods for detecting and launching Chromium-based browsers.

BrowserRegistry
    Singleton registry of browser definitions (Chrome, Brave, Edge).

BrowserProfileManager
    Read-only Chromium profile discovery APIs.

BrowserSessionManager
    Read-only Chromium process detection via psutil.

ExtensionInstallationEngine
    Extension validation and browser launch with ``--load-extension``.

BrowserInfo / BrowserDefinition
    Runtime detection results vs. static platform definitions.

ProfileMetadata / BrowserScanResult
    Strongly typed profile scan results.

ProcessInfo / BrowserSessionResult
    Strongly typed session scan results.

LaunchResult / LaunchErrorCode
    Launch outcome types.

ExtensionErrorCode / ExtensionValidationResult / ExtensionLaunchResult
    Extension installation engine result types.

EnterprisePolicyResult
    Windows Registry enterprise policy check result.

Backward-compatible free functions
-----------------------------------
detect_chrome()
detect_all_browsers()
detect_first_browser()
"""

from .browser_defs import (
    BrowserCapabilities,
    BrowserDefinition,
    BrowserFeature,
    all_browser_definitions,
    brave_definition,
    chrome_definition,
    edge_definition,
)
from .browser_extension_installer import (
    ExtensionErrorCode,
    ExtensionInstallationEngine,
    ExtensionLaunchResult,
    ExtensionValidationResult,
)
from .browser_info import (
    BrowserInfo,
    BrowserProfileResult,
    BrowserRegistrationResult,
    EnterprisePolicyResult,
    ExtensionStatus,
    LaunchErrorCode,
    LaunchResult,
)
from .browser_launcher import (
    BrowserLauncher,
    detect_all_browsers,
    detect_chrome,
    detect_first_browser,
)
from .browser_profiles import (
    BrowserProfileManager,
    BrowserScanResult,
    ProfileMetadata,
)
from .browser_sessions import (
    BrowserSessionManager,
    BrowserSessionResult,
    ProcessInfo,
)
from .browser_registry import BrowserRegistry

__all__ = [
    # Launcher
    "BrowserLauncher",
    "detect_chrome",
    "detect_all_browsers",
    "detect_first_browser",
    # Registry
    "BrowserRegistry",
    # Profile discovery
    "BrowserProfileManager",
    "BrowserScanResult",
    "ProfileMetadata",
    # Session detection
    "BrowserSessionManager",
    "BrowserSessionResult",
    "ProcessInfo",
    # Extension installation
    "ExtensionInstallationEngine",
    "ExtensionErrorCode",
    "ExtensionValidationResult",
    "ExtensionLaunchResult",
    # Definitions
    "BrowserDefinition",
    "BrowserCapabilities",
    "BrowserFeature",
    "all_browser_definitions",
    "chrome_definition",
    "brave_definition",
    "edge_definition",
    # Info types
    "BrowserInfo",
    "BrowserProfileResult",
    "BrowserRegistrationResult",
    "EnterprisePolicyResult",
    "ExtensionStatus",
    "LaunchErrorCode",
    "LaunchResult",
]
