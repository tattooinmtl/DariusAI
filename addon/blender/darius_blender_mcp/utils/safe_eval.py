"""An AST gate for agent-written Blender code.

`execute_python` is the escape hatch that makes the rest of the tool set
optional — anything bpy can do, the agent can reach. That power is the
whole point and also the whole risk, so the code is parsed and inspected
before it runs.

This is a **gate, not a sandbox**. It stops the obvious ways to leave
Blender (import os, __import__, open, getattr chains into builtins) and it
will not stop a determined attacker. It runs code the user's own agent
wrote, on the user's own machine, over a loopback socket — the same trust
level as the terminal panel.

Pure stdlib and no bpy import, so it is testable outside Blender.
"""

from __future__ import annotations

import ast
from typing import Any

MAX_STATEMENTS = 200
MAX_LENGTH = 40_000

# Modules that exist to leave the process. `bpy`, `bmesh`, `mathutils`,
# `math` and `random` are what Blender scripting is made of and stay.
FORBIDDEN_MODULES = frozenset({
    "os", "sys", "subprocess", "shutil", "socket", "urllib", "http", "requests",
    "ftplib", "smtplib", "telnetlib", "ssl", "asyncio", "multiprocessing",
    "threading", "ctypes", "importlib", "pickle", "marshal", "shelve", "pty",
    "signal", "webbrowser", "tempfile", "pathlib", "glob", "io", "builtins",
})

FORBIDDEN_NAMES = frozenset({
    "__import__", "eval", "exec", "compile", "open", "input", "breakpoint",
    "globals", "locals", "vars", "memoryview", "exit", "quit",
})

# Attribute names that only appear when someone is walking out of the
# interpreter or deleting things off disk.
FORBIDDEN_ATTRIBUTES = frozenset({
    "__globals__", "__builtins__", "__subclasses__", "__bases__", "__mro__",
    "__code__", "__closure__", "__class__", "__dict__", "__loader__", "__spec__",
    "system", "popen", "spawn", "fork", "execv", "execve", "rmtree", "unlink",
    "remove", "rmdir", "removedirs", "kill", "terminate",
})

ALLOWED_MODULES = frozenset({"bpy", "bmesh", "mathutils", "math", "random", "json", "colorsys"})


class UnsafeCode(ValueError):
    """The code was rejected before it ran."""


def _module_root(name: str) -> str:
    return (name or "").split(".")[0]


def check(source: str) -> None:
    """Raise UnsafeCode if this must not run."""
    if not source or not source.strip():
        raise UnsafeCode("empty code")
    if len(source) > MAX_LENGTH:
        raise UnsafeCode(f"code is {len(source)} chars; limit is {MAX_LENGTH}")

    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise UnsafeCode(f"syntax error on line {exc.lineno}: {exc.msg}") from exc

    if len(tree.body) > MAX_STATEMENTS:
        raise UnsafeCode(f"{len(tree.body)} top-level statements; limit is {MAX_STATEMENTS}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _module_root(alias.name)
                if root in FORBIDDEN_MODULES or root not in ALLOWED_MODULES:
                    raise UnsafeCode(f"import of {alias.name!r} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            root = _module_root(node.module or "")
            if root in FORBIDDEN_MODULES or root not in ALLOWED_MODULES:
                raise UnsafeCode(f"import from {node.module!r} is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES:
                raise UnsafeCode(f"attribute {node.attr!r} is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                raise UnsafeCode(f"{node.id!r} is not allowed")


def safe_globals() -> dict[str, Any]:
    """A curated namespace. `__builtins__` is trimmed to the harmless
    parts — leaving the real one in place would make every check above
    pointless, since `__builtins__.__import__` is one attribute away."""
    import json
    import math
    import random

    allowed_builtins = {
        name: __builtins__[name] if isinstance(__builtins__, dict)
        else getattr(__builtins__, name)
        for name in (
            "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
            "float", "format", "frozenset", "getattr", "hasattr", "hash", "int",
            "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min",
            "next", "print", "range", "repr", "reversed", "round", "set", "setattr",
            "slice", "sorted", "str", "sum", "tuple", "type", "zip",
            "True", "False", "None", "Exception", "ValueError", "TypeError",
            "RuntimeError", "KeyError", "IndexError", "AttributeError",
        )
        if (name in __builtins__ if isinstance(__builtins__, dict)
            else hasattr(__builtins__, name))
    }

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        """`import bpy` has to work — it is the whole point — but only for
        the allow-list. Without this, executed code cannot import at all,
        because a trimmed __builtins__ has no __import__ for the import
        statement to call."""
        root = (name or "").split(".")[0]
        if root not in ALLOWED_MODULES:
            raise ImportError(f"import of {name!r} is not allowed")
        return real_import(name, globals, locals, fromlist, level)

    real_import = (__builtins__["__import__"] if isinstance(__builtins__, dict)
                   else __builtins__.__import__)
    allowed_builtins["__import__"] = guarded_import

    namespace: dict[str, Any] = {
        "__builtins__": allowed_builtins,
        "math": math, "random": random, "json": json,
    }
    try:  # pragma: no cover - only inside Blender
        import bmesh
        import bpy
        import mathutils
        namespace.update({"bpy": bpy, "bmesh": bmesh, "mathutils": mathutils})
    except ImportError:
        pass
    return namespace


def run(source: str) -> dict[str, Any]:
    """Gate, then execute. `result` is whatever the code assigned to a
    variable of that name — a return channel, since exec has none."""
    check(source)
    namespace = safe_globals()
    exec(compile(source, "<darius-mcp>", "exec"), namespace)  # noqa: S102 - the point of the tool
    value = namespace.get("result")
    return {
        "ok": True,
        "result": value if isinstance(value, (str, int, float, bool, list, dict, type(None)))
                  else repr(value),
        "names": sorted(k for k in namespace if not k.startswith("__")
                        and k not in {"bpy", "bmesh", "mathutils", "math", "random", "json"}),
    }
