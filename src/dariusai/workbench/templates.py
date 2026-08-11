"""Project types the workbench can create, and what each one actually needs.

A template is three things: the files to write, the runtime it needs (so the
form can refuse to offer something this machine can't build), and the setup
steps to run afterwards. Steps are real commands — `python -m venv`, `npm
install`, `go mod init` — streamed to the console in the form so the user
watches the environment being built rather than staring at a spinner.

`keywords` drives the type picker's typeahead. Typing "h" should reach HTML,
HTML5 and HTMX; typing "py" should reach Python, Flask and FastAPI. The
match is prefix-based over the label and every keyword, so "htm" narrows to
the three HTML-family entries rather than jumping to whichever one happens
to sort first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SQLITE_SCHEMA = """\
-- Starter schema. sqlite3 ships inside Python, Node (better-sqlite3), PHP
-- (pdo_sqlite) and Go, so a file database needs no server to be installed.
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_GITIGNORE = """\
.dariusai-scratch/
__pycache__/
.venv/
node_modules/
*.db
dist/
build/
"""


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    runtime: str | None            # key into agent.runtimes; None = nothing to install
    keywords: tuple[str, ...] = ()
    files: dict[str, str] = field(default_factory=dict)
    steps: tuple[tuple[str, str], ...] = ()   # (human label, shell command)
    needs_env: bool = False        # a per-project interpreter environment
    sqlite: bool = False
    description: str = ""


