"""Exposing MCP tools to the agent.

Discovered MCP tools become ordinary Darius tools, so the model calls
`blender_build_structure` exactly the way it calls `read_file` — no second
tool-calling convention to learn, and no prompt explaining that some tools
are special.

Names are prefixed (`blender_…`) because an MCP server names its tools for
its own world, and `scene_info` or `render_image` in a flat namespace is
one collision away from being ambiguous.
"""

from __future__ import annotations

import json
from typing import Any

from .blender import BlenderBridge, get_bridge
from .client import MCPError

PREFIX = "blender_"

# The game3d tools already read as a set; prefixing them again would give
# `blender_game3d_build_structure`, which is a mouthful for no clarity.
_KEEP_NAME_PREFIXES = ("game3d_",)


def agent_tool_name(mcp_name: str) -> str:
    if mcp_name.startswith(_KEEP_NAME_PREFIXES):
        return mcp_name
    return f"{PREFIX}{mcp_name}"


def describe_tools(bridge: BlenderBridge | None = None) -> list[dict[str, Any]]:
    bridge = bridge or get_bridge()
    return [
        {
            "name": agent_tool_name(tool.name),
            "mcp_name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in bridge.tools
    ]


def register_blender_tools(reg, bridge: BlenderBridge | None = None, store=None) -> list[str]:
    """Add every discovered Blender tool to an agent ToolRegistry.

    Called after a successful handshake — before one there is nothing to
    register, and advertising tools that cannot run is worse than
    advertising none. Passing `store` also gives each tool a node in the
    brain graph, so Blender activity lights up the neural view like any
    other tool; without one they are simply callable.
    """
    from ..agent.tools import ToolSpec, _register  # noqa: PLC0415 - avoids a cycle

    bridge = bridge or get_bridge()
    added: list[str] = []

    for tool in bridge.tools:
        name = agent_tool_name(tool.name)

        def call(_mcp_name=tool.name, **kwargs):
            try:
                result = bridge.call(_mcp_name, kwargs)
            except MCPError as exc:
                # The model can act on this: reconnect, or pick another
                # tool. A raised exception would end the turn instead.
                return f"Blender tool {_mcp_name} failed: {exc}"
            if isinstance(result, str):
                return result
            return json.dumps(result, indent=2, default=str)

        spec = ToolSpec(
            name=name,
            description=f"[Blender] {tool.description}",
            input_schema=tool.input_schema,
            fn=call,
        )
        if store is not None:
            _register(reg, store, spec)
        else:
            reg.register(spec)
        added.append(name)
    return added
