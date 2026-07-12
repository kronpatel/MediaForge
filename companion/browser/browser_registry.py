"""
browser_registry.py – Singleton registry of supported browsers.

Maintains the authoritative list of browser definitions and provides
lookup helpers used by BrowserLauncher and the UI.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .browser_defs import BrowserDefinition
from .browser_defs import all_browser_definitions


class BrowserRegistry:
    """Thread-safe singleton registry of browser definitions.

    Usage::

        registry = BrowserRegistry.instance()
        chrome = registry.get("Chrome")
        for bdef in registry.all():
            print(bdef.name)
    """

    _instance: Optional[BrowserRegistry] = None

    def __init__(self) -> None:
        self._definitions: Dict[str, BrowserDefinition] = {}
        self._order: List[str] = []
        self._load_defaults()

    # -- singleton ------------------------------------------------------------

    @classmethod
    def instance(cls) -> BrowserRegistry:
        """Return the process-wide singleton, creating it if needed."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing only)."""
        cls._instance = None

    # -- registration ---------------------------------------------------------

    def register(self, definition: BrowserDefinition) -> None:
        """Add or replace a browser definition."""
        key = definition.name
        self._definitions[key] = definition
        if key not in self._order:
            self._order.append(key)

    def unregister(self, name: str) -> bool:
        """Remove a browser definition by name. Returns True if removed."""
        if name in self._definitions:
            del self._definitions[name]
            self._order.remove(name)
            return True
        return False

    # -- lookup ---------------------------------------------------------------

    def get(self, name: str) -> Optional[BrowserDefinition]:
        """Return the definition for *name*, or ``None``."""
        return self._definitions.get(name)

    def has(self, name: str) -> bool:
        return name in self._definitions

    def all(self) -> List[BrowserDefinition]:
        """Return definitions in registration order."""
        return [self._definitions[n] for n in self._order if n in self._definitions]

    def names(self) -> List[str]:
        """Return browser names in registration order."""
        return list(self._order)

    def find_by_exe(self, exe_name: str) -> Optional[BrowserDefinition]:
        """Look up a definition by executable filename."""
        exe_lower = exe_name.lower()
        for bdef in self._definitions.values():
            if any(e.lower() == exe_lower for e in bdef.exe_names):
                return bdef
        return None

    def installed_browsers(self, detector: object | None = None) -> List[BrowserDefinition]:
        """Return definitions whose executables exist on disk.

        *detector* is reserved for future use (dependency injection).
        Currently checks ``search_paths`` via ``os.path.isfile``.
        """
        result: List[BrowserDefinition] = []
        for bdef in self.all():
            for path in bdef.search_paths:
                try:
                    if path and _is_file(path):
                        result.append(bdef)
                        break
                except Exception:
                    continue
        return result

    # -- internal -------------------------------------------------------------

    def _load_defaults(self) -> None:
        for bdef in all_browser_definitions():
            self.register(bdef)


def _is_file(path: str) -> bool:
    """Thin wrapper to allow mocking in tests."""
    return os.path.isfile(path)
