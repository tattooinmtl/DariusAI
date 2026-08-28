#!/usr/bin/env node
/**
 * list-hooks.js — List all installed hooks across all supported harnesses.
 *
 * Usage:
 *   node list-hooks.js [--harness <name>] [--json]
 *
 * Output:
 *   Table of hooks per harness, or JSON array if --json flag is set.
 */

const fs = require('fs');
const path = require('path');

// ─── Harness configurations ──────────────────────────────────────────────────
const HARNESS_CONFIGS = {
  claude: {
    name: 'Claude Code',
    hooksDir: '~/.claude/hooks',
    configFile: '~/.claude/settings.json',
  },
  codex: {
    name: 'OpenAI Codex',
    hooksDir: '~/.codex/hooks',
    configFile: '~/.codex/hooks.json',
  },
  cursor: {
    name: 'Cursor',
    hooksDir: '~/.cursor/hooks',
    configFile: '~/.cursor/settings.json',
  },
  pi: {
    name: 'Pi Dev',
    hooksDir: '~/.pi/hooks',
    configFile: '~/.pi/agent/extensions/',
  },
  hermes: {
    name: 'Hermes Agent',
    hooksDir: '~/.hermes/plugins/command-guard',
    configFile: '~/.hermes/config.json',
  },
  factory: {
    name: 'Factory AI',
    hooksDir: '~/.factory/hooks',
    configFile: '~/.factory/settings.json',
  },
  opencode: {
    name: 'OpenCode',
    hooksDir: '~/.config/opencode/plugins',
    configFile: '~/.config/opencode/config.json',
  },
  devin: {
    name: 'Devin',
    hooksDir: '~/.devin/hooks',
    configFile: '~/.devin/config.json',
  },
  nimagent: {
    name: 'NimAgent',
    hooksDir: '~/.nimagent/hooks',
    configFile: '~/.nimagent/config.json',
  },
};

// ─── Helpers ─────────────────────────────────────────────────────────────────
function expandTilde(p) {
  return p.replace(/^~/, process.env.HOME || process.env.USERPROFILE);
}

function getHooks(harnessConfig) {
  const hooksDir = expandTilde(harnessConfig.hooksDir);
  const hooks = [];

  if (fs.existsSync(hooksDir)) {
    const files = fs.readdirSync(hooksDir);
    for (const file of files) {
      const filePath = path.join(hooksDir, file);
      const stat = fs.statSync(filePath);
      if (stat.isFile()) {
        hooks.push({
          name: file,
          path: filePath,
          size: stat.size,
          modified: stat.mtime.toISOString(),
        });
      }
    }
  }

  return hooks;
}

// ─── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);
  let harnessFilter = null;
  let jsonOutput = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--harness' && args[i + 1]) {
      harnessFilter = args[++i];
    } else if (args[i] === '--json') {
      jsonOutput = true;
    }
  }

  const harnesses = harnessFilter ? [harnessFilter] : Object.keys(HARNESS_CONFIGS);
  const results = [];

  for (const harness of harnesses) {
    const config = HARNESS_CONFIGS[harness];
    if (!config) {
      console.error(`Unknown harness: ${harness}`);
      continue;
    }

    const hooks = getHooks(config);
    results.push({
      harness,
      name: config.name,
      hooksDir: config.hooksDir,
      count: hooks.length,
      hooks,
    });
  }

  if (jsonOutput) {
    console.log(JSON.stringify(results, null, 2));
  } else {
    console.log('\nInstalled Hooks by Harness:\n');
    console.log('─'.repeat(80));

    for (const result of results) {
      console.log(`\n${result.name} (${result.hooks})`);
      console.log(`  Directory: ${result.hooksDir}`);
      console.log(`  Hooks: ${result.count}`);

      if (result.hooks.length > 0) {
        for (const hook of result.hooks) {
          console.log(`    - ${hook.name} (${hook.size} bytes, modified ${hook.modified})`);
        }
      } else {
        console.log('    (no hooks installed)');
      }
    }

    console.log('\n' + '─'.repeat(80));
  }
}

main();
