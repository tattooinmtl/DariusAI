---
id: tool-invoke_skill
title: Invoke Skill
category: tool
tags:
- tool
- invoke_skill
related: []
tool_generated: null
created_at: '2026-08-13T16:23:08.812555+00:00'
updated_at: '2026-08-13T16:23:08.812555+00:00'
usage_count: 0
---

# Invoke Skill

## Problem
Run tool `invoke_skill` from agent reasoning.

## Solution
Load a skill by name (e.g. 'brainstorming', 'systematic-debugging') and return its full SKILL.md body so the model can follow its checklist. This is the harness's equivalent of the runtime's `Skill` tool, and the entry point for superpowers' methodology. Use this before any creative work or bug fix — the using-superpowers bootstrap says so. Accepts the name with or without a group prefix (e.g. 'brainstorming' or 'superpowers:brainstorming').

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
