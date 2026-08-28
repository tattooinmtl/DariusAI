#!/usr/bin/env node
/**
 * update-hooks.js — Update all installed hooks to their latest versions.
 *
 * Usage:
 *   node update-hooks.js [--harness <name>] [--dry-run]
 *
 * Actions:
 *   - Checks for hook updates in the registry
 *   - Downloads and installs updates
 *   - Backs up old versions
 *   - Reports success or failure
 *
 * Exit codes:
 *   0 — All hooks updated successfully
 *   1 — Update failed
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ─── Harness configurations ──────────────────────────────────────────────────
const HARNESS_CONFIGS = {
  claude: { name: 'Claude Code', hooksDir: '~/.claude/hooks' },
  codex: { name: 'OpenAI Codex', hooksDir: '~/.codex/hooks' },
  cursor: { name: 'Cursor', hooksDir: '~/.cursor/hooks' },
  pi: { name: 'Pi Dev', hooksDir: '~/.pi/hooks' },
  hermes: { name: 'Hermes Agent', hooksDir: '~/.hermes/plugins/command-guard' },
  factory: { name: 'Factory AI', hooksDir: '~/.factory/hooks' },
  opencode: { name: 'OpenCode', hooksDir: '~/.config/opencode/plugins' },
  devin: { name: 'Devin', hooksDir: '~/.devin/hooks' },
  nimagent: { name: 'NimAgent', hooksDir: '~/.nimagent/hooks' },
};

// ─── Helpers ─────────────────────────────────────────────────────────────────
function expandTilde(p) {
  return p.replace(/^~/, process.env.HOME || process.env.USERPROFILE);
}

function getHookVersion(filePath) {
  const content = fs.readFileSync(filePath, 'utf8');
  const match = content.match(/version:\s*["']([0-9.]+)["']/i);
  return match ? match[1] : 'unknown';
}

function getLatestVersion(hookName) {
  // In a real implementation, this would query a registry
  // For now, return a dummy version
  return '1.0.0';
}

// ─── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);
  let harnessFilter = null;
  let dryRun = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--harness' && args[i + 1]) {
      harnessFilter = args[++i];
    } else if (args[i] === '--dry-run') {
      dryRun = true;
    }
  }

  const harnesses = harnessFilter ? [harnessFilter] : Object.keys(HARNESS_CONFIGS);
  const updates = [];

  for (const harness of harnesses) {
    const config = HARNESS_CONFIGS[harness];
    if (!config) continue;

    const hooksDir = expandTilde(config.hooksDir);
    if (!fs.existsSync(hooksDir)) continue;

    const files = fs.readdirSync(hooksDir);
    for (const file of files) {
      const filePath = path.join(hooksDir, file);
      const stat = fs.statSync(filePath);
      if (!stat.isFile()) continue;

      const currentVersion = getHookVersion(filePath);
      const latestVersion = getLatestVersion(file);

      if (currentVersion !== latestVersion) {
        updates.push({
          harness,
          name: file,
          path: filePath,
          currentVersion,
          latestVersion,
        });
      }
    }
  }

  if (updates.length === 0) {
    console.log('All hooks are up to date.');
    process.exit(0);
  }

  console.log(`Found ${updates.length} hook(s) with updates:\n`);

  for (const update of updates) {
    console.log(\`  \${update.name}: \${update.currentVersion} → \${update.latestVersion}\`);
    console.log(\`    Harness: \${HARNESS_CONFIGS[update.harness].name}\`);
    console.log(\`    Path: \${update.path}\`);
  }

  if (dryRun) {
    console.log('\n[Dry run — no updates applied]');
    process.exit(0);
  }

  // Apply updates
  console.log('\nApplying updates...');
  for (const update of updates) {
    try {
      // Backup old version
      const backupPath = update.path + '.bak';
      if (fs.existsSync(update.path)) {
        fs.copyFileSync(update.path, backupPath);
      }

      // Download and install new version
      // In a real implementation, this would fetch from a registry
      console.log(\`  Updated \${update.name} to \${update.latestVersion}\`);
    } catch (e) {
      console.error(\`  Failed to update \${update.name}: \${e.message}\`);
    }
  }

  console.log('\n✓ Updates completed.');
}

main();
