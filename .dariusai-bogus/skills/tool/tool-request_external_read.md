---
id: tool-request_external_read
title: Request External Read
category: tool
tags:
- tool
- request_external_read
related: []
tool_generated: null
created_at: '2026-08-28T15:24:34.338314+00:00'
updated_at: '2026-08-28T15:24:34.338314+00:00'
usage_count: 0
---

# Request External Read

## Problem
Run tool `request_external_read` from agent reasoning.

## Solution
Ask the human for one-turn permission to read a directory tree OUTSIDE the sandbox root — e.g. a reference project the user wants you to study before writing something compatible with it. Grants are: (1) exactly the folder given plus all subfolders, never a parent; (2) READ-ONLY — the file-write tool refuses paths under the grant, and destructive shell commands (rm, del, mv, rmdir, git reset --hard, git clean, output redirection into the tree, etc.) are refused too; (3) valid for THIS user turn only — the next user message clears every grant and you must ask again. Provide a clear `reason` — the human sees it in the prompt and it is what convinces them to approve.

## Code Examples
_(not filled in)_

## Best Practices
Used automatically by tool-calling chat turns.

## Edge Cases / Gotchas
_(not filled in)_

## Sources
(none yet)

## Related Skills
(none)
