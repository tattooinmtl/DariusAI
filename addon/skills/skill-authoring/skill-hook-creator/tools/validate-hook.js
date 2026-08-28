#!/usr/bin/env node
/**
 * validate-hook.js — Validate a hook script against the harness's schema.
 *
 * Usage:
 *   node validate-hook.js <hook-file> [--schema <schema-file>]
 *
 * Checks:
 *   - File exists and is readable
 *   - Valid JSON (or TOML) syntax
 *   - Required fields present (name, trigger, command)
 *   - Command is a non-empty string
 *   - Trigger is a known event type
 *   - No shell metacharacters in unsafe positions
 *   - No hardcoded secrets (API keys, tokens)
 *
 * Exit codes:
 *   0 — Valid
 *   1 — Invalid (errors printed to stderr)
 *   2 — Schema error
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ─── Known trigger events per harness ────────────────────────────────────────
const KNOWN_TRIGGERS = {
  pre_tool_use: ['PreToolUse', 'pre_tool_use'],
  post_tool_use: ['PostToolUse', 'post_tool_use'],
  pre_command: ['PreCommand', 'pre_command'],
  post_command: ['PostCommand', 'post_command'],
  pre_llm_call: ['PreLLMCall', 'pre_llm_call'],
  post_llm_call: ['PostLLMCall', 'post_llm_call'],
  on_start: ['OnStart', 'on_start'],
  on_exit: ['OnExit', 'on_exit'],
  on_error: ['OnError', 'on_error'],
};

// ─── Secret patterns to flag ─────────────────────────────────────────────────
const SECRET_PATTERNS = [
  /(?:api[_-]?key|apikey|secret|token|password|passwd|credential)\s*[:=]\s*["']?[A-Za-z0-9+/=]{20,}/gi,
  /(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}/gi,
  /sk-[A-Za-z0-9]{20,}/gi,
  /AIza[A-Za-z0-9_-]{30,}/gi,
  /AKIA[A-Z0-9]{16}/gi,
];

// ─── Unsafe shell patterns ───────────────────────────────────────────────────
const UNSAFE_PATTERNS = [
  /;\s*rm\s+-rf\s+\/?/i,
  /;\s*mkfs/i,
  /;\s*sudo\s+rm/i,
  /\|\s*sh\b/i,
  /\|\s*bash\b/i,
  /git\s+push\s+--force/i,
  /gh\s+repo\s+delete/i,
  /:\(\)\{\s*:\|\:\s*&\s*;\s*\}/,
];

// ─── Helpers ─────────────────────────────────────────────────────────────────
function die(msg) {
  console.error(`ERROR: ${msg}`);
  process.exit(1);
}

function loadJSON(filePath) {
  const raw = fs.readFileSync(filePath, 'utf8');
  return JSON.parse(raw);
}

function parseConfig(content) {
  // Try JSON first
  try {
    return { format: 'json', config: JSON.parse(content) };
  } catch {}
  // Try TOML (simple parser for flat configs)
  try {
    const toml = require('toml');
    return { format: 'toml', config: toml.parse(content) };
  } catch {}
  // Try YAML
  try {
    const yaml = require('yaml');
    return { format: 'yaml', config: yaml.parse(content) };
  } catch {}
  throw new Error('Unsupported config format. Expected JSON, TOML, or YAML.');
}

// ─── Validation ──────────────────────────────────────────────────────────────
function validateHook(config, filePath) {
  const errors = [];
  const warnings = [];

  // 1. Required fields
  if (!config.name || typeof config.name !== 'string') {
    errors.push('Missing or invalid "name" field (required: non-empty string)');
  }
  if (!config.trigger) {
    errors.push('Missing "trigger" field (required)');
  } else if (typeof config.trigger !== 'string') {
    errors.push('"trigger" must be a string');
  }

  // 2. Command field
  if (!config.command || typeof config.command !== 'string') {
    errors.push('Missing or invalid "command" field (required: non-empty string)');
  } else if (config.command.trim().length === 0) {
    errors.push('"command" must not be empty');
  }

  // 3. Trigger validation
  if (config.trigger) {
    const triggerLower = config.trigger.toLowerCase();
    const known = Object.values(KNOWN_TRIGGERS).flat().map(t => t.toLowerCase());
    if (!known.includes(triggerLower)) {
      warnings.push(`Unknown trigger "${config.trigger}". Known: ${known.join(', ')}`);
    }
  }

  // 4. Secret scanning
  const rawContent = fs.readFileSync(filePath, 'utf8');
  for (const pattern of SECRET_PATTERNS) {
    const matches = rawContent.match(pattern);
    if (matches) {
      errors.push(`Potential secret detected: ${matches[0].slice(0, 20)}...`);
    }
  }

  // 5. Unsafe shell patterns
  if (config.command) {
    for (const pattern of UNSAFE_PATTERNS) {
      const matches = config.command.match(pattern);
      if (matches) {
        errors.push(`Unsafe command pattern detected: ${matches[0]}`);
      }
    }
  }

  // 6. Schema-specific validation (if schema provided)
  return { errors, warnings };
}

// ─── Main ────────────────────────────────────────────────────────────────────
function main() {
  const args = process.argv.slice(2);
  let hookFile = null;
  let schemaFile = null;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--schema' && args[i + 1]) {
      schemaFile = args[++i];
    } else if (!args[i].startsWith('--')) {
      hookFile = args[i];
    }
  }

  if (!hookFile) {
    die('Usage: node validate-hook.js <hook-file> [--schema <schema-file>]');
  }

  if (!fs.existsSync(hookFile)) {
    die(`Hook file not found: ${hookFile}`);
  }

  let config;
  try {
    const content = fs.readFileSync(hookFile, 'utf8');
    ({ config } = parseConfig(content));
  } catch (e) {
    die(`Failed to parse hook file: ${e.message}`);
  }

  const { errors, warnings } = validateHook(config, hookFile);

  if (schemaFile) {
    try {
      const schema = loadJSON(schemaFile);
      // Simple schema validation: check required fields from schema
      if (schema.required) {
        for (const field of schema.required) {
          if (config[field] === undefined) {
            errors.push(`Missing required field from schema: ${field}`);
          }
        }
      }
    } catch (e) {
      console.error(`WARNING: Failed to load schema: ${e.message}`);
    }
  }

  // Report
  if (warnings.length > 0) {
    console.warn('\nWarnings:');
    warnings.forEach(w => console.warn(`  ⚠  ${w}`));
  }

  if (errors.length > 0) {
    console.error('\nErrors:');
    errors.forEach(e => console.error(`  ✗  ${e}`));
    process.exit(1);
  }

  console.log('✓ Hook is valid.');
  process.exit(0);
}

main();
