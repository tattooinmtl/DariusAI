#!/usr/bin/env node
/**
 * remove-hook.js — Remove a hook from a harness.
 *
 * Usage:
 *   node remove-hook.js <hook-name> --harness <harness-name> [--dry-run]
 *
 * Actions:
 *   - Removes the hook file from the harness's hooks directory
 *   - Updates the harness's config to remove the hook reference
 *   - Reports success or failure
 *
 * Exit codes:
 *   0 — Removed successfully
 *   1 — Error
 */

const fs = require('fs');
const path = require('path');

// ─── Harness configurations ──────────────────────────────────────────────────
const HARNESS_CONFIGS = {
  claude: {
    name: 'Claude Code',
    hooksDir: '~/.claude/hooks',
    configFile: '~/.claude/settings.json',
    hookField: 'hooks',
  },
  codex: {
    name: 'OpenAI Codex',
    hooksDir: '~/.codex/hooks',
    configFile: '~/.codex/hooks.json',
    hookField: null,
  },
  cursor: {
    name: 'Cursor',
    hooksDir: '~/.cursor/hooks',
    configFile: '~/.cursor/settings.json',
    hookField: 'hooks',
  },
  pi: {
    name: 'Pi Dev',
    hooksDir: '~/.pi/hooks',
    configFile: '~/.pi/agent/extensions/',
    hookField: null,
  },
  hermes: {
    name: 'Hermes Agent',
    hooksDir: '~/.hermes/plugins/command-guard',
    configFile: '~/.hermes/config.json',
    hookField: 'commandGuard',
  },
  factory: {
    name: 'Factory AI',
    hooksDir: '~/.factory/hooks',
    configFile: '~/.factory/settings.json',
    hookField: 'hooks',
  },
  opencode: {
    name: 'OpenCode',
    hooksDir: '~/.config/opencode/plugins',
    configFile: '~/.config/opencode/config.json',
    hookField: 'plugins',
  },
  devin: {
    name: 'Devin',
    hooksDir: '~/.devin/hooks',
    configFile: '~/.devin/config.json',
    hookField: 'hooks',
  },
  nimagent: {
    name: 'NimAgent',
    hooksDir: '~/.nimagent/hooks',
    configFile: '~/.nimagent/config.json',
    hookField: 'hooks',
  },
};

// ─── Helpers ─────────────────────────────────────────────────────────────────
function expandTilde(p) {
  return p.replace(/^~/, process.env.HOME || process.env.USERPROFILE);
}

function loadJSON(filePath) {
  const raw = fs.readFileSync(expandTilde(filePath), 'utf8');
  return JSON.parse(raw);
}

function saveJSON(filePath, data) {
  fs.writeFileSync(expandTilde(filePath), JSON.stringify(data, null, 2) + '\n');
}

// ─── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);
  let hookName = null;
  let harness = null;
  let dryRun = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--harness' && args[i + 1]) {
      harness = args[++i];
    } else if (args[i] === '--dry-run') {
      dryRun = true;
    } else if (!args[i].startsWith('--')) {
      hookName = args[i];
    }
  }

  if (!hookName) {
    console.error('Usage: node remove-hook.js <hook-name> --harness <harness-name> [--dry-run]');
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

  const hooksDir = expandTilde(harnessConfig.hooksDir);
  const hookPath = path.join(hooksDir, hookName);

  console.log(`Removing hook: ${hookName}`);
  console.log(`Harness: ${harnessConfig.name}`);
  console.log(`Path: ${hookPath}`);

  if (dryRun) {
    console.log('\n[Dry run — no changes made]');
    if (fs.existsSync(hookPath)) {
      console.log(`Would remove: ${hookPath}`);
    } else {
      console.log(`Hook file not found: ${hookPath}`);
    }
    process.exit(0);
  }

  // Remove hook file
  if (fs.existsSync(hookPath)) {
    fs.unlinkSync(hookPath);
    console.log(`✓ Removed hook file: ${hookPath}`);
  } else {
    console.log(`⚠ Hook file not found: ${hookPath}`);
  }

  // Update harness config
  if (harnessConfig.configFile && harnessConfig.hookField) {
    const configPath = expandTilde(harnessConfig.configFile);
    if (fs.existsSync(configPath)) {
      let config = loadJSON(configPath);
      if (config[harnessConfig.hookField]) {
        config[harnessConfig.hookField] = config[harnessConfig.hookField].filter(
          h => h !== hookName
        );
        saveJSON(configPath, config);
        console.log(`✓ Updated config: ${configPath}`);
      }
    }
  }

  console.log('\n✓ Hook removed successfully!');
}

main();
