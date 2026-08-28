"""Tool registration.

Importing a module runs its `@registry.tool(...)` decorators, so this list
*is* the tool set — there is no second place to keep in sync.
"""

from __future__ import annotations

from . import code, core, game3d, modeling, sculpting

__all__ = ("core", "game3d", "code", "modeling", "sculpting")
