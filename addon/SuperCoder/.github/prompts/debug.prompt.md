---
description: Perform systematic root-cause debugging without guessing or applying speculative fixes.
---

# SuperCoder Debug Prompt

You are tasked with diagnosing and fixing a bug or test failure using the SuperCoder systematic debugging protocol.

## Instructions
1. **Isolate the Failure**:
   - Inspect the error message, stack trace, or unexpected behavior.
   - Pinpoint the exact file and line where the error manifests (`path/to/file.ext:line`).

2. **Root Cause Analysis (RCA)**:
   - Trace the data flow backward from the failure point to where the invalid state originated.
   - Do NOT apply guesswork or symptom-masking patches.
   - State the verified root cause in one clear sentence.

3. **Reproduction & Regression Test**:
   - Write a minimal failing test that reproduces the bug under isolation.
   - Confirm the test reproduces the failure.

4. **Surgical Fix**:
   - Implement the smallest, cleanest fix addressing the root cause.
   - Ensure all edge cases and boundary conditions are handled.

5. **Verification**:
   - Run the full test suite to confirm the regression test passes and no existing tests break.
   - Provide the before/after diff and test execution output.
