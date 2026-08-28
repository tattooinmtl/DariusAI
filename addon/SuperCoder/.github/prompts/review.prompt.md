---
description: Perform an exhaustive code quality, security, and architectural review.
---

# SuperCoder Review Prompt

You are tasked with conducting a rigorous code review using SuperCoder's engineering and security standards.

## Instructions
1. **Correctness & Robustness**:
   - Verify error handling (no swallowed exceptions, proper timeouts, retries with backoff).
   - Check boundary conditions, null/undefined safety, and off-by-one errors.
   - Ensure concurrency safety (race conditions, locks, deadlocks, atomic operations).

2. **Security Audit**:
   - Check for injection vulnerabilities (SQL, Command, Path traversal, XSS, LDAP).
   - Ensure no hardcoded secrets, keys, or private tokens exist.
   - Verify permission boundaries and safe deserialization.

3. **Performance & Resource Management**:
   - Inspect memory consumption, open file handles, database connection pooling, and unclosed streams.
   - Check query efficiency (missing indices, N+1 query patterns).

4. **Maintainability & Idioms**:
   - Confirm adherence to language-specific best practices and naming conventions.
   - Check test coverage and clarity of test assertions.

5. **Output**:
   - **Summary**: High-level verdict (`Approved`, `Approved with minor suggestions`, or `Changes requested`).
   - **Critical Issues**: Security / correctness bugs (file, line, issue, fix).
   - **Recommendations**: Code cleanups, performance improvements, or idiomatic enhancements.
