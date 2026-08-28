#!/usr/bin/env node
/**
 * scaffold-hook.js — Scaffold a new hook script for a specific harness.
 *
 * Usage:
 *   node scaffold-hook.js <name> --harness <harness> [--trigger <event>] [--output <dir>]
 *
 * Generates:
 *   - A hook script with the correct shebang and structure
 *   - A README.md with usage instructions
 *   - A test file (optional)
 *
 * Supported harnesses:
 *   claude, codex, cursor, pi, hermes, factory, opencode, devin, nimagent
 */

const fs = require('fs');
const path = require('path');

// ─── Harness configurations ──────────────────────────────────────────────────
const HARNESS_CONFIGS = {
  claude: {
    name: 'Claude Code',
    hooksDir: '~/.claude/hooks',
    exampleTrigger: 'PreToolUse',
    language: 'bash',
    shebang: '#!/bin/bash',
  },
  codex: {
    name: 'OpenAI Codex',
    hooksDir: '~/.codex/hooks',
    exampleTrigger: 'pre_tool_use',
    language: 'bash',
    shebang: '#!/bin/bash',
  },
  cursor: {
    name: 'Cursor',
    hooksDir: '~/.cursor/hooks',
    exampleTrigger: 'PreToolUse',
    language: 'bash',
    shebang: '#!/bin/bash',
  },
  pi: {
    name: 'Pi Dev',
    hooksDir: '~/.pi/hooks',
    exampleTrigger: 'pre_tool_use',
    language: 'typescript',
    shebang: '#!/usr/bin/env node',
  },
  hermes: {
    name: 'Hermes Agent',
    hooksDir: '~/.hermes/plugins/command-guard',
    exampleTrigger: 'PreToolUse',
    language: 'bash',
    shebang: '#!/bin/bash',
  },
  factory: {
    name: 'Factory AI',
    hooksDir: '~/.factory/hooks',
    exampleTrigger: 'pre_tool_use',
    language: 'bash',
    shebang: '#!/bin/bash',
  },
  opencode: {
    name: 'OpenCode',
    hooksDir: '~/.config/opencode/plugins',
    exampleTrigger: 'pre_tool_use',
    language: 'bash',
    shebang: '#!/bin/bash',
  },
  devin: {
    name: 'Devin',
    hooksDir: '~/.devin/hooks',
    exampleTrigger: 'pre_tool_use',
    language: 'bash',
    shebang: '#!/bin/bash',
  },
  nimagent: {
    name: 'NimAgent',
    hooksDir: '~/.nimagent/hooks',
    exampleTrigger: 'pre_tool_use',
    language: 'bash',
    shebang: '#!/bin/bash',
  },
};

// ─── Hook templates ──────────────────────────────────────────────────────────
const BASH_TEMPLATE = `#!/bin/bash
#
# Hook: {{NAME}}
# Harness: {{HARNESS_NAME}}
# Trigger: {{TRIGGER}}
#
# Description: {{DESCRIPTION}}
#
# This hook runs before/after tool commands in {{HARNESS_NAME}}.
# It validates commands and blocks unsafe operations.
#

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
HOOK_NAME="{{NAME}}"
HOOK_TRIGGER="{{TRIGGER}}"
LOG_FILE="{{LOG_DIR}}/${{NAME}}.log"

# ─── Logging ─────────────────────────────────────────────────────────────────
log() {
  local level="$1"
  local message="$2"
  local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[$timestamp] [$level] [$HOOK_NAME] $message" >> "$LOG_FILE"
}

log "INFO" "Hook triggered: $HOOK_TRIGGER"

# ─── Input handling ──────────────────────────────────────────────────────────
# Hooks receive input via stdin or environment variables
# Adapt this section based on your harness's hook interface

COMMAND="${1:-}"
TOOL_NAME="${2:-}"
ARGS="${3:-}"

log "INFO" "Command: $COMMAND"
log "INFO" "Tool: $TOOL_NAME"
log "INFO" "Args: $ARGS"

# ─── Validation logic ────────────────────────────────────────────────────────
# Add your validation logic here

# Example: Block dangerous commands
BLOCKED_PATTERNS=(
  "rm -rf /"
  "mkfs"
  "sudo rm"
  "| sh"
  "| bash"
  "git push --force"
  "gh repo delete"
)

for pattern in "${BLOCKED_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qi "$pattern"; then
    log "WARN" "Blocked command matching pattern: $pattern"
    echo "ERROR: Command blocked by hook '$HOOK_NAME'" >&2
    exit 1
  fi
done

# ─── Success ─────────────────────────────────────────────────────────────────
log "INFO" "Hook completed successfully"
exit 0
`;

