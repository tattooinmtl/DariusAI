import os
import json
import re

# Qwen/Python Agent Skill: PiReagents Snippet Library
# Usage: from pi_reagents_skill import PiReagentsLibrary

BASE = r"C:/Test/PiReagents_Snippets"
CONTROLLER = os.path.join(BASE, "pi_reagents_snippets.json")


class PiReagentsLibrary:
    def __init__(self, ctrl_path=CONTROLLER):
        with open(ctrl_path, encoding="utf-8") as f:
            self.data = json.load(f)
        self.snippets = self.data["snippets"]
        self.base_folder = self.data["meta"]["base_folder"]

    def search(self, query):
        q = query.lower()
        return [
            s
            for s in self.snippets
            if q in s["name"].lower()
            or q in s["category"].lower()
            or q in s["description"].lower()
        ]

    def get_snippet_code(self, snippet):
        path = os.path.join(self.base_folder, snippet["file"])
        with open(path, encoding="utf-8") as f:
            return f.read()


# Optionally, script to extract ALL snippets from the MD file to JSON
# (Uncomment below for one-time export)
def md_to_json(md_path, json_path):
    snippets = []
    current_category = None
    with open(md_path, encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        cat = re.match(r"^##+\s*(.+)", line)
        if cat:
            current_category = cat.group(1).strip()
        if line.startswith("### "):
            name = line[4:].strip()
            description = lines[i + 1].strip("_\n ")
            typeline = lines[i + 2]
            m = re.search(
                r"\*\*Type:\*\* `?([a-zA-Z_]+)`? \| \*\*File:\*\* `?([^\s`]+)`?",
                typeline,
            )
            if m:
                snip_type, snip_file = m.groups()
                snippets.append(
                    {
                        "category": current_category,
                        "name": name,
                        "description": description,
                        "type": snip_type,
                        "file": snip_file,
                    }
                )
    output = {
        "meta": {
            "library": "PiReAgents Snippet Library",
            "version": 1,
            "source": "md export",
            "base_folder": BASE,
        },
        "snippets": snippets,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(snippets)} snippets to JSON.")


# Example for one-time extraction:
# md_to_json(
#     "C:/Test/PiReagents_Snippets.md",
#     "C:/Test/PiReagents_Snippets/pi_reagents_snippets.json"
# )
