---
id: tool-set_todos
title: Set Todos
category: tool
tags:
- tool
- set_todos
related: []
tool_generated: null
created_at: '2026-08-13T16:23:08.796905+00:00'
updated_at: '2026-08-13T16:23:08.796905+00:00'
usage_count: 0
---

# Set Todos

## Problem
Run tool `set_todos` from agent reasoning.

## Solution
Update the active query's TodoList panel (the phases list under the chat input). Use at the start of any multi-step task with a checklist, and update as phases move through pending → in_progress → done. Each item is `{id, title, status}` where status is 'pending' | 'in_progress' | 'done'. Send the full list each call; partial updates are done by re-sending the whole list with the changed field. Empty list clears the panel. Stable ids across calls let the UI animate transitions.

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