const TYPESCRIPT_TEMPLATE = `#!/usr/bin/env node
/**
 * Hook: {{NAME}}
 * Harness: {{HARNESS_NAME}}
 * Trigger: {{TRIGGER}}
 *
 * Description: {{DESCRIPTION}}
 *
 * This hook runs before/after tool commands in {{HARNESS_NAME}}.
 * It validates commands and blocks unsafe operations.
 */

import * as fs from 'fs';
import * as path from 'path';

// ─── Configuration ───────────────────────────────────────────────────────────
const HOOK_NAME = "{{NAME}}";
const HOOK_TRIGGER = "{{TRIGGER}}";
const LOG_DIR = process.env.HOOK_LOG_DIR || path.join(process.env.HOME || '.', '.hooks-logs');
const LOG_FILE = path.join(LOG_DIR, \`\${HOOK_NAME}.log\`);

// ─── Logging ─────────────────────────────────────────────────────────────────
function log(level: string, message: string): void {
  const timestamp = new Date().toISOString();
  const logEntry = \`[\${timestamp}] [\${level}] [\${HOOK_NAME}] \${message}\`;
  console.log(logEntry);

  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(LOG_FILE, logEntry + '\n');
  } catch (e) {
    console.error(\`Failed to write log: \${e.message}\`);
  }
}

log('INFO', \`Hook triggered: \${HOOK_TRIGGER}\`);

// ─── Input handling ──────────────────────────────────────────────────────────
// Hooks receive input via stdin or environment variables
// Adapt this section based on your harness's hook interface

const args = process.argv.slice(2);
const command = args[0] || '';
const toolName = args[1] || '';
const toolArgs = args.slice(2).join(' ') || '';

log('INFO', \`Command: \${command}\`);
log('INFO', \`Tool: \${toolName}\`);
log('INFO', \`Args: \${toolArgs}\`);

// ─── Validation logic ────────────────────────────────────────────────────────
// Add your validation logic here

// Example: Block dangerous commands
const BLOCKED_PATTERNS = [
  /rm\s+-rf\s+\/?/i,
  /mkfs/i,
  /sudo\s+rm/i,
  /\|\s*sh\b/i,
  /\|\s*bash\b/i,
  /git\s+push\s+--force/i,
  /gh\s+repo\s+delete/i,
];

for (const pattern of BLOCKED_PATTERNS) {
  if (pattern.test(command)) {
    log('WARN', \`Blocked command matching pattern: \${pattern}\`);
    console.error(\`ERROR: Command blocked by hook '\${HOOK_NAME}'\`);
    process.exit(1);
  }
}

// ─── Success ─────────────────────────────────────────────────────────────────
log('INFO', 'Hook completed successfully');
process.exit(0);
`;

const README_TEMPLATE = `# Hook: {{NAME}}

**Harness:** {{HARNESS_NAME}}
**Trigger:** {{TRIGGER}}
**Language:** {{LANGUAGE}}

## Description

{{DESCRIPTION}}

## Installation

\`\`\`bash
node install-hook.js {{NAME}}.{{EXT}} --harness {{HARNESS}}
\`\`\`

## Configuration

Edit the hook file to customize:

- \`BLOCKED_PATTERNS\` — Commands to block
- \`LOG_FILE\` — Log destination
- Validation logic

## Testing

\`\`\`bash
# Validate the hook
node validate-hook.js {{NAME}}.{{EXT}}

# Test the hook
bash {{NAME}}.{{EXT}} "echo hello"

# View logs
tail -f ~/.hooks-logs/{{NAME}}.log
\`\`\`

## Files

- \`${{NAME}}.{{EXT}}\` — Hook script
- \`README.md\` — This file
- \`test-{{NAME}}.test.{{TEST_EXT}}\` — Test suite (if generated)

## License

{{LICENSE}}
`;