def _html(title: str, body: str, head_extra: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{head_extra}
<link rel="stylesheet" href="styles.css">
</head>
<body>
{body}
</body>
</html>
"""


_CSS = """\
:root { color-scheme: light dark; --fg: #1b1e2b; --bg: #ffffff; --accent: #3b5bdb; }
@media (prefers-color-scheme: dark) { :root { --fg: #e6e9f5; --bg: #0d0f20; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem; font-family: system-ui, "Segoe UI", sans-serif;
       color: var(--fg); background: var(--bg); line-height: 1.6; }
main { max-width: 44rem; margin: 0 auto; }
h1 { color: var(--accent); }
"""

TEMPLATES: tuple[Template, ...] = (
    # ---- HTML family (the typeahead case: h -> html, html5, htmx) ----------
    Template(
        id="html", label="HTML", runtime=None, keywords=("html", "web", "page", "site"),
        description="A plain HTML page with a stylesheet.",
        files={
            "index.html": _html("HTML Project", "<main>\n  <h1>Hello</h1>\n  <p>Edit index.html to begin.</p>\n</main>"),
            "styles.css": _CSS,
        },
    ),
    Template(
        id="html5", label="HTML5 (semantic starter)", runtime=None,
        keywords=("html5", "html", "semantic", "web"),
        description="Semantic HTML5 skeleton — header, main, footer, skip link.",
        files={
            "index.html": _html(
                "HTML5 Project",
                "<a class=\"skip\" href=\"#main\">Skip to content</a>\n"
                "<header><h1>HTML5 Project</h1></header>\n"
                "<main id=\"main\">\n  <section>\n    <h2>Section</h2>\n    <p>Content.</p>\n  </section>\n</main>\n"
                "<footer><small>Built with DariusAI</small></footer>",
            ),
            "styles.css": _CSS + "\n.skip { position:absolute; left:-999px; }\n.skip:focus { left:1rem; }\n",
        },
    ),
    Template(
        id="htmx", label="HTMX", runtime=None, keywords=("htmx", "html", "hypermedia"),
        description="HTMX page with a vendored script and a demo endpoint stub.",
        files={
            "index.html": _html(
                "HTMX Project",
                "<main>\n  <h1>HTMX</h1>\n"
                "  <button hx-get=\"fragment.html\" hx-target=\"#out\" hx-swap=\"innerHTML\">Load</button>\n"
                "  <div id=\"out\"></div>\n</main>",
                head_extra="\n<script src=\"https://unpkg.com/htmx.org@2\" defer></script>",
            ),
            "fragment.html": "<p>Swapped in by HTMX.</p>\n",
            "styles.css": _CSS,
        },
    ),

    # ---- JS / Node --------------------------------------------------------
    Template(
        id="javascript", label="JavaScript (vanilla)", runtime="javascript",
        keywords=("javascript", "js", "vanilla", "node"),
        description="A single JS entry point runnable with node.",
        files={"main.js": "console.log('hello from JavaScript');\n"},
        steps=(("node version", "node --version"),),
    ),
    Template(
        id="nodejs", label="Node.js (npm package)", runtime="javascript",
        keywords=("node", "nodejs", "npm", "javascript", "js"),
        description="npm-initialised package with a start script and SQLite support.",
        files={
            "index.js": (
                "import { DatabaseSync } from 'node:sqlite';\n\n"
                "// node:sqlite is built in from Node 22 — no native module to compile.\n"
                "const db = new DatabaseSync('data/app.db');\n"
                "db.exec(\"CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)\");\n"
                "console.log('database ready at data/app.db');\n"
            ),
        },
        steps=(
            ("node version", "node --version"),
            ("npm version", "npm --version"),
            ("initialise package", "npm init -y"),
            ("enable ES modules", 'npm pkg set type=module main=index.js scripts.start="node index.js"'),
        ),
        sqlite=True,
    ),
    Template(
        id="typescript", label="TypeScript", runtime="typescript",
        keywords=("typescript", "ts", "node"),
        description="TypeScript entry point; Node 24+ runs .ts directly.",
        files={
            "main.ts": "const greeting: string = 'hello from TypeScript';\nconsole.log(greeting);\n",
            "tsconfig.json": '{\n  "compilerOptions": {\n    "target": "ES2022",\n    "module": "ESNext",\n'
                             '    "moduleResolution": "bundler",\n    "strict": true,\n    "noEmit": true\n  }\n}\n',
        },
        steps=(("node version", "node --version"), ("initialise package", "npm init -y")),
    ),

    # ---- Python -----------------------------------------------------------
    Template(
        id="python", label="Python (venv)", runtime="python",
        keywords=("python", "py", "script"),
        description="Python project with its own virtual environment and SQLite.",
        files={
            "main.py": (
                "import sqlite3\nfrom pathlib import Path\n\n"
                "DB = Path(__file__).parent / 'data' / 'app.db'\n\n\n"
                "def main() -> None:\n"
                "    con = sqlite3.connect(DB)\n"
                "    con.execute('CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)')\n"
                "    con.commit()\n"
                "    print(f'database ready at {DB}')\n\n\n"
                "if __name__ == '__main__':\n    main()\n"
            ),
            "requirements.txt": "",
        },
        steps=(
            ("python version", "python --version"),
            ("create virtual environment", "python -m venv .venv"),
            ("upgrade pip", r".venv\Scripts\python.exe -m pip install --upgrade pip"),
        ),
        needs_env=True, sqlite=True,
    ),
    Template(
        id="flask", label="Python + Flask", runtime="python",
        keywords=("flask", "python", "py", "web", "api"),
        description="Flask app with a venv, installed dependencies and SQLite.",
        files={
            "app.py": (
                "import sqlite3\nfrom flask import Flask, jsonify\n\n"
                "app = Flask(__name__)\n\n\n"
                "@app.get('/')\ndef index():\n    return jsonify(status='ok')\n\n\n"
                "if __name__ == '__main__':\n    app.run(debug=True)\n"
            ),
            "requirements.txt": "flask\n",
        },
        steps=(
            ("python version", "python --version"),
            ("create virtual environment", "python -m venv .venv"),
            ("install dependencies", r".venv\Scripts\python.exe -m pip install -r requirements.txt"),
        ),
        needs_env=True, sqlite=True,
    ),
    Template(
        id="fastapi", label="Python + FastAPI", runtime="python",
        keywords=("fastapi", "python", "py", "api", "web"),
        description="FastAPI app with uvicorn, a venv and SQLite.",
        files={
            "main.py": (
                "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
                "@app.get('/')\ndef read_root():\n    return {'status': 'ok'}\n"
            ),
            "requirements.txt": "fastapi\nuvicorn[standard]\n",
        },
        steps=(
            ("python version", "python --version"),
            ("create virtual environment", "python -m venv .venv"),
            ("install dependencies", r".venv\Scripts\python.exe -m pip install -r requirements.txt"),
        ),
        needs_env=True, sqlite=True,
    ),

    # ---- others -----------------------------------------------------------
    Template(
        id="php", label="PHP", runtime="php", keywords=("php", "web", "backend"),
        description="PHP entry point with a PDO SQLite connection.",
        files={
            "index.php": (
                "<?php\n$db = new PDO('sqlite:' . __DIR__ . '/data/app.db');\n"
                "$db->exec('CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)');\n"
                "echo \"database ready\\n\";\n"
            ),
        },
        steps=(("php version", "php --version"), ("check pdo_sqlite", "php -m")),
        sqlite=True,
    ),
    Template(
        id="go", label="Go (module)", runtime="go", keywords=("go", "golang"),
        description="Go module with a main package.",
        files={"main.go": 'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("hello from Go")\n}\n'},
        steps=(("go version", "go version"), ("initialise module", "go mod init project")),
    ),
    Template(
        id="ruby", label="Ruby", runtime="ruby", keywords=("ruby", "rb"),
        description="Ruby script with SQLite via the stdlib-adjacent gem.",
        files={"main.rb": "puts 'hello from Ruby'\n"},
        steps=(("ruby version", "ruby --version"),),
    ),
    Template(
        id="csharp", label="C# (.NET console)", runtime="csharp", keywords=("csharp", "cs", "dotnet", "c#"),
        description="dotnet console project.",
        files={},
        steps=(("dotnet version", "dotnet --version"), ("create console app", "dotnet new console --force")),
    ),
    Template(
        id="java", label="Java", runtime="java", keywords=("java", "jvm"),
        description="Single-file Java program (runs without compiling).",
        files={"Main.java": "public class Main {\n    public static void main(String[] args) {\n"
                            "        System.out.println(\"hello from Java\");\n    }\n}\n"},
        steps=(("java version", "java -version"),),
    ),
    Template(
        id="sqlite", label="SQLite database", runtime="python", keywords=("sqlite", "sqlite3", "sql", "database", "db"),
        description="A database project — schema file plus an initialised .db.",
        files={},
        sqlite=True,
    ),
    Template(
        id="bash", label="Bash script", runtime="bash", keywords=("bash", "sh", "shell", "script"),
        description="Executable shell script.",
        files={"main.sh": "#!/usr/bin/env bash\nset -euo pipefail\necho \"hello from bash\"\n"},
        steps=(("bash version", "bash --version"),),
    ),
    Template(
        id="static", label="Static site (HTML + CSS + JS)", runtime=None,
        keywords=("static", "site", "web", "html", "frontend"),
        description="Three-file static site, no build step.",
        files={
            "index.html": _html("Static Site", "<main>\n  <h1>Static Site</h1>\n</main>",
                                head_extra="\n<script src=\"main.js\" defer></script>"),
            "styles.css": _CSS,
            "main.js": "console.log('ready');\n",
        },
    ),
)

BY_ID = {t.id: t for t in TEMPLATES}


def common_files(name: str, template: Template) -> dict[str, str]:
    """Files every project gets regardless of type."""
    files = {
        "README.md": f"# {name}\n\n{template.description}\n\nCreated by DariusAI.\n",
        ".gitignore": _GITIGNORE,
    }
    if template.sqlite:
        files["schema.sql"] = SQLITE_SCHEMA
    return files


def search(query: str) -> list[Template]:
    """Prefix match over label and keywords — 'h' returns the HTML family,
    'py' returns Python/Flask/FastAPI. Exact-prefix hits sort first so the
    obvious answer is the one already selected."""
    q = (query or "").strip().lower()
    if not q:
        return list(TEMPLATES)

    scored: list[tuple[int, Template]] = []
    for template in TEMPLATES:
        haystack = [template.id.lower(), template.label.lower(), *(k.lower() for k in template.keywords)]
        if any(h.startswith(q) for h in haystack):
            scored.append((0, template))
        elif any(q in h for h in haystack):
            scored.append((1, template))
    scored.sort(key=lambda pair: (pair[0], pair[1].label.lower()))
    return [t for _, t in scored]
