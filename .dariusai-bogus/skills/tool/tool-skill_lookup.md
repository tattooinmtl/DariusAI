---
id: tool-skill_lookup
title: Skill Lookup
category: tool
tags:
- tool
- skill_lookup
related: []
tool_generated: null
created_at: '2026-08-19T21:55:18.782574+00:00'
updated_at: '2026-08-19T21:55:18.782574+00:00'
usage_count: 0
---

# Skill Lookup

## Problem
Run tool `skill_lookup` from agent reasoning.

## Solution
Retrieve the specific paragraphs from the skill library that answer a question, without loading any skill in full. This is the cheap first move for 'how should I do X' — it searches inside every SKILL.md and returns a few matching passages with their skill name and heading. Follow up with invoke_skill(name, query=...) only if a passage shows you need more of that one skill.

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
