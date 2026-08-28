---
description: Implement features or fixes using the Red-Green-Refactor test-driven cycle.
---

# SuperCoder TDD Prompt

You are tasked with implementing functionality following the strict Test-Driven Development (TDD) cycle.

## Instructions
1. **Red Phase (Failing Test)**:
   - Identify the contract, API, or behavior to be implemented.
   - Author a clean, readable unit/integration test capturing the specification.
   - Run the test to confirm that it fails for the right reason.

2. **Green Phase (Minimal Implementation)**:
   - Write the minimal, production-grade code required to satisfy the test.
   - Do not write speculative extra features outside the test scope.
   - Run the test suite and confirm green status.

3. **Refactor Phase (Clean Code)**:
   - Improve structure, reduce duplication, enhance type safety, and optimize performance.
   - Re-run all tests to guarantee 100% regression-free behavior.

4. **Output**:
   - Test code block.
   - Implementation code block.
   - Test execution verification summary.
