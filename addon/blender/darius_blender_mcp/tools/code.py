"""`execute_python` — the escape hatch, behind an AST gate."""

from __future__ import annotations

from typing import Any

from ..server.mcp_server import registry, run_on_main
from ..utils.safe_eval import UnsafeCode, run


def _execute_impl(code: str) -> dict[str, Any]:
    try:
        return run(code)
    except UnsafeCode as exc:
        # A rejection is a result, not a crash: the agent should see why
        # and rewrite, not retry the same thing.
        return {"ok": False, "rejected": True, "reason": str(exc)}


@registry.tool(
    name="execute_python",
    description=("Run Python against Blender's API for anything the other tools do not "
                 "cover. `bpy`, `bmesh`, `mathutils`, `math`, `random` and `json` are in "
                 "scope. Assign to a variable named `result` to return a value. Imports "
                 "outside that set, and anything that leaves Blender, are rejected."),
    input_schema={
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Python source."}},
        "required": ["code"],
        "additionalProperties": False,
    },
)
def execute_python(code: str) -> dict[str, Any]:
    return run_on_main(_execute_impl, code, timeout=300.0)
