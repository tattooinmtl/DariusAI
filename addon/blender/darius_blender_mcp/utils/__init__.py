"""Utilities that do not need Blender."""

from __future__ import annotations

from .safe_eval import UnsafeCode, check, run, safe_globals

__all__ = ("UnsafeCode", "check", "run", "safe_globals")
