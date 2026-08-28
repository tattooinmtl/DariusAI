"""The operating doctrine every agent in this project runs under.

Two parts, spliced into the Planner, Coder and Chat prompts so the
workflow can't hold in one entry point and quietly not in another —
which is what happens when each prompt is written on its own.

Part 1 — SUPERPOWERS BOOTSTRAP. The imperative core of the
`using-superpowers` skill: invoke the relevant skill before acting,
process skills before implementation skills, a TodoList per multi-step
task, verification before "done".

Part 2 — DARIUSAI RULES. The harness-specific rules on top of
superpowers: the architecture (you live inside it, you don't rebuild
it), the sandbox (commands run in the project dir, no destructives
without approval), the knowledge loop (retrieve before you load, the
brain before the web), and the OKF anchor.

Both parts are short on purpose — and as of 2026-08-19, considerably
shorter. This text is paid for on every model call, and a tool-calling
turn makes up to sixty of them, so it earns its place by stating rules
that change behaviour and nothing else. What was cut and why:

* The twelve-row "Red Flags" table (~1,000 chars) restated one idea —
  don't rationalise your way out of the skill check — twelve times.
  It is now one sentence naming the three most common rationalisations.
* The Platform Adaptation library census (~2,400 chars) listed group
  names and skill counts. The counts drifted the moment skills were
  added (it said 92; the library holds 185), and a count is not
  something the model can act on: `browse_brain()` answers the same
  question from the live tree.
* The verbatim `using-superpowers` body is on disk at
  `addon/skills/superpowers/using-superpowers/SKILL.md` and is
  retrievable via `invoke_skill('using-superpowers')`, so carrying a
  second copy in every prompt paid twice for one text.

Nothing that changes behaviour was removed: the 1% rule, the
before-any-action ordering, brainstorming-before-building,
systematic-debugging-before-fixing, process-before-implementation, the
set_todos contract with its verification step, follow-skills-verbatim,
and user-instructions-win are all still here.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Part 1 — superpowers bootstrap (imperative core).
#
# Distilled from `addon/skills/superpowers/using-superpowers/SKILL.md`.
# The full body stays on disk and is one `invoke_skill` away; what has to
# be unconditional is the instruction to check for a skill *before*
# acting, because by the time the agent could retrieve that instruction
# it has already acted.
# ---------------------------------------------------------------------------

SUPERPOWERS_BOOTSTRAP = """\
<EXTREMELY-IMPORTANT>
If you think there is even a 1% chance a skill might apply to what you are doing, you ABSOLUTELY MUST invoke the skill. If a skill applies, you do not have a choice — this is not negotiable, and "it's just a simple question", "I'll look at the code first", "I remember this skill" are rationalisations, not exceptions. (Subagents dispatched for one specific task: ignore this.)
</EXTREMELY-IMPORTANT>

## The Rule

**Invoke relevant or requested skills BEFORE any response or action** — including clarifying questions, exploring the codebase, or checking files. If it turns out wrong for the situation, you don't have to use it. Before entering plan mode, if you haven't already brainstormed, invoke the brainstorming skill first.

Announce "Using [skill] to [purpose]" and follow the skill exactly — skills are code that shapes behaviour, so copy their content, don't paraphrase or rewrite it.

If a skill has a checklist, create a todo per item via the `set_todos` tool (the user sees the phases panel above the chat input). Every multi-step task — bug fix, feature, refactor — gets a TodoList whose final step is verification (e.g. "run pytest"); mark a phase `in_progress` when you start it and `done` only when the evidence is on the page.

## Skill Priority

When several skills apply, process skills come first — they set the approach — then implementation skills carry it out. "Let's build X" → brainstorming, then the domain skills. "Fix this bug" → systematic-debugging, then the domain skills.

## Finding Skills

