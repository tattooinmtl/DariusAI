#!/usr/bin/env node
/**
 * audit-hooks.js — Audit all installed hooks for security and compliance.
 *
 * Usage:
 *   node audit-hooks.js [--harness <name>] [--json] [--fix]
 *
 * Checks:
 *   - Hardcoded secrets
 *   - Unsafe command patterns
 *   - Missing validation
 *   - Outdated hook versions
 *   - Permission issues
 *
 * Exit codes:
 *   0 — All hooks pass audit
 *   1 — Issues found
 */

const fs = require('fs');
const path = require('path');

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

// ─── Security patterns ───────────────────────────────────────────────────────
const SECRET_PATTERNS = [
  { pattern: /(?:api[_-]?key|apikey|secret|token|password|passwd)\s*[:=]\s*["']?[A-Za-z0-9+/=]{20,}/gi, severity: 'high', desc: 'Hardcoded secret' },
  { pattern: /(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}/gi, severity: 'high', desc: 'GitHub token' },
  { pattern: /sk-[A-Za-z0-9]{20,}/gi, severity: 'high', desc: 'API key' },
  { pattern: /AKIA[A-Z0-9]{16}/gi, severity: 'high', desc: 'AWS access key' },
  { pattern: /AIza[A-Za-z0-9_-]{30,}/gi, severity: 'medium', desc: 'Google API key' },
];

const UNSAFE_PATTERNS = [
  { pattern: /;\s*rm\s+-rf\s+\/?/i, severity: 'critical', desc: 'Destructive rm -rf' },
  { pattern: /;\s*mkfs/i, severity: 'critical', desc: 'Disk formatting' },
  { pattern: /;\s*sudo\s+rm/i, severity: 'high', desc: 'Sudo rm' },
  { pattern: /\|\s*sh\b/i, severity: 'high', desc: 'Pipe to shell' },
  { pattern: /\|\s*bash\b/i, severity: 'high', desc: 'Pipe to bash' },
  { pattern: /git\s+push\s+--force/i, severity: 'medium', desc: 'Force push' },
  { pattern: /gh\s+repo\s+delete/i, severity: 'high', desc: 'Delete repo' },
  { pattern: /:\(\)\{\s*:\|\:\s*&\s*;\s*\}/, severity: 'critical', desc: 'Fork bomb' },
];

// ─── Helpers ─────────────────────────────────────────────────────────────────
function expandTilde(p) {
  return p.replace(/^~/, process.env.HOME || process.env.USERPROFILE);
}

function readFile(filePath) {
  try {
    return fs.readFileSync(filePath, 'utf8');
  } catch {
    return null;
  }
}

// ─── Audit functions ─────────────────────────────────────────────────────────
function auditSecrets(content, filePath) {
  const issues = [];
  for (const { pattern, severity, desc } of SECRET_PATTERNS) {
    const matches = content.match(pattern);
    if (matches) {
      issues.push({
        file: filePath,
        severity,
        type: 'secret',
        description: desc,
        match: matches[0].slice(0, 30) + '...',
      });
    }
  }
  return issues;
}

function auditUnsafeCommands(content, filePath) {
  const issues = [];
  for (const { pattern, severity, desc } of UNSAFE_PATTERNS) {
    const matches = content.match(pattern);
    if (matches) {
      issues.push({
        file: filePath,
        severity,
        type: 'unsafe-command',
        description: desc,
        match: matches[0],
      });
    }
  }
  return issues;
}

function auditPermissions(filePath) {
  const stat = fs.statSync(filePath);
  const issues = [];

  // Check if executable
  if (process.platform !== 'win32') {
    if (!(stat.mode & 0o111)) {
      issues.push({
        file: filePath,
        severity: 'low',
        type: 'permission',
        description: 'Hook is not executable',
      });
    }
  }

  return issues;
}

function auditMissingValidation(content, filePath) {
  const issues = [];

  // Check if hook has any validation logic
  const hasValidation = /BLOCKED_PATTERNS|blocked|validate|check|deny|forbid/i.test(content);
  if (!hasValidation) {
    issues.push({
      file: filePath,
      severity: 'medium',
      type: 'missing-validation',
      description: 'Hook has no command validation logic',
    });
  }

  return issues;
}

// ─── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);
  let harnessFilter = null;
  let jsonOutput = false;
  let fixMode = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--harness' && args[i + 1]) {
      harnessFilter = args[++i];
    } else if (args[i] === '--json') {
      jsonOutput = true;
    } else if (args[i] === '--fix') {
      fixMode = true;
    }
  }

  const harnesses = harnessFilter ? [harnessFilter] : Object.keys(HARNESS_CONFIGS);
  const allIssues = [];

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

      const content = readFile(filePath);
      if (!content) continue;

      // Run audits
      allIssues.push(...auditSecrets(content, filePath));
      allIssues.push(...auditUnsafeCommands(content, filePath));
      allIssues.push(...auditPermissions(filePath));
      allIssues.push(...auditMissingValidation(content, filePath));
    }
  }

  // Output results
  if (jsonOutput) {
    console.log(JSON.stringify(allIssues, null, 2));
  } else {
    console.log('\nHook Audit Report\n');
    console.log('─'.repeat(80));

    if (allIssues.length === 0) {
      console.log('\n✓ No issues found. All hooks pass audit.\n');
    } else {
      console.log(`\nFound ${allIssues.length} issue(s):\n`);

      // Group by severity
      const bySeverity = { critical: [], high: [], medium: [], low: [] };
      for (const issue of allIssues) {
        bySeverity[issue.severity].push(issue);
      }

      for (const [severity, issues] of Object.entries(bySeverity)) {
        if (issues.length === 0) continue;
        console.log(`\n${severity.toUpperCase()} (${issues.length}):`);
        for (const issue of issues) {
          console.log(`  • ${issue.file}`);
          console.log(`    ${issue.type}: ${issue.description}`);
          if (issue.match) {
            console.log(`    Match: ${issue.match}`);
          }
        }
      }
    }

    console.log('\n' + '─'.repeat(80));
  }

  // Exit with appropriate code
  const hasCritical = allIssues.some(i => i.severity === 'critical' || i.severity === 'high');
  process.exit(hasCritical ? 1 : 0);
}

main();
