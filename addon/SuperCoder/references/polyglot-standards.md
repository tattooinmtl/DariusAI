# SuperCoder Polyglot Engineering Standards

SuperCoder provides native, production-grade engineering standards across all major programming languages and application domains.

---

## 1. Python
- **Build / Packaging**: `pyproject.toml` (standardized with `hatchling`, `flit`, or `setuptools`), virtual environments (`.venv`), `uv` or `pip`.
- **Typing**: Strict type hints (`from typing import ...`), compatible with `mypy` or `pyright`.
- **Testing**: `pytest` with fixtures, parameterized tests, and `pytest-cov`.
- **Linting & Formatting**: `ruff` for ultra-fast linting/formatting or `black` + `flake8` + `isort`.
- **Idioms**: Context managers (`with`), explicit custom exceptions, generators for large data pipelines, dataclasses / Pydantic models for structured schemas.

---

## 2. TypeScript & JavaScript (Node.js / Deno / Bun)
- **Configuration**: `tsconfig.json` with `"strict": true`, `"moduleResolution": "bundler"` or `"nodenext"`.
- **Runtime**: ESM (`"type": "module"`), Node.js >= 20.
- **Testing**: `vitest` or `jest` with mocking and code coverage.
- **Linting**: `eslint` with `@typescript-eslint` or `biome`.
- **Idioms**: Explicit return types for public functions, custom error classes extending `Error`, exhaustive switch checks (`never` type), defensive async error boundaries.

---

## 3. Rust
- **Build**: Cargo workspaces, `Cargo.toml` with explicit feature flags and dependency versions.
- **Testing**: Built-in unit tests (`#[cfg(test)] mod tests`), integration tests in `tests/`, property testing with `proptest`.
- **Quality**: `cargo clippy -- -D warnings`, `cargo fmt --check`.
- **Idioms**: Ownership and borrowing over cloning, `Result<T, E>` / `Option<T>` combinators (`map`, `and_then`), custom error enums via `thiserror`, `anyhow` for applications.

---

## 4. Go
- **Build**: Standard `go.mod` and `go.sum`, clear directory structure (`cmd/`, `internal/`, `pkg/`).
- **Testing**: Standard `go test -race ./...`, table-driven tests, benchmark tests.
- **Quality**: `golangci-lint run`, `go vet ./...`, `gofmt -s`.
- **Idioms**: Explicit error checking (`if err != nil`), context propagation (`ctx context.Context`) through all IO operations, synchronization via channels or `sync.Mutex`.

---

## 5. C# / .NET
- **SDK**: .NET 8 / 9 SDK, C# 12 / 13.
- **Project Setup**: Solution (`.sln`), Clean Architecture / Layered structure (`Domain`, `Application`, `Infrastructure`, `Presentation`).
- **Testing**: `xUnit` or `NUnit`, `FluentAssertions`, `Moq` / `NSubstitute`.
- **Quality**: `<Nullable>enable</Nullable>`, `<TreatWarningsAsErrors>true</TreatWarningsAsErrors>`.
- **Idioms**: Dependency Injection (`IServiceCollection`), async/await with `CancellationToken`, record types for immutable DTOs, LINQ with care on allocations.

---

## 6. C & C++
- **Build**: Modern `CMake` (>= 3.25) or `Meson`, C++20 / C++23 standard.
- **Testing**: `GoogleTest` or `Catch2` integrated via `CTest`.
- **Sanitizers**: AddressSanitizer (ASan), UndefinedBehaviorSanitizer (UBSan), ThreadSanitizer (TSan).
- **Idioms**: RAII for all resource lifecycles, smart pointers (`std::unique_ptr`, `std::shared_ptr`), `std::span` and `std::string_view` for non-owning views, no naked `new`/`delete`.

---

## 7. Java & Kotlin
- **Build**: Gradle (Kotlin DSL) or Maven, Java 21+ LTS.
- **Testing**: JUnit 5, Mockito / MockK, AssertJ.
- **Idioms**: Immutability (Java `record`, Kotlin `data class`), functional streams, structured concurrency, Spring Boot / Ktor best practices.

---

## 8. PHP & Ruby
- **PHP**: PHP 8.3+, Composer, PHPUnit, PHPStan level 8, Laravel / Symfony idioms, strict types (`declare(strict_types=1);`).
- **Ruby**: Ruby 3.3+, Bundler, RSpec, RuboCop, Rails / Sinatra idioms, frozen string literals (`# frozen_string_literal: true`).

---

## 9. SQL & Persistence
- **Principles**: Parameterized statements only; explicit index design for WHERE / JOIN / ORDER BY columns; transaction boundaries around multi-step state mutations.
- **Migrations**: Versioned schema migration files (`schema.sql`, Flyway, Alembic, Prisma).

---

## 10. Modern Web & Frontend
- **HTML/CSS**: Semantic HTML5, accessible ARIA attributes, responsive grid/flexbox, CSS custom properties.
- **React / JSX**: Functional components, custom hooks, memoization when measured, clean separation of UI and state.
- **HTMX**: Hypermedia-driven architectures with server-rendered fragments and progressive enhancement.
