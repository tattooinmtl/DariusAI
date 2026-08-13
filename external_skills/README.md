# External skills folder

Drop skills from other agents into this folder and they will be imported
into the brain on the next import run.

## Folder layout

Two layouts are supported. The agent folder is treated as a "group" so the
import puts all skills from that agent under one branch in the brain.

**Grouped by source agent (recommended when mixing agents):**

```
external_skills/
├── claude-code/
│   ├── react-best-practices/SKILL.md
│   └── hooks-101/SKILL.md
├── cursor/
│   └── agent-mode/SKILL.md
└── aider/
    └── repo-mapping/SKILL.md
```

**Flat (skills at the top level):**

```
external_skills/
├── react-best-practices/SKILL.md
├── hooks-101/SKILL.md
└── agent-mode/SKILL.md
```

## SKILL.md format

YAML frontmatter + markdown body. The frontmatter fields are optional but
the parser picks up everything it finds:

```markdown
---
name: react-best-practices
description: Patterns for writing React components that survive a code review.
tags: [react, frontend, hooks]
---

# React Best Practices

Use [these patterns] when writing React components. The body of this file
is the "solution" text the agent reads when it loads the skill.

## Code Examples

\`\`\`tsx
function Counter() {
  const [count, setCount] = useState(0);
  return <button onClick={() => setCount(c => c + 1)}>{count}</button>;
}
\`\`\`

## Edge Cases

- Strict mode double-mounts effects in dev — guard with refs.
- ...
```

Available frontmatter fields:

| field | used as |
|---|---|
| `name` | skill id (defaults to folder name) |
| `description` | the "problem" the skill solves |
| `tags` | extra tags |
| `category` | overrides the category (defaults to `external`) |
| `command` | slash-command shortcut |

The body is stored verbatim as the skill's `solution`. Long bodies are fine
— the brain uses deferred loading, so the body only enters the agent's
context when the skill is actually used.

## How to import

From the CLI:

```bash
dariusai import-external
```

From the running app:

- Settings → Import external skills
- Or: `POST /api/import-external`

Re-running is safe. Skills are addressed by id, so a re-import overwrites
its own existing rows instead of duplicating them.