// ─── Helpers ─────────────────────────────────────────────────────────────────
function expandTilde(p) {
  return p.replace(/^~/, process.env.HOME || process.env.USERPROFILE);
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

// ─── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);
  let name = null;
  let harness = null;
  let trigger = null;
  let outputDir = null;
  let description = 'Custom hook for command validation';
  let license = 'MIT';

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--harness' && args[i + 1]) {
      harness = args[++i];
    } else if (args[i] === '--trigger' && args[i + 1]) {
      trigger = args[++i];
    } else if (args[i] === '--output' && args[i + 1]) {
      outputDir = args[++i];
    } else if (args[i] === '--description' && args[i + 1]) {
      description = args[++i];
    } else if (args[i] === '--license' && args[i + 1]) {
      license = args[++i];
    } else if (!args[i].startsWith('--')) {
      name = args[i];
    }
  }

  if (!name) {
    console.error('Usage: node scaffold-hook.js <name> --harness <harness> [--trigger <event>] [--output <dir>]');
    process.exit(1);
  }

  if (!harness) {
    console.error('Error: --harness is required');
    console.error('Supported: ' + Object.keys(HARNESS_CONFIGS).join(', '));
    process.exit(1);
  }

  const harnessConfig = HARNESS_CONFIGS[harness];
  if (!harnessConfig) {
    console.error(`Unknown harness: ${harness}`);
    console.error('Supported: ' + Object.keys(HARNESS_CONFIGS).join(', '));
    process.exit(1);
  }

  // Determine output directory
  if (!outputDir) {
    outputDir = expandTilde(harnessConfig.hooksDir);
  }
  ensureDir(outputDir);

  // Determine trigger
  if (!trigger) {
    trigger = harnessConfig.exampleTrigger;
  }

  // Determine file extension
  const ext = harnessConfig.language === 'typescript' ? 'ts' : 'sh';
  const testExt = harnessConfig.language === 'typescript' ? 'test.ts' : 'test.sh';

  // Generate hook file
  const template = harnessConfig.language === 'typescript' ? TYPESCRIPT_TEMPLATE : BASH_TEMPLATE;
  const hookContent = template
    .replace(/\{\{NAME\}\}/g, name)
    .replace(/\{\{HARNESS_NAME\}\}/g, harnessConfig.name)
    .replace(/\{\{TRIGGER\}\}/g, trigger)
    .replace(/\{\{DESCRIPTION\}\}/g, description)
    .replace(/\{\{LOG_DIR\}\}/g, expandTilde('~/.hooks-logs'))
    .replace(/\{\{HARNESS\}\}/g, harness);

  const hookPath = path.join(outputDir, `${name}.${ext}`);
  fs.writeFileSync(hookPath, hookContent);
  fs.chmodSync(hookPath, '755');
  console.log(`✓ Created hook: ${hookPath}`);

  // Generate README
  const readmeContent = README_TEMPLATE
    .replace(/\{\{NAME\}\}/g, name)
    .replace(/\{\{HARNESS_NAME\}\}/g, harnessConfig.name)
    .replace(/\{\{TRIGGER\}\}/g, trigger)
    .replace(/\{\{LANGUAGE\}\}/g, harnessConfig.language)
    .replace(/\{\{EXT\}\}/g, ext)
    .replace(/\{\{TEST_EXT\}\}/g, testExt)
    .replace(/\{\{DESCRIPTION\}\}/g, description)
    .replace(/\{\{HARNESS\}\}/g, harness)
    .replace(/\{\{LICENSE\}\}/g, license);

  const readmePath = path.join(outputDir, 'README.md');
  fs.writeFileSync(readmePath, readmeContent);
  console.log(`✓ Created README: ${readmePath}`);

  // Generate test file
  const testContent = harnessConfig.language === 'typescript'
    ? `/**
 * Tests for hook: ${name}
 */

import { describe, it, expect } from 'vitest';

describe('${name} hook', () => {
  it('should block dangerous commands', () => {
    // Test your validation logic here
    expect(true).toBe(true);
  });

  it('should allow safe commands', () => {
    // Test your validation logic here
    expect(true).toBe(true);
  });
});
`
    : `#!/bin/bash
#
# Tests for hook: ${name}
#

set -euo pipefail

HOOK_PATH="$(dirname "$0")/${name}.${ext}"

echo "Testing hook: ${name}"

# Test 1: Block dangerous commands
echo "Test 1: Blocking dangerous commands..."
for cmd in "rm -rf /" "mkfs /dev/sda" "git push --force"; do
  if bash "$HOOK_PATH" "$cmd" 2>/dev/null; then
    echo "FAIL: Command should have been blocked: $cmd"
    exit 1
  fi
done
echo "PASS: Dangerous commands blocked"

# Test 2: Allow safe commands
echo "Test 2: Allowing safe commands..."
if bash "$HOOK_PATH" "echo hello" 2>/dev/null; then
  echo "PASS: Safe command allowed"
else
  echo "FAIL: Safe command should have been allowed"
  exit 1
fi

echo "All tests passed!"
`;

  const testPath = path.join(outputDir, `test-${name}.${testExt}`);
  fs.writeFileSync(testPath, testContent);
  if (ext === 'sh') {
    fs.chmodSync(testPath, '755');
  }
  console.log(`✓ Created test: ${testPath}`);

  console.log(`\n✓ Hook scaffolded successfully!`);
  console.log(`\nTo install: node install-hook.js ${name}.${ext} --harness ${harness}`);
}

main();
