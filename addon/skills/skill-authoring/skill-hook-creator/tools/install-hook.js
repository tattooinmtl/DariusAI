#!/usr/bin/env node
/**
 * install-hook.js — Install a hook script into a harness's hooks directory.
 *
 * Usage:
 *   node install-hook.js <hook-file> --harness <harness-name> [--dry-run]
 *
 * Supported harnesses:
 *   omni, claude, codex, cursor, pi, hermes, factory, opencode, devin, nimagent, gemini, kimicode
 *
 * Actions:
 *   - Validates the hook before installing
 *   - Creates the harness's hooks directory if needed
 *   - Copies the hook to the correct location
 *   - Updates the harness's config if needed
 *   - Reports success or failure
 *
 * Exit codes:
 *   0 — Installed successfully
 *   1 — Validation failed or install error
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ─── Harness configurations ──────────────────────────────────────────────────
const HARNESS_CONFIGS = {
  omni: {
    name: 'Omni Agent',
    hooksDir: '~/.omni/hooks',
    configFile: '~/.omni/omni.config.json',
    format: 'json',
    hookField: 'hooks',
  },
  claude: {
    name: 'Claude Code',
    hooksDir: '~/.claude/hooks',
    configFile: '~/.claude/settings.json',
    format: 'json',
    hookField: 'hooks',
  },
  codex: {
    name: 'OpenAI Codex',
    hooksDir: '~/.codex/hooks',
    configFile: '~/.codex/hooks.json',
    format: 'json',
    hookField: null,
  },
  cursor: {
    name: 'Cursor',
    hooksDir: '~/.cursor/hooks',
    configFile: '~/.cursor/settings.json',
    format: 'json',
    hookField: 'hooks',
  },
  pi: {
    name: 'Pi Dev',
    hooksDir: '~/.pi/hooks',
    configFile: '~/.pi/agent/extensions/index.json',
    format: 'ts',
    hookField: null,
  },
  hermes: {
    name: 'Hermes Agent',
    hooksDir: '~/.hermes/plugins/command-guard',
    configFile: '~/.hermes/config.json',
    format: 'json',
    hookField: 'commandGuard',
  },
  factory: {
    name: 'Factory AI',
    hooksDir: '~/.factory/hooks',
    configFile: '~/.factory/settings.json',
    format: 'json',
    hookField: 'hooks',
  },
  opencode: {
    name: 'OpenCode',
    hooksDir: '~/.config/opencode/plugins',
    configFile: '~/.config/opencode/config.json',
    format: 'json',
    hookField: 'plugins',
  },
  devin: {
    name: 'Devin',
    hooksDir: '~/.devin/hooks',
    configFile: '~/.devin/config.json',
    format: 'json',
    hookField: 'hooks',
  },
  nimagent: {
    name: 'NimAgent',
    hooksDir: '~/.nimagent/hooks',
    configFile: '~/.nimagent/config.json',
    format: 'json',
    hookField: 'hooks',
  },
  gemini: {
    name: 'Gemini CLI',
    hooksDir: '~/.gemini/hooks',
    configFile: '~/.gemini/settings.json',
    format: 'json',
    hookField: 'hooks',
  },
  kimicode: {
    name: 'Kimi Code CLI',
    hooksDir: '~/.kimi-code/hooks',
    configFile: '~/.kimi-code/config.toml',
    format: 'toml',
    hookField: 'hooks',
  },
};

// ─── Helpers ─────────────────────────────────────────────────────────────────
function expandTilde(p) {
  // Prefer USERPROFILE on win32 to avoid MSYS/Cygwin/Git-Bash HOME mismatches.
  const home = process.platform === 'win32'
    ? (process.env.USERPROFILE || process.env.HOME)
    : (process.env.HOME || process.env.USERPROFILE);
  return p.replace(/^~/, home);
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
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
  let hookFile = null;
  let harness = null;
  let dryRun = false;

  let harnessSet = false;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--harness' && args[i + 1]) {
      if (harnessSet) console.warn(`WARNING: --harness specified more than once; using "${args[i + 1]}"`);
      harness = args[++i];
      harnessSet = true;
    } else if (args[i] === '--dry-run') {
      dryRun = true;
    } else if (!args[i].startsWith('--')) {
      hookFile = args[i];
    }
  }

  if (!hookFile) {
    console.error('Usage: node install-hook.js <hook-file> --harness <harness-name> [--dry-run]');
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

  // Validate hook first (uses the real validator, not a bare JSON.parse)
  console.log(`Validating ${hookFile}...`);
  try {
    execSync(`node "${path.join(__dirname, 'validate-hook.js')}" "${hookFile}"`, {
      stdio: 'inherit',
    });
  } catch (e) {
    console.error(`✗ Hook validation failed (exit ${e.status || 1})`);
    process.exit(1);
  }

  // Install
  const hooksDir = expandTilde(harnessConfig.hooksDir);
  const destPath = path.join(hooksDir, path.basename(hookFile));

  console.log(`\nTarget harness: ${harnessConfig.name}`);
  console.log(`Hooks directory: ${hooksDir}`);
  console.log(`Destination: ${destPath}`);

  if (dryRun) {
    console.log('\n[Dry run — no changes made]');
    console.log(`Would copy: ${hookFile} → ${destPath}`);
    if (harnessConfig.configFile) {
      console.log(`Would update config: ${harnessConfig.configFile}`);
    }
    process.exit(0);
  }

  // Create hooks directory
  ensureDir(hooksDir);

  // Copy hook file
  fs.copyFileSync(hookFile, destPath);
  console.log(`✓ Installed hook to ${destPath}`);

  // Update harness config if needed
  if (harnessConfig.configFile && harnessConfig.hookField) {
    const configPath = expandTilde(harnessConfig.configFile);
    try {
      let config = {};
      if (fs.existsSync(configPath)) {
        config = loadJSON(configPath);
      }

      if (!config[harnessConfig.hookField]) {
        config[harnessConfig.hookField] = [];
      }

      // Avoid duplicates
      const hookName = path.basename(hookFile);
      if (!config[harnessConfig.hookField].includes(hookName)) {
        config[harnessConfig.hookField].push(hookName);
        saveJSON(configPath, config);
        console.log(`✓ Updated config: ${configPath}`);
      } else {
        console.log(`✓ Hook already registered in config`);
      }
    } catch (e) {
      console.warn(`WARNING: Failed to update config at ${configPath}: ${e.message}`);
      console.warn('         Hook was copied successfully, but the harness config was not updated.');
      console.warn('         Edit the config manually to register the hook.');
    }
  }

  console.log('\n✓ Hook installed successfully!');
}

main();
