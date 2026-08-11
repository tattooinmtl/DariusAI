#!/usr/bin/env python3
"""
Universal Multi-Harness Lifecycle Guard
Compatible with Antigravity, .omni, DariusAI-Harness, Thoth, Claude Code, Codex, Cursor.
Handles PreToolUse, PostToolUse, PreInvocation, PostInvocation, and Stop events.
"""

import sys
import json
import re
import os
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.resolve()
PATTERNS_FILE = HOOKS_DIR / "dangerous-patterns.txt"

def load_dangerous_patterns():
    patterns = []
    if PATTERNS_FILE.exists():
        for line in PATTERNS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns

def check_command_safety(command_str: str, patterns: list) -> tuple[bool, str]:
    for pattern in patterns:
        try:
            if re.search(pattern, command_str, re.IGNORECASE):
                return False, f"Command matched dangerous pattern: {pattern}"
        except re.error:
            if pattern in command_str:
                return False, f"Command contained dangerous string: {pattern}"
    return True, ""

def main():
    raw_input = sys.stdin.read()
    if not raw_input.strip():
        print(json.dumps({}))
        sys.exit(0)

    try:
        payload = json.loads(raw_input)
    except Exception as e:
        print(json.dumps({"error": f"Invalid JSON stdin: {e}"}))
        sys.exit(0)

    # 1. Determine payload structure (PreToolUse)
    tool_call = payload.get("toolCall") or payload.get("tool_input") or {}
    tool_name = tool_call.get("name") or payload.get("toolName") or ""
    args = tool_call.get("args") or payload.get("tool_input") or payload.get("args") or {}

    command = args.get("CommandLine") or args.get("command") or payload.get("command") or ""

    if command:
        patterns = load_dangerous_patterns()
        safe, reason = check_command_safety(command, patterns)
        if not safe:
            # Check if calling in Cursor mode or standard Antigravity/DariusAI mode
            if "permission" in payload or "cursor" in sys.argv:
                response = {
                    "permission": "deny",
                    "user_message": "Command guard blocked dangerous shell operation.",
                    "agent_message": f"Blocked by harness_guard: {reason}. Do not attempt to bypass."
                }
            else:
                response = {
                    "decision": "deny",
                    "reason": f"Global Harness Security Block: {reason}. Do not retry this command."
                }
            print(json.dumps(response))
            sys.exit(0)

    # PreInvocation handling
    if "invocationNum" in payload:
        response = {
            "injectSteps": [
                {
                    "ephemeralMessage": "[Harness Guard]: Verify compiler status, dependency locks, and test outputs before reporting completion."
                }
            ]
        }
        print(json.dumps(response))
        sys.exit(0)

    # Default allow response
    response = {
        "decision": "allow",
        "reason": "Harness security verification passed."
    }
    print(json.dumps(response))

if __name__ == "__main__":
    main()
