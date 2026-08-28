# VS Code & Official GitHub Copilot Integration Guide

This guide explains how to install, configure, and maximize the **SuperCoder** skill inside **Visual Studio Code with official GitHub Copilot**.

---

## 1. Prerequisites
- **Visual Studio Code** (latest version).
- **GitHub Copilot** and **GitHub Copilot Chat** extensions installed and active.
- Access to GitHub Copilot (Individual, Business, or Enterprise).

---

## 2. Quick Setup

### Method A: Workspace-Level Integration (Recommended)
Copy the `.github` and `.vscode` folders from `SuperCoder` into your project root:
```text
your-project/
├── .github/
│   ├── copilot-instructions.md       # Auto-loaded by Copilot Chat & Agent Mode
│   └── prompts/                      # Reusable prompt files
│       ├── plan.prompt.md
│       ├── debug.prompt.md
│       ├── tdd.prompt.md
│       ├── review.prompt.md
│       └── scaffold.prompt.md
└── .vscode/
    └── settings.json                 # Enables promptFiles and Copilot optimizations
```

### Method B: Global / User-Level Custom Instructions
1. Open VS Code Settings (`Ctrl+,` or `Cmd+,`).
2. Search for `github.copilot.chat.customInstructions`.
3. In the user settings, paste the contents of [`copilot-instructions.md`](../copilot-instructions.md).

---

## 3. How to Use SuperCoder in Copilot Chat

### 1. Using Custom Prompt Files
In the Copilot Chat input box, type `/` to access prompt files:
- `/plan`: Generate an architectural design and verification plan before coding.
- `/debug`: Run systematic root-cause debugging on an error or stack trace.
- `/tdd`: Drive a Red-Green-Refactor development cycle.
- `/review`: Perform a comprehensive security, performance, and code review.
- `/scaffold`: Generate a complete polyglot project boilerplate.

### 2. Context Attachment Variables
Maximize reasoning accuracy by referencing context:
- `#file:path/to/file.ts`: Attach a specific file to the prompt.
- `#folder:src/services`: Attach an entire folder.
- `#terminalSelection`: Pass recent compiler or test failure output.
- `#codebase`: Query the entire repository index.

---

## 4. SuperCoder Agent Mode Workflows

When using Copilot in Agent Mode or multi-turn execution:
1. **Plan First**: State the goal and allow SuperCoder to create a numbered implementation plan.
2. **Review TDD Cycle**: Observe the failing test first, followed by the minimal fix and verification pass.
3. **Inspect Diffs**: Review the clean, surgical edits.
4. **Sign-off**: Verify that all automated tests pass in the integrated terminal before completing the task.
