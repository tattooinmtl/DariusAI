---
description: Create a rigorous architectural plan with risk analysis and verification steps before implementing.
---

# SuperCoder Plan Prompt

You are tasked with planning a software engineering change using the SuperCoder methodology.

## Instructions
1. **Analyze Context & Requirements**:
   - Inspect the current workspace files, architecture, dependencies, and constraints.
   - Clarify the goal and any edge cases or ambiguities.

2. **Formulate Concrete Plan**:
   - Structure the plan with numbered steps. Format each step as: `[action_type] Verb + Object + Success Criterion`.
   - Action types: `[read]`, `[test]`, `[edit]`, `[run]`, `[verify]`.
   - Identify:
     - Target files affected
     - Dependencies required
     - Potential risks and failure modes
     - Rollback / recovery approach

3. **Validation Strategy**:
   - Specify the exact automated commands to verify the change (`pytest`, `npm test`, `cargo test`, `dotnet test`, etc.).
   - Define expected green signals.

4. **Output Format**:
   - **Objective**: 1-2 sentence summary.
   - **Current State vs Target State**.
   - **Step-by-Step Plan**.
   - **Verification Strategy**.
