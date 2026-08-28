---
description: Scaffold a production-ready polyglot project structure with tooling, linting, and tests.
---

# SuperCoder Project Scaffolding Prompt

You are tasked with scaffolding a clean, robust, and idiomatic project structure.

## Instructions
1. **Determine Technology Stack**:
   - Language (Python, TypeScript, Rust, Go, C#, C++, Java, etc.).
   - Target Framework & Runtime (FastAPI, React, Next.js, Actix, Gin, ASP.NET Core, etc.).
   - Package Manager & Build Tool (`uv`/`poetry`/`pip`, `pnpm`/`npm`, `cargo`, `go`, `dotnet`, `cmake`).

2. **Generate Standard Project Structure**:
   ```text
   project-root/
   ├── src/ (or app / internal / lib)
   ├── tests/ (unit, integration, fixtures)
   ├── .gitignore (comprehensive for the stack)
   ├── Configuration files (tsconfig.json, pyproject.toml, Cargo.toml, etc.)
   └── README.md (architecture overview, setup, test, and run instructions)
   ```

3. **Configure Tooling & Quality Gates**:
   - Test framework configuration (`pytest.ini`, `vitest.config.ts`, etc.).
   - Linter and formatter settings (`ruff.toml`, `.eslintrc`, `.prettierrc`, `rustfmt.toml`).
   - GitHub Actions CI workflow template (`.github/workflows/ci.yml`).

4. **Verify Scaffold**:
   - Provide the initial CLI commands to install dependencies, run the test suite, and start the development server.
