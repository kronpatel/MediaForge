"""
browser – Browser detection, registry, profile discovery, session detection,
extension installation, launch infrastructure, and automation engine.

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

BrowserAutomationEngine
    Singleton orchestrator for browser automation session lifecycles.

AutomationSteps
    Ordered pipeline of step handlers for browser automation workflows.

StepDescriptor
    Immutable descriptor for a single automation step.

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

AutomationState / AutomationErrorCode
    Automation state machine and error code definitions.

AutomationError
    Structured error information for automation failures.

AutomationResult
    Immutable outcome of an automation session.

AutomationSession
    Mutable lifecycle tracker for automation runs.

Step handlers
-------------
detect_browser
validate_extension
launch_browser
detect_profiles
detect_running
verify_installation

Backward-compatible free functions
-----------------------------------
detect_chrome()
detect_all_browsers()
detect_first_browser()

Singleton helpers
-----------------
get_automation_engine()
    Return the global BrowserAutomationEngine instance.
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
from .automation import (
    BrowserAutomationEngine,
    ProgressCallback,
    get_automation_engine,
    reset_automation_engine,
)
from .automation_defs import (
    AutomationErrorCode,
    AutomationState,
)
from .automation_errors import AutomationError
from .automation_result import AutomationResult
from .automation_session import AutomationSession
from .automation_steps import AutomationSteps, StepDescriptor
from .step_handlers import (
    detect_browser,
    detect_profiles,
    detect_running,
    launch_browser,
    validate_extension,
    verify_installation,
)

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
    # Automation engine
    "BrowserAutomationEngine",
    "get_automation_engine",
    "reset_automation_engine",
    "ProgressCallback",
    # Automation state machine
    "AutomationState",
    "AutomationErrorCode",
    # Automation types
    "AutomationError",
    "AutomationResult",
    "AutomationSession",
    # Pipeline orchestration
    "AutomationSteps",
    "StepDescriptor",
    # Step handlers
    "detect_browser",
    "detect_profiles",
    "detect_running",
    "launch_browser",
    "validate_extension",
    "verify_installation",
]