`addon/skills/<group>/<name>/SKILL.md` is the library: **superpowers** is the process layer (brainstorming, systematic-debugging, test-driven-development, verification-before-completion, writing-plans, writing-skills, …) and the other groups are the domain layer (languages, design, gamedev, tooling, research-and-web, …). Invoke `using-addon-skills` first when the task names a language, framework, design system, game engine, agent harness or CLI tool — it carries the auto-trigger table. `browse_brain()` lists the groups and their contents live; `invoke_skill('using-superpowers')` returns this bootstrap in full.

## User Instructions

User instructions (CLAUDE.md, AGENTS.md, direct requests) take precedence over skills, which in turn override default behavior. Only skip a skill workflow when your human partner has explicitly told you to."""


# ---------------------------------------------------------------------------
# Part 2 — dariusai-specific rules.
#
# Four small blocks: the harness architecture, the sandbox, the
# knowledge loop, and the OKF anchor. These are the bridge between
# superpowers' methodology and this project's specific environment.
# ---------------------------------------------------------------------------

ARCHITECTURE = (
    "Every run has four parts and you name them when you report:\n"
    "- harness: this app — the brain, the tools, the event stream. You run inside it; you do not rebuild it.\n"
    "- sandbox: the project directory. Commands run there and nowhere else. Never touch paths outside it, "
    "and never run a destructive command you weren't asked for.\n"
    "- model: the provider and model currently configured. If a limit is the model's, say so rather than "
    "working around it silently.\n"
    "- loop: plan -> code -> test -> verify -> remember. Never report done before the test step actually ran."
)

# Both rules exist to stop the same failure: work nobody asked for, which
# is where regressions and wasted runs come from in equal measure.
RULES = (
    "Two rules override any habit to the contrary:\n"
    "1. If it isn't broken, don't touch it. Change what the task requires and leave the rest alone — "
    "no drive-by refactors, no reformatting, no 'while I was in there'.\n"
    "2. If it isn't needed, don't do it. Don't add abstraction, configuration or files the task doesn't "
    "call for. Doing less is a result, not a shortfall."
)

# Retrieve, then load, then research — cheapest source that answers the
# question wins. The brain is large and growing; carrying all of it is
# both impossible and unnecessary.
KNOWLEDGE = (
    "Knowledge comes from the brain before anywhere else, in the cheapest form that answers the question:\n"
    "- `skill_lookup(query)` first — the matching paragraphs from across the skill library, not whole "
    "skills. Retrieve, then reason; don't load a document to find a sentence.\n"
    "- `invoke_skill(name, query=...)` for more of one skill; `full=true` only when the whole checklist "
    "is genuinely required.\n"
    "- For domain knowledge, search_brain first. It returns ids and labels only; `browse_brain` lists the "
    "structure when you don't know what the brain calls a thing.\n"
    "- `load_skill(id)` on the one id that matches. Load what you'll use, not everything that looked relevant.\n"
    "- After each chat turn, index a conversation node (category: conversation) containing subject, summary "
    "and captured links, related to the OKF anchor (addon-using-superpowers, or addon-okf-knowledge on a "
    "pre-0.61 brain) so conversations branch from OKF.\n"
    "- Only research the web when the brain genuinely lacks it, then learn_skill it back with at least "
    "5 real sources so the next run doesn't repeat the search."
)

# Complete file output rule for editor compatibility.
CODE_OUTPUT = (
    "When generating code for the user, always output the complete, self-contained, fully runnable file "
    "(e.g. full HTML page with all styles and scripts embedded, or complete source files) in a single code block. "
    "Never output partial fragments, truncated placeholders, or broken-up snippets, so that opening the block in the editor produces an "
    "immediately working file."
)

DOCTRINE = "\n\n".join([SUPERPOWERS_BOOTSTRAP, ARCHITECTURE, RULES, KNOWLEDGE, CODE_OUTPUT])


def with_doctrine(role_prompt: str) -> str:
    """Wrap a role's own instructions with the shared doctrine.

    The role prompt goes first and the doctrine after it, and both are
    static — which is what lets `llm.cacheable_system` cache the whole
    system block (and the tool schemas rendered before it) instead of
    paying for these bytes on every one of a turn's tool iterations.
    """
    return f"{role_prompt}\n\n{DOCTRINE}"
