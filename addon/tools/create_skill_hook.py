#!/usr/bin/env python3
"""
Skill & Hook Creation & Validation Tool
Used by Agents and Harnesses (.omni, DariusAI-Harness, Thoth, Antigravity, Claude, Codex, Cursor)
to generate, validate, and manage agent skills and lifecycle hooks.
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional

DEFAULT_ADDON_ROOT = Path(r"C:\.dariusai-harness\addon")
DEFAULT_SKILLS_DIR = DEFAULT_ADDON_ROOT / "skills"
DEFAULT_HOOKS_DIR = DEFAULT_ADDON_ROOT / "hooks"
DEFAULT_HOOKS_JSON = DEFAULT_HOOKS_DIR / "hooks.json"

SKILL_TEMPLATE = """---
name: {name}
description: >-
  {description}
---

# {title} Skill

{overview}

## 1. Stack Overview & Dependencies
- **Core Technology**: {name}
- **Recommended Package Manager / Tools**: 
- **Key Dependencies**:

## 2. Project Structure Standard
```text
project-root/
├── src/
├── tests/
└── README.md
```

## 3. How-To Workflows
### Build & Compile
```bash
# Build command
```

### Run & Dev Mode
```bash
# Dev run command
```

### Test & Verification
```bash
# Verification command
```

## 4. Best Practices & Design Patterns
- 

## 5. Tips, Tricks & Common Pitfalls
- 

