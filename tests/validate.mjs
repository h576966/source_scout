import { strict as assert } from 'node:assert';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');

const results = [];
let checksPassed = 0;
let checksFailed = 0;

function readText(filePath) {
  const buf = readFileSync(filePath);
  const bom16LE = buf[0] === 0xFF && buf[1] === 0xFE;
  const bom16BE = buf[0] === 0xFE && buf[1] === 0xFF;
  const bom8 = buf[0] === 0xEF && buf[1] === 0xBB && buf[2] === 0xBF;

  if (bom16LE) return buf.toString('utf16le').replace(/^\uFEFF/, '');
  if (bom16BE) return buf.toString('utf16be').replace(/^\uFEFF/, '');
  if (bom8) return buf.toString('utf-8').replace(/^\uFEFF/, '');
  return buf.toString('utf-8');
}

function pass(label) {
  results.push(`  \x1b[32m✓\x1b[0m ${label}`);
  checksPassed++;
}

function fail(label, detail) {
  results.push(`  \x1b[31m✗\x1b[0m ${label}`);
  if (detail) results.push(`    \x1b[31m→ ${detail}\x1b[0m`);
  checksFailed++;
}

// ── JSONC parser ───────────────────────────────────────────────

function stripJsonComments(text) {
  text = text.replace(/\/\*[\s\S]*?\*\//g, '');
  const lines = text.split('\n');
  const out = lines.map(line => {
    const idx = line.indexOf('//');
    if (idx === -1) return line;
    if (idx > 0 && line[idx - 1] === ':') return line;
    return line.substring(0, idx);
  });
  return out.join('\n');
}

function readJsonc(filePath) {
  const raw = readText(filePath);
  const stripped = stripJsonComments(raw);
  return JSON.parse(stripped);
}

// ── YAML frontmatter parser ────────────────────────────────────

function parseYamlFrontmatter(content) {
  const lines = content.split('\n');
  let start = -1;
  let end = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim() === '---') {
      if (start === -1) start = i;
      else if (end === -1) { end = i; break; }
    }
  }
  if (start === -1 || end === -1) return null;

  const result = {};
  let currentParent = null;
  let currentParentObj = null;

  for (let i = start + 1; i < end; i++) {
    const line = lines[i];
    if (line.trim() === '') continue;

    const indent = line.search(/\S/);
    const trimmed = line.trim();
    const colonIdx = trimmed.indexOf(':');

    if (colonIdx === -1) continue;

    const key = trimmed.substring(0, colonIdx).trim();
    const value = trimmed.substring(colonIdx + 1).trim();

    if (indent === 0 || indent < 2) {
      currentParent = null;
      currentParentObj = null;
      if (value === '') {
        result[key] = {};
        currentParent = key;
        currentParentObj = result;
      } else {
        result[key] = parseScalar(value);
      }
    } else {
      if (currentParent && currentParentObj) {
        if (value === '') {
          currentParentObj[currentParent][key] = {};
          currentParent = key;
          currentParentObj = currentParentObj[currentParent];
        } else {
          currentParentObj[currentParent] = currentParentObj[currentParent] || {};
          currentParentObj[currentParent][key] = parseScalar(value);
        }
      }
    }
  }
  return result;
}

function parseScalar(value) {
  if (value === 'true') return true;
  if (value === 'false') return false;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  return value;
}

// ── Agent file helpers ─────────────────────────────────────────

const OVERRIDE_AGENTS = ['plan', 'ask', 'reviewer', 'worker'];
const BUILTIN_AGENTS = ['compaction', 'explore'];

function readAgentYaml(name) {
  const filePath = join(ROOT, '.kilo', 'agents', `${name}.md`);
  if (!existsSync(filePath)) return null;
  const content = readText(filePath);
  return { path: filePath, yaml: parseYamlFrontmatter(content) };
}

// ── Validation checks ──────────────────────────────────────────

function checkJsonConfig() {
  const section = 'kilo.jsonc';
  results.push(`\n[${section}]`);

  const path = join(ROOT, 'kilo.jsonc');
  if (!existsSync(path)) { fail('File exists', 'kilo.jsonc not found'); return; }
  pass('File exists');

  let config;
  try {
    config = readJsonc(path);
    pass('Valid JSONC');
  } catch (e) {
    fail('Valid JSONC', e.message);
    return;
  }

  if (config.agent && typeof config.agent === 'object') {
    pass('agent block present');
    const entries = Object.keys(config.agent);
    for (const key of entries) {
      if (config.agent[key] && typeof config.agent[key].model === 'string') {
        pass(`agent.${key} has model`);
      } else {
        fail(`agent.${key} has model`, `missing or invalid model for ${key}`);
      }
    }
  } else {
    fail('agent block present', 'missing or invalid agent block');
  }

  if (Array.isArray(config.instructions)) {
    pass('instructions array present');
  } else {
    fail('instructions array present', 'missing or invalid instructions');
  }

  if (typeof config.permission === 'object') {
    pass('permission block present');
  } else {
    fail('permission block present', 'missing or invalid permission');
  }
}

