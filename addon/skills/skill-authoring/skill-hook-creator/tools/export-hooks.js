#!/usr/bin/env node
/**
 * export-hooks.js — Export all installed hooks to a backup directory.
 *
 * Usage:
 *   node export-hooks.js [--output <dir>] [--harness <name>] [--json]
 *
 * Actions:
 *   - Copies all hooks to the output directory
 *   - Creates a manifest.json with metadata
 *   - Optionally creates a tarball for portability
 *
 * Exit codes:
 *   0 — Export successful
 *   1 — Error
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

function copyFile(src, dest) {
  const destDir = path.dirname(dest);
  fs.mkdirSync(destDir, { recursive: true });
  fs.copyFileSync(src, dest);
}

// ─── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);
  let outputDir = null;
  let harnessFilter = null;
  let jsonOutput = false;
  let createTarball = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--output' && args[i + 1]) {
      outputDir = args[++i];
    } else if (args[i] === '--harness' && args[i + 1]) {
      harnessFilter = args[++i];
    } else if (args[i] === '--json') {
      jsonOutput = true;
    } else if (args[i] === '--tarball') {
      createTarball = true;
    }
  }

  if (!outputDir) {
    outputDir = path.join(process.env.HOME || '.', 'hooks-backup');
  }
  outputDir = expandTilde(outputDir);

  const harnesses = harnessFilter ? [harnessFilter] : Object.keys(HARNESS_CONFIGS);
  const manifest = {
    exportedAt: new Date().toISOString(),
    hooksDir: outputDir,
    harnesses: {},
  };

  console.log(`Exporting hooks to: ${outputDir}\n`);

  for (const harness of harnesses) {
    const config = HARNESS_CONFIGS[harness];
    if (!config) continue;

    const hooksDir = expandTilde(config.hooksDir);
    if (!fs.existsSync(hooksDir)) continue;

    const files = fs.readdirSync(hooksDir);
    const harnessHooks = [];

    for (const file of files) {
      const srcPath = path.join(hooksDir, file);
      const destPath = path.join(outputDir, harness, file);
      const stat = fs.statSync(srcPath);

      if (stat.isFile()) {
        copyFile(srcPath, destPath);
        harnessHooks.push({
          name: file,
          path: destPath,
          size: stat.size,
          modified: stat.mtime.toISOString(),
        });
      }
    }

    if (harnessHooks.length > 0) {
      manifest.harnesses[harness] = {
        name: config.name,
        count: harnessHooks.length,
        hooks: harnessHooks,
      };
    }
  }

  // Write manifest
  const manifestPath = path.join(outputDir, 'manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  console.log(`✓ Manifest written: ${manifestPath}`);

  // Create tarball if requested
  if (createTarball) {
    const tarballPath = path.join(path.dirname(outputDir), 'hooks-backup.tar.gz');
    try {
      execSync(\`tar -czf "\${tarballPath}" -C "\${path.dirname(outputDir)}" "\${path.basename(outputDir)}"\`, {
        stdio: 'pipe',
      });
      console.log(\`✓ Tarball created: \${tarballPath}\`);
    } catch (e) {
      console.error(\`⚠ Failed to create tarball: \${e.message}\`);
    }
  }

  if (jsonOutput) {
    console.log(JSON.stringify(manifest, null, 2));
  } else {
    console.log(\`\n✓ Exported \${Object.values(manifest.harnesses).reduce((sum, h) => sum + h.count, 0)} hook(s)\`);
  }
}

main();