## 6. Harness Hooks & Safety Enforcement
- **PreToolUse Guard**: 
- **PostToolUse Verification**: 
"""

HOOK_PYTHON_TEMPLATE = '''#!/usr/bin/env python3
"""
Lifecycle Hook Handler: {name}
Supports Multi-Harness JSON payloads (Antigravity, DariusAI-Harness, .omni, Thoth, Claude, Codex, Cursor)
Event: {event}
"""

import sys
import json
import re

def process_hook():
    raw_input = sys.stdin.read()
    if not raw_input.strip():
        # Default allow/ok response
        print(json.dumps({{}}))
        sys.exit(0)

    try:
        payload = json.loads(raw_input)
    except Exception as e:
        print(json.dumps({{"error": f"Failed to parse hook stdin JSON: {{e}}"}}))
        sys.exit(0)

    # Extract tool call / context info
    tool_call = payload.get("toolCall", {{}})
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args", {{}})

    # Custom Hook Logic Here:
    # decision: "allow" | "deny" | "ask" | "force_ask"
    
    response = {{
        "decision": "allow",
        "reason": "Passed safety checks."
    }}

    print(json.dumps(response))

if __name__ == "__main__":
    process_hook()
'''

def create_skill(name: str, category: str, description: str, target_dir: Path, with_examples: bool, with_references: bool, with_scripts: bool) -> Path:
    name_clean = name.strip().lower().replace(" ", "-")
    category_clean = category.strip().lower().replace(" ", "-")
    
    skill_dir = target_dir / category_clean / name_clean
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        title = name_clean.replace("-", " ").title()
        overview = f"Production operational guidance, codebases, and harness integration for {title}."
        content = SKILL_TEMPLATE.format(
            name=name_clean,
            description=description,
            title=title,
            overview=overview
        )
        skill_file.write_text(content, encoding="utf-8")
        print(f"[+] Created skill file: {skill_file}")

    if with_examples:
        (skill_dir / "examples").mkdir(exist_ok=True)
        readme_ex = skill_dir / "examples" / "README.md"
        if not readme_ex.exists():
            readme_ex.write_text(f"# {name_clean} Examples\n\nStarter codebase templates and code snippets.", encoding="utf-8")
        print(f"[+] Created examples directory: {skill_dir / 'examples'}")

    if with_references:
        (skill_dir / "references").mkdir(exist_ok=True)
        readme_ref = skill_dir / "references" / "best_practices.md"
        if not readme_ref.exists():
            readme_ref.write_text(f"# {name_clean} Reference & Best Practices\n\nDeep-dive documentation and patterns.", encoding="utf-8")
        print(f"[+] Created references directory: {skill_dir / 'references'}")

    if with_scripts:
        (skill_dir / "scripts").mkdir(exist_ok=True)
        readme_scr = skill_dir / "scripts" / "README.md"
        if not readme_scr.exists():
            readme_scr.write_text(f"# {name_clean} Scripts\n\nAutomated workflow helper scripts.", encoding="utf-8")
        print(f"[+] Created scripts directory: {skill_dir / 'scripts'}")

    return skill_dir

def create_hook(name: str, event: str, matcher: str, command: str, timeout: int, hooks_json_path: Path, create_script: bool) -> None:
    hooks_data = {}
    if hooks_json_path.exists():
        try:
            hooks_data = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        except Exception:
            hooks_data = {}

    if name not in hooks_data:
        hooks_data[name] = {"enabled": True}

    hook_entry = {
        "type": "command",
        "command": command,
        "timeout": timeout
    }

    if event in ["PreToolUse", "PostToolUse"]:
        if event not in hooks_data[name]:
            hooks_data[name][event] = []
        
        # Check if matcher exists
        matched_group = None
        for group in hooks_data[name][event]:
            if group.get("matcher") == matcher:
                matched_group = group
                break
        
        if matched_group:
            matched_group["hooks"].append(hook_entry)
        else:
            hooks_data[name][event].append({
                "matcher": matcher,
                "hooks": [hook_entry]
            })
    else:
        if event not in hooks_data[name]:
            hooks_data[name][event] = []
        hooks_data[name][event].append(hook_entry)

    hooks_json_path.parent.mkdir(parents=True, exist_ok=True)
    hooks_json_path.write_text(json.dumps(hooks_data, indent=2), encoding="utf-8")
    print(f"[+] Updated hooks config: {hooks_json_path}")

    if create_script and ("python" in command or command.endswith(".py")):
        # Extract script path from command
        parts = command.split()
        script_path_str = parts[-1] if len(parts) > 1 else parts[0]
        script_path = Path(script_path_str)
        if not script_path.is_absolute():
            script_path = hooks_json_path.parent / script_path
        
        if not script_path.exists():
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_content = HOOK_PYTHON_TEMPLATE.format(name=name, event=event)
            script_path.write_text(script_content, encoding="utf-8")
            print(f"[+] Created hook script handler: {script_path}")

def validate_repository(addon_root: Path) -> bool:
    skills_dir = addon_root / "skills"
    hooks_dir = addon_root / "hooks"
    errors = []
    warnings = []

    print(f"[*] Validating skills at: {skills_dir}")
    if skills_dir.exists():
        for skill_md in skills_dir.rglob("SKILL.md"):
            content = skill_md.read_text(encoding="utf-8")
            if not content.startswith("---"):
                errors.append(f"Skill missing frontmatter start line: {skill_md}")
                continue
            fm_match = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
            if not fm_match:
                errors.append(f"Invalid YAML frontmatter: {skill_md}")
                continue
            fm_text = fm_match.group(1)
            if "name:" not in fm_text:
                errors.append(f"Missing 'name:' in frontmatter: {skill_md}")
            if "description:" not in fm_text:
                errors.append(f"Missing 'description:' in frontmatter: {skill_md}")

    hooks_json = hooks_dir / "hooks.json"
    print(f"[*] Validating hooks at: {hooks_dir}")
    if hooks_json.exists():
        try:
            hdata = json.loads(hooks_json.read_text(encoding="utf-8"))
            for hname, hspec in hdata.items():
                if not isinstance(hspec, dict):
                    errors.append(f"Hook spec '{hname}' must be a dictionary in hooks.json")
        except Exception as e:
            errors.append(f"Malformed hooks.json: {e}")

    print("\n=== Validation Results ===")
    if errors:
        print(f"[!] ERRORS ({len(errors)}):")
        for err in errors:
            print(f"  - {err}")
    else:
        print("[OK] No errors found.")

    if warnings:
        print(f"[!] WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")

    return len(errors) == 0

def list_skills_and_hooks(addon_root: Path) -> None:
    skills_dir = addon_root / "skills"
    hooks_dir = addon_root / "hooks"

    print("\n=== Registered Skills ===")
    if skills_dir.exists():
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            rel_path = skill_md.relative_to(skills_dir)
            content = skill_md.read_text(encoding="utf-8")
            name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            name = name_match.group(1).strip() if name_match else str(rel_path.parent)
            desc = desc_match.group(1).strip() if desc_match else ""
            print(f"  - {name} ({rel_path.parent})\n    Summary: {desc[:80]}...")

    print("\n=== Registered Hooks ===")
    hooks_json = hooks_dir / "hooks.json"
    if hooks_json.exists():
        try:
            hdata = json.loads(hooks_json.read_text(encoding="utf-8"))
            for hname, hspec in hdata.items():
                status = "enabled" if hspec.get("enabled", True) else "disabled"
                print(f"  - Hook: {hname} [{status}]")
                for event in ["PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"]:
                    if event in hspec:
                        print(f"    Event: {event}")
        except Exception as e:
            print(f"  Error reading hooks.json: {e}")

def main():
    parser = argparse.ArgumentParser(description="Skill & Hook Creation Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create-skill
    p_skill = subparsers.add_parser("create-skill", help="Scaffold a new skill")
    p_skill.add_argument("--name", required=True, help="Skill name (e.g. python, rust)")
    p_skill.add_argument("--category", default="codebases", help="Category folder (e.g. codebases, agent-orchestration)")
    p_skill.add_argument("--description", required=True, help="Skill description for frontmatter")
    p_skill.add_argument("--target-dir", default=str(DEFAULT_SKILLS_DIR), help="Root skills directory")
    p_skill.add_argument("--with-examples", action="store_true", default=True, help="Create examples directory")
    p_skill.add_argument("--with-references", action="store_true", default=True, help="Create references directory")
    p_skill.add_argument("--with-scripts", action="store_true", default=True, help="Create scripts directory")

    # create-hook
    p_hook = subparsers.add_parser("create-hook", help="Scaffold/Register a hook")
    p_hook.add_argument("--name", required=True, help="Hook group name")
    p_hook.add_argument("--event", required=True, choices=["PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"])
    p_hook.add_argument("--matcher", default="*", help="Tool matcher regex (for PreToolUse/PostToolUse)")
    p_hook.add_argument("--command", required=True, help="Command to execute")
    p_hook.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    p_hook.add_argument("--target-hooks-file", default=str(DEFAULT_HOOKS_JSON), help="Path to hooks.json")
    p_hook.add_argument("--create-script", action="store_true", default=True, help="Auto-create python script handler if command uses python")

    # validate
    p_val = subparsers.add_parser("validate", help="Validate skills and hooks")
    p_val.add_argument("--addon-root", default=str(DEFAULT_ADDON_ROOT), help="Path to addon root")

    # list
    p_list = subparsers.add_parser("list", help="List all skills and hooks")
    p_list.add_argument("--addon-root", default=str(DEFAULT_ADDON_ROOT), help="Path to addon root")

    args = parser.parse_args()

    if args.command == "create-skill":
        create_skill(
            name=args.name,
            category=args.category,
            description=args.description,
            target_dir=Path(args.target_dir),
            with_examples=args.with_examples,
            with_references=args.with_references,
            with_scripts=args.with_scripts
        )
    elif args.command == "create-hook":
        create_hook(
            name=args.name,
            event=args.event,
            matcher=args.matcher,
            command=args.command,
            timeout=args.timeout,
            hooks_json_path=Path(args.target_hooks_file),
            create_script=args.create_script
        )
    elif args.command == "validate":
        ok = validate_repository(Path(args.addon_root))
        sys.exit(0 if ok else 1)
    elif args.command == "list":
        list_skills_and_hooks(Path(args.addon_root))

if __name__ == "__main__":
    main()
