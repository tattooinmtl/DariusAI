# SuperCoder Process Methodology (Superpowers Distilled)

This reference outlines the disciplined engineering lifecycle adopted by SuperCoder, synthesized from the 14 Superpowers methodology skills.

---

## 1. Brainstorming & Requirements Probing
- **When**: Before starting any creative work, building new components, or introducing major architectural modifications.
- **Rules**:
  - Clarify ambiguous user requirements upfront.
  - Propose 2–3 viable design alternatives with explicit pros/cons.
  - Choose the simplest architecture that completely fulfills the requirement.

---

## 2. Architectural Planning
- **When**: For any task requiring multiple steps or touching multiple files.
- **Rules**:
  - Format each step as: `[action_type] Verb + Object + Success Criterion`.
  - Classify actions: `[read]`, `[test]`, `[edit]`, `[run]`, `[verify]`.
  - Identify target files, dependencies, potential failure modes, and rollback strategies.

---

## 3. Test-Driven Development (TDD)
- **When**: When adding features or fixing bugs.
- **Rules**:
  - **Red**: Author a failing automated test capturing the expected specification or reproducing the defect.
  - **Green**: Write the minimal code to satisfy the test.
  - **Refactor**: Clean up and optimize while ensuring all tests stay green.
  - *Violating the letter of TDD is violating the spirit of TDD.*

---

## 4. Systematic Root-Cause Debugging
- **When**: When encountering bugs, test failures, or crashes.
- **Rules**:
  - Do NOT guess or make speculative code edits.
  - Trace the error backward from stack trace to original state corruption.
  - Formulate a testable hypothesis.
  - Isolate with a minimal reproducible test case.
  - Apply the surgical fix and verify with regression tests.

---

## 5. Verification Before Completion
- **When**: Before claiming any task is done.
- **Rules**:
  - Never say "it works" without running the build/test commands.
  - Capture real terminal output (compiler status, test runner results).
  - Verify static type checkers (`mypy`, `tsc`, `cargo check`) and linters report 0 errors.

---

## 6. Code Review & Quality Gates
- **When**: Prior to finalizing implementation.
- **Rules**:
  - Review diffs for:
    - Null/undefined safety and edge cases.
    - Error handling (no silent swallows).
    - Security vulnerabilities (SQL injection, XSS, command injection, secret leakage).
    - Performance bottlenecks (N+1 queries, memory leaks, unclosed handles).
