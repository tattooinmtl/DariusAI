#!/usr/bin/env node
/**
 * install-agents-md.js — Drop a per-agent entry-point file (AGENTS.md,
 * CLAUDE.md, GEMINI.md, KIMI.md, PI.md, OMNI.md) into each target agent's
 * home directory so the agent follows the six-phase plan-and-dispatch flow
 * when it starts a session.
 *
 * Usage:
 *   node install-agents-md.js --harness <name> [--dry-run]
 *
 *   <name> = omni | claude | gemini | kimicode | pi | all
 *
 * Source files live in examples/ next to this skill:
 *   examples/AGENTS.md    (canonical, used for 'all' fallback)
 *   examples/CLAUDE.md
 *   examples/GEMINI.md
 *   examples/KIMI.md
 *   examples/PI.md
 *   examples/OMNI.md
 *
 * Exit codes:
 *   0 — installed
 *   1 — bad args or missing source
 */

const fs = require('fs');
const path = require('path');

// ─── Per-agent mapping ───────────────────────────────────────────────────────
const AGENT_TARGETS = {
  omni: {
    name: 'Omni Agent',
    src:  'OMNI.md',
    dest: '~/.omni/AGENTS.md',
  },
  claude: {
    name: 'Claude Code',
    src:  'CLAUDE.md',
    dest: '~/.claude/CLAUDE.md',
  },
  gemini: {
    name: 'Gemini CLI',
    src:  'GEMINI.md',
    dest: '~/.gemini/GEMINI.md',
  },
  kimicode: {
    name: 'Kimi Code CLI',
    src:  'KIMI.md',
    dest: '~/.kimi-code/KIMI.md',
  },
  pi: {
    name: 'Pi Dev',
    src:  'PI.md',
    dest: '~/.pi/PI.md',
  },
};

function expandTilde(p) {
  const home = process.platform === 'win32'
    ? (process.env.USERPROFILE || process.env.HOME)
    : (process.env.HOME || process.env.USERPROFILE);
  return p.replace(/^~/, home);
}

function parseArgs() {
  const args = process.argv.slice(2);
  let harness = null;
  let dryRun = false;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--harness' && args[i + 1]) harness = args[++i];
    else if (args[i] === '--dry-run') dryRun = true;
  }
  return { harness, dryRun };
}

function main() {
  const { harness, dryRun } = parseArgs();

  if (!harness) {
    console.error('Usage: node install-agents-md.js --harness <name> [--dry-run]');
    console.error('  name = omni | claude | gemini | kimicode | pi | all');
    process.exit(1);
  }

  const skillsRoot = path.join(__dirname, '..');
  const examplesDir = path.join(skillsRoot, 'examples');

  const targets = harness === 'all'
    ? Object.entries(AGENT_TARGETS)
    : (AGENT_TARGETS[harness]
        ? [[harness, AGENT_TARGETS[harness]]]
        : null);

  if (!targets) {
    console.error(`Unknown harness: ${harness}`);
    console.error('Supported: ' + Object.keys(AGENT_TARGETS).join(', ') + ', all');
    process.exit(1);
  }

  let installed = 0;
  for (const [key, t] of targets) {
    const srcPath = path.join(examplesDir, t.src);
    const destPath = expandTilde(t.dest);

    if (!fs.existsSync(srcPath)) {
      console.error(`✗ Missing source: ${srcPath}`);
      continue;
    }

    if (dryRun) {
      console.log(`[dry-run] ${t.name}: ${srcPath} → ${destPath}`);
      installed++;
      continue;
    }

    try {
      fs.mkdirSync(path.dirname(destPath), { recursive: true });
      fs.copyFileSync(srcPath, destPath);
      console.log(`✓ ${t.name}: ${destPath}`);
      installed++;
    } catch (e) {
      console.error(`✗ ${t.name}: ${e.message}`);
    }
  }

  if (installed === 0) process.exit(1);
  console.log(`\nDone — ${installed} file(s) ${dryRun ? 'would be ' : ''}installed.`);
}

main();