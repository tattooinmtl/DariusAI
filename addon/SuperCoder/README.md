# SuperCoder — Autonomous Engineering Harness

**SuperCoder** is a unified, production-grade engineering harness and skill package designed specifically for **Visual Studio Code with official GitHub Copilot**.

It synthesizes the capabilities of the DariusAI Addon ecosystem:
- **`addon/hooks`**: Command safety guardrails, dangerous pattern filtering, and lifecycle guards.
- **`addon/skills`**:
  - **14 Superpowers**: Brainstorming, Systematic Debugging, TDD, Verification Before Completion, Writing Plans, Code Review, Worktrees, and Subagents.
  - **21 Language Skills**: Deep idiomatic guidance for Python, TypeScript, Node.js, Rust, Go, C#, C++, Java, Kotlin, Ruby, PHP, SQL, React, HTMX, HTML5/CSS, and Bash.
  - **10 Codebase Starters**: Production templates and scaffolding patterns.
  - **Design & Gamedev**: Frontend aesthetics, Three.js 3D modeling, and canvas architectures.
  - **Thinking & Docs**: Decision logs (ADRs), requirements probing, and architecture specs.
- **`addon/tools`**: Skill & hook validation and generation utilities.

---

## Directory Structure

```text
addon/SuperCoder/
├── SKILL.md                          # Master Universal Agent Skill specification
├── copilot-instructions.md           # Master custom instructions for Copilot Chat & Agent Mode
├── README.md                         # This file
├── .github/
│   ├── copilot-instructions.md       # Drop-in repo instructions for VS Code
│   └── prompts/                      # VS Code Copilot Prompt Files
│       ├── plan.prompt.md            # /plan prompt
│       ├── debug.prompt.md           # /debug prompt
│       ├── tdd.prompt.md             # /tdd prompt
│       ├── review.prompt.md          # /review prompt
│       └── scaffold.prompt.md        # /scaffold prompt
├── .vscode/
│   └── settings.json                 # Recommended VS Code settings
├── references/
│   ├── process-methodology.md        # Distilled Superpowers methodology
│   ├── polyglot-standards.md         # Multi-language engineering rubrics
│   ├── security-guardrails.md        # Dangerous patterns & safety rules
│   └── vscode-copilot-guide.md       # VS Code + GitHub Copilot setup guide
└── scripts/
    ├── guard.py                      # Standalone command safety checker
    └── validate_skill.py             # SuperCoder suite validator
```

---

## Quick Start for VS Code with GitHub Copilot

1. **Option 1: Project Drop-in**
   - Copy `.github/` and `.vscode/` into your project root.
   - VS Code with GitHub Copilot Chat will automatically detect the custom instructions and prompt files (`/plan`, `/debug`, `/tdd`, `/review`, `/scaffold`).

2. **Option 2: Global VS Code Setting**
   - Copy the contents of [`copilot-instructions.md`](copilot-instructions.md) into VS Code Settings under `github.copilot.chat.customInstructions`.

3. **Validation**:
   - Run `python addon/SuperCoder/scripts/validate_skill.py` to verify the skill suite.
