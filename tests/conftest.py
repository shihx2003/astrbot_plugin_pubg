"""Pytest configuration ensuring plugin package imports work standalone without AstrBot."""

from __future__ import annotations

import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Provide lightweight astrbot.api.logger stub when running standalone pytest outside AstrBot
if "astrbot.api" not in sys.modules:
    class _StubLogger:
        def debug(self, msg: object, *args: object, **kwargs: object) -> None:
            pass

        def info(self, msg: object, *args: object, **kwargs: object) -> None:
            pass

        def warning(self, msg: object, *args: object, **kwargs: object) -> None:
            pass

        def error(self, msg: object, *args: object, **kwargs: object) -> None:
            pass

        def exception(self, msg: object, *args: object, **kwargs: object) -> None:
            pass

    astrbot_pkg = sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = _StubLogger()  # type: ignore[attr-defined]
    astrbot_pkg.api = astrbot_api  # type: ignore[attr-defined]
    sys.modules["astrbot.api"] = astrbot_api
