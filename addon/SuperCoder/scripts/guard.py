#!/usr/bin/env python3
"""
SuperCoder Command Safety Guard
Checks shell commands against dangerous and destructive patterns.
"""

import sys
import re
from typing import Tuple, List

DANGEROUS_PATTERNS: List[str] = [
    r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+(/|~|\.\*)(\s|$)",
    r"rm\s+-rf\s+(/|~|\.\*)",
    r"Remove-Item\s+.*-Recurse\s+[A-Za-z]:\\",
    r"del\s+/[sS]\s+/[qQ]\s+[A-Za-z]:\\",
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R\s+root\s+/",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    r"mkfs\.",
    r"dd\s+if=/dev/zero\s+of=/dev/sd",
    r"curl\s+.*\|\s*(bash|sh)",
    r"wget\s+.*\|\s*(bash|sh)",
    r"powershell\s+.*-enc(odedCommand)?\s+[A-Za-z0-9+/=]{40,}",
    r"cat\s+~/\.ssh/id_rsa",
    r"type\s+%.*USERPROFILE%\\\.ssh\\id_rsa",
]

def check_command(command: str) -> Tuple[bool, str]:
    """Returns (is_safe, reason)."""
    command_clean = command.strip()
    if not command_clean:
        return True, "Empty command"

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command_clean, re.IGNORECASE):
            return False, f"Matched dangerous pattern: {pattern}"

    return True, "Passed safety checks"

def main():
    if len(sys.argv) < 2:
        print("Usage: guard.py <command_string>")
        sys.exit(1)

    cmd = " ".join(sys.argv[1:])
    safe, reason = check_command(cmd)

    if safe:
        print(f"[OK] {reason}")
        sys.exit(0)
    else:
        print(f"[DENIED] Command blocked by SuperCoder Safety Guard: {reason}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