function checkAgentOverrideFiles() {
  const section = 'Agent override files';
  results.push(`\n[${section}]`);

  const agentsDir = join(ROOT, '.kilo', 'agents');
  const files = readdirSync(agentsDir).filter(f => f.endsWith('.md'));

  for (const file of files) {
    const content = readText(join(agentsDir, file));
    const yaml = parseYamlFrontmatter(content);

    if (!yaml) {
      fail(file, 'no valid YAML frontmatter');
      continue;
    }

    if (yaml.description && typeof yaml.description === 'string') {
      pass(`${file} description`);
    } else {
      fail(`${file} description`, 'missing description field');
    }

    if (yaml.mode === 'primary' || yaml.mode === 'subagent') {
      pass(`${file} mode (${yaml.mode})`);
    } else {
      fail(`${file} mode`, `expected primary or subagent, got "${yaml.mode}"`);
    }

    if (typeof yaml.steps === 'number' && yaml.steps > 0) {
      pass(`${file} steps (${yaml.steps})`);
    } else {
      fail(`${file} steps`, 'must be a positive number');
    }

    if (typeof yaml.color === 'string' && yaml.color.length > 0) {
      pass(`${file} color`);
    } else {
      fail(`${file} color`, 'missing color field');
    }
  }
}

function checkCrossReferenceAgents() {
  const section = 'Cross-reference: kilo.jsonc ↔ agents';
  results.push(`\n[${section}]`);

  const config = readJsonc(join(ROOT, 'kilo.jsonc'));
  const configAgents = Object.keys(config.agent);

  const agentsDir = join(ROOT, '.kilo', 'agents');
  const overrideFiles = readdirSync(agentsDir).filter(f => f.endsWith('.md')).map(f => f.replace('.md', ''));

  for (const name of overrideFiles) {
    if (configAgents.includes(name)) {
      pass(`${name} in both override files and config`);
    } else {
      fail(`${name}`, 'has override file but no config entry');
    }
  }

  const orphans = overrideFiles.filter(f => !configAgents.includes(f));
  if (orphans.length === 0) {
    pass('No orphaned agent override files');
  } else {
    fail('No orphaned agent override files', `found: ${orphans.join(', ')}`);
  }

  const projectAgents = configAgents.filter(a => !BUILTIN_AGENTS.includes(a));
  for (const name of projectAgents) {
    if (overrideFiles.includes(name)) {
      pass(`${name} referenced in config has override file`);
    } else {
      fail(`${name}`, 'in agent config but missing override file');
    }
  }
}

function checkAgentsMdTable() {
  const section = 'AGENTS.md agent table';
  results.push(`\n[${section}]`);

  const config = readJsonc(join(ROOT, 'kilo.jsonc'));
  const projectAgents = Object.keys(config.agent).filter(a => !BUILTIN_AGENTS.includes(a));

  const content = readText(join(ROOT, 'AGENTS.md'));
  const lines = content.split('\n');

  let inTable = false;
  let headerFound = false;
  const tableRows = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      if (!headerFound && /Agent.*Mode.*Model.*Use for/i.test(trimmed)) {
        headerFound = true;
        inTable = true;
        continue;
      }
      if (inTable && /^\|[-|\s]+\|$/.test(trimmed)) continue;
      if (inTable) {
        const cells = trimmed.split('|').map(c => c.trim()).filter(Boolean);
        if (cells.length >= 4) {
          tableRows.push({ name: cells[0], mode: cells[1], model: cells[2], purpose: cells[3] });
        }
      }
    } else if (inTable && trimmed === '') {
      inTable = false;
    }
  }

  if (tableRows.length === 0) {
    fail('Agent table parseable', 'no rows found in table');
    return;
  }

  const tableNames = tableRows.map(r => r.name);

  for (const name of projectAgents) {
    const row = tableRows.find(r => r.name === name);
    if (row) {
      pass(`${name} in table`);
      const expectedModel = config.agent[name]?.model;
      if (expectedModel && (row.model === expectedModel || expectedModel.endsWith(row.model))) {
        pass(`${name} model matches config (${expectedModel})`);
      } else {
        fail(`${name} model`, `table has "${row.model}", config has "${expectedModel}"`);
      }

      if (name === 'plan' || name === 'ask') {
        const expectedMode = 'primary';
        if (row.mode === expectedMode) {
          pass(`${name} mode is primary`);
        } else {
          fail(`${name} mode`, `expected primary, got "${row.mode}"`);
        }
      } else if (name === 'reviewer' || name === 'worker') {
        const expectedMode = 'subagent';
        if (row.mode === expectedMode) {
          pass(`${name} mode is subagent`);
        } else {
          fail(`${name} mode`, `expected subagent, got "${row.mode}"`);
        }
      }
    } else {
      fail(`${name} in table`, 'agent missing from AGENTS.md table');
    }
  }

  const extraInTable = tableNames.filter(n => !projectAgents.includes(n));
  if (extraInTable.length === 0) {
    pass('No extra entries in table');
  } else {
    fail('No extra entries in table', `found: ${extraInTable.join(', ')}`);
  }
}

