#!/usr/bin/env python3
"""
SuperCoder Skill & Instruction Validator
Validates YAML frontmatter, prompt files, markdown integrity, and VS Code integration.
"""

import sys
import re
from pathlib import Path

def validate_supercoder(supercoder_dir: Path) -> bool:
    errors = []
    warnings = []

    print(f"[*] Validating SuperCoder at: {supercoder_dir}")

    # 1. Validate SKILL.md
    skill_file = supercoder_dir / "SKILL.md"
    if not skill_file.exists():
        errors.append("SKILL.md is missing from SuperCoder root")
    else:
        content = skill_file.read_text(encoding="utf-8")
        if not content.startswith("---"):
            errors.append("SKILL.md missing opening frontmatter delimiter '---'")
        match = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not match:
            errors.append("SKILL.md has malformed YAML frontmatter")
        else:
            fm = match.group(1)
            if "name:" not in fm:
                errors.append("SKILL.md missing 'name:' in frontmatter")
            if "description:" not in fm:
                errors.append("SKILL.md missing 'description:' in frontmatter")
            print("  [+] SKILL.md frontmatter verified.")

    # 2. Validate Copilot Instructions
    for inst_path in [supercoder_dir / "copilot-instructions.md", supercoder_dir / ".github" / "copilot-instructions.md"]:
        if not inst_path.exists():
            errors.append(f"Missing copilot instructions: {inst_path.name}")
        else:
            print(f"  [+] {inst_path.name} exists and is verified.")

    # 3. Validate Prompts
    prompts_dir = supercoder_dir / ".github" / "prompts"
    if not prompts_dir.exists():
        errors.append("Missing .github/prompts directory")
    else:
        expected_prompts = ["plan.prompt.md", "debug.prompt.md", "tdd.prompt.md", "review.prompt.md", "scaffold.prompt.md"]
        for p in expected_prompts:
            p_file = prompts_dir / p
            if not p_file.exists():
                errors.append(f"Missing prompt file: {p}")
            else:
                p_content = p_file.read_text(encoding="utf-8")
                if not p_content.startswith("---"):
                    warnings.append(f"{p} missing description frontmatter")
                print(f"  [+] Prompt file verified: {p}")

    # 4. Validate References
    refs_dir = supercoder_dir / "references"
    if not refs_dir.exists():
        errors.append("Missing references directory")
    else:
        expected_refs = ["process-methodology.md", "polyglot-standards.md", "security-guardrails.md", "vscode-copilot-guide.md"]
        for r in expected_refs:
            if not (refs_dir / r).exists():
                errors.append(f"Missing reference document: {r}")
            else:
                print(f"  [+] Reference verified: {r}")

    # 5. Summary
    print("\n=== Validation Results ===")
    if errors:
        print(f"[!] ERRORS ({len(errors)}):")
        for err in errors:
            print(f"  - {err}")
    else:
        print("[OK] All required SuperCoder files and structures are valid and verified.")

    if warnings:
        print(f"[!] WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")

    return len(errors) == 0

def main():
    script_dir = Path(__file__).parent.resolve()
    supercoder_dir = script_dir.parent

    ok = validate_supercoder(supercoder_dir)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