function checkCommandAgentLinks() {
  const section = 'Command ↔ agent linkage';
  results.push(`\n[${section}]`);

  const config = readJsonc(join(ROOT, 'kilo.jsonc'));
  const configAgents = Object.keys(config.agent);

  const commandsDir = join(ROOT, '.kilo', 'commands');
  const files = readdirSync(commandsDir).filter(f => f.endsWith('.md'));

  for (const file of files) {
    const content = readText(join(commandsDir, file));
    const yaml = parseYamlFrontmatter(content);

    if (!yaml) {
      fail(file, 'no valid YAML frontmatter');
      continue;
    }

    if (yaml.agent && typeof yaml.agent === 'string') {
      if (configAgents.includes(yaml.agent)) {
        pass(`${file} → ${yaml.agent} (valid)`);
      } else {
        fail(`${file} → ${yaml.agent}`, 'agent not found in config');
      }
    } else {
      fail(file, 'missing agent field in frontmatter');
    }
  }
}

function checkRuleFilesExist() {
  const section = 'Rule files';
  results.push(`\n[${section}]`);

  const config = readJsonc(join(ROOT, 'kilo.jsonc'));

  if (!Array.isArray(config.instructions)) {
    fail('instructions array parseable', 'missing or invalid');
    return;
  }

  for (const relPath of config.instructions) {
    const absPath = join(ROOT, relPath);
    if (existsSync(absPath)) {
      pass(`${relPath} exists`);
    } else {
      fail(`${relPath} exists`, 'file not found');
    }
  }
}

function checkReadmeAgentCount() {
  const section = 'README agent count';
  results.push(`\n[${section}]`);

  const content = readText(join(ROOT, 'README.md'));
  const lines = content.split('\n');

  let inPhilosophy = false;
  for (const line of lines) {
    if (line.startsWith('## Philosophy')) inPhilosophy = true;
    if (inPhilosophy && line.includes('agents')) {
      if (/Four agents/.test(line)) {
        pass('Says "Four agents"');
      } else {
        fail('Says "Four agents"', `found: "${line.trim()}"`);
      }
      return;
    }
  }
  fail('"Four agents" line found', 'not found in Philosophy section');
}

function checkReadmeDirectoryTree() {
  const section = 'README directory tree';
  results.push(`\n[${section}]`);

  const content = readText(join(ROOT, 'README.md'));
  const lines = content.split('\n');

  let inTree = false;
  let inAgents = false;
  const treeAgentFiles = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed === '```' && !inTree) {
      inTree = true;
      continue;
    }
    if (trimmed === '```' && inTree) {
      inTree = false;
      break;
    }
    if (!inTree) continue;

    if (line.includes('agents/')) {
      inAgents = true;
      continue;
    }
    if (inAgents && line.includes('.md')) {
      const match = line.match(/[\w-]+\.md/);
      if (match) {
        treeAgentFiles.push(match[0]);
      }
      continue;
    }
    if (inAgents && line.includes('commands/')) {
      inAgents = false;
    }
  }

  if (treeAgentFiles.length === 0) {
    fail('Agent files found in tree', 'no agent .md files found');
    return;
  }

  const expected = ['plan.md', 'ask.md', 'reviewer.md', 'worker.md'];
  for (const file of expected) {
    if (treeAgentFiles.includes(file)) {
      pass(`${file} in tree`);
    } else {
      fail(`${file} in tree`, 'missing from directory tree');
    }
  }

  const extra = treeAgentFiles.filter(f => !expected.includes(f));
  if (extra.length === 0) {
    pass('No extra agent files in tree');
  } else {
    fail('No extra agent files in tree', `found: ${extra.join(', ')}`);
  }
}

// ── Main runner ─────────────────────────────────────────────────

console.log('\nValidating KiloTemplate...');

try {
  checkJsonConfig();
  checkAgentOverrideFiles();
  checkCrossReferenceAgents();
  checkAgentsMdTable();
  checkCommandAgentLinks();
  checkRuleFilesExist();
  checkReadmeAgentCount();
  checkReadmeDirectoryTree();
} catch (e) {
  console.error(`\n\x1b[31mFatal error: ${e.message}\x1b[0m`);
  process.exit(1);
}

for (const line of results) {
  console.log(line);
}

console.log(`\n${checksPassed} passed, ${checksFailed} failed`);

if (checksFailed > 0) {
  process.exit(1);
} else {
  console.log('\x1b[32mAll validations passed.\x1b[0m');
  process.exit(0);
}
