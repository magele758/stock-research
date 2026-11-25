#!/usr/bin/env node
// Zero-intrusion Agent Home registration for Pro-Quant V2.0.
//
// Creates / verifies the permanent `quant-lead` Bot via the Agent Home daemon
// REST API (GET/POST/PATCH /api/bots) and prints the exact env snippet and
// Skill path the operator must add on the Agent Home side. It never edits the
// Agent Home repository. Node >= 22, native ESM, no npm dependencies.
//
//   node scripts/register_agent_home.mjs [--dry-run] [--daemon-url URL]
//        [--mcp-url URL] [--bearer-env NAME] [--json]
//
// Env: AGENT_HOME_DAEMON_URL, RAW_AGENT_AUTH_TOKEN, QUANT_MCP_URL

import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..');
const SKILL_PATH = resolve(REPO_ROOT, 'services/quant-mcp/skills/quant-lead/SKILL.md');
const PORTS_FILE =
  process.env.AGENT_HOME_PORTS_FILE ||
  resolve(REPO_ROOT, '../ppeng-agent-core/.agent-state/dev-lab.ports.json');

const BOT_ID = 'quant-lead';
const BOT_TITLE = '量化主研 Pro-Quant';
// Agent Home caps description at 2000 chars (BOT_DESCRIPTION_MAX).
const BOT_DESCRIPTION = [
  '你是永久量化主研助手 quant-lead，服务 A 股/美股机构级量化研究。',
  '职责：通过 quant_* MCP 工具做因子检验、CPCV/DSR 过拟合门禁、Barra CNE6 风险暴露、cvxpy 组合优化、A 股微结构回测归因与影子盘漂移监控。',
  '铁律：(1) 只引用工具返回的数值，结论必须带 snapshot_id 与 as_of，不得凭记忆编造指标；(2) 零荐股：只输出统计学指标、因子暴露、样本外验证与漂移分析，绝不给出买卖建议或收益承诺，系统无任何下单能力；',
  '(3) 耗时工具(quant_run_backtest/quant_run_cpcv_gate/quant_submit_factor_hypothesis)优先 async_mode=true，拿到 job_id 后用 wait_for 或轮询，禁止同步阻塞超过 20s；(4) 工具大结果只看摘要与 artifact uri，不要要求灌入全表。',
  '防重复试错记忆约定：每次因子被 CPCV 门禁判 reject/inconclusive 或被相关性淘汰，立即写入 project.memory 的「已证伪因子墓碑库」，格式 `[FALSIFIED] factor_id | expr_hash | 原因 | DSR/PBO/OOS-IS | snapshot_id | 日期`；提出新假设前先检索该墓碑库与 quant_list_experiments，同质表达式不得重复提交。',
  '用户偏好（股票池、换手上限、行业/风格偏离容忍度）写入 user.memory；当前研究主线写 session.long。',
  `详细 Runbook 见 Skill: ${SKILL_PATH}`,
].join('\n');

function parseArgs(argv) {
  const out = { dryRun: false, json: false, daemonUrl: null, mcpUrl: null, bearerEnv: null, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === '--dry-run') out.dryRun = true;
    else if (a === '--json') out.json = true;
    else if (a === '--daemon-url') out.daemonUrl = next();
    else if (a === '--mcp-url') out.mcpUrl = next();
    else if (a === '--bearer-env') out.bearerEnv = next();
    else if (a === '-h' || a === '--help') out.help = true;
    else {
      console.error(`unknown argument: ${a}`);
      process.exit(2);
    }
  }
  return out;
}

const clean = (u) => String(u).trim().replace(/\/+$/, '');

function readPortsFile() {
  if (!existsSync(PORTS_FILE)) return null;
  try {
    const j = JSON.parse(readFileSync(PORTS_FILE, 'utf8'));
    const url = j.daemonUrl || j.proxyTarget || (j.daemonPort ? `http://127.0.0.1:${j.daemonPort}` : null);
    return url ? clean(url) : null;
  } catch {
    return null;
  }
}

/** Ordered candidate list: env -> dev-lab.ports.json -> 27070 -> 37070. */
function daemonCandidates(explicit) {
  const list = [];
  const push = (source, url) => {
    if (url && !list.some((c) => c.url === clean(url))) list.push({ source, url: clean(url) });
  };
  push('--daemon-url', explicit);
  push('AGENT_HOME_DAEMON_URL', process.env.AGENT_HOME_DAEMON_URL);
  push('dev-lab.ports.json', readPortsFile());
  push('default:27070', 'http://127.0.0.1:27070');
  push('default:37070', 'http://127.0.0.1:37070');
  return list;
}

function headers(token) {
  const h = { 'content-type': 'application/json', accept: 'application/json' };
  if (token) h.authorization = `Bearer ${token}`;
  return h;
}

async function http(method, url, token, body, timeoutMs = 4000) {
  const res = await fetch(url, {
    method,
    headers: headers(token),
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  return { status: res.status, ok: res.ok, data };
}

async function pickDaemon(candidates, token) {
  for (const c of candidates) {
    try {
      const r = await http('GET', `${c.url}/api/health`, token, undefined, 1500);
      if (r.status < 500) return { ...c, reachable: true, health: r.status };
    } catch {
      /* try next */
    }
  }
  return null;
}

function buildEnvSnippet(mcpUrl, bearerEnv) {
  const entry = { url: mcpUrl, transport: 'streamable-http' };
  if (bearerEnv) entry.bearerEnv = bearerEnv;
  const mcpHttp = JSON.stringify([entry]);
  return {
    RAW_AGENT_MCP_EXPAND_HTTP: 'true',
    RAW_AGENT_MCP_HTTP: mcpHttp,
    dotenv: `RAW_AGENT_MCP_EXPAND_HTTP=true\nRAW_AGENT_MCP_HTTP='${mcpHttp}'`,
    stdioAlternative: JSON.stringify([
      {
        command: `${REPO_ROOT}/.venv/bin/python`,
        args: ['-m', 'quant_mcp.server', '--transport', 'stdio'],
        cwd: REPO_ROOT,
        env: {
          PYTHONPATH: ['packages/quant-core/src', 'services/quant-api/src', 'services/quant-mcp/src']
            .map((p) => resolve(REPO_ROOT, p))
            .join(':'),
        },
      },
    ]),
  };
}

const createPayload = () => ({ name: BOT_ID, title: BOT_TITLE, description: BOT_DESCRIPTION });

async function registerBot(daemonUrl, token) {
  const list = await http('GET', `${daemonUrl}/api/bots?includeHidden=1`, token);
  if (list.status === 401 || list.status === 403) {
    throw new Error(`daemon rejected credentials (HTTP ${list.status}); set RAW_AGENT_AUTH_TOKEN`);
  }
  if (!list.ok) throw new Error(`GET /api/bots failed: HTTP ${list.status} ${JSON.stringify(list.data)}`);
  const bots = Array.isArray(list.data?.bots) ? list.data.bots : [];
  const existing = bots.find((b) => b.id === BOT_ID || b.name === BOT_ID);
  if (!existing) {
    const created = await http('POST', `${daemonUrl}/api/bots`, token, createPayload());
    if (!created.ok) throw new Error(`POST /api/bots failed: HTTP ${created.status} ${JSON.stringify(created.data)}`);
    return { action: 'created', bot: created.data?.bot };
  }
  const patch = {};
  if (existing.hidden) patch.hidden = false;
  if ((existing.description ?? '') !== BOT_DESCRIPTION) patch.description = BOT_DESCRIPTION;
  if (!Object.keys(patch).length) return { action: 'verified', bot: existing };
  const updated = await http('PATCH', `${daemonUrl}/api/bots/${encodeURIComponent(existing.id)}`, token, patch);
  if (!updated.ok) throw new Error(`PATCH /api/bots/${existing.id} failed: HTTP ${updated.status} ${JSON.stringify(updated.data)}`);
  return { action: `updated(${Object.keys(patch).join(',')})`, bot: updated.data?.bot ?? existing };
}

function printGuide(report) {
  const lines = [
    '',
    '== Add to Agent Home .env (then restart the daemon) ==',
    report.env.dotenv,
    '',
    `Quant MCP Bearer: export ${report.bearerEnv}=<token> on BOTH the quant-mcp process (as QUANT_MCP_BEARER_TOKEN) and the Agent Home daemon.`,
    '',
    '== Skill ==',
    `quant-lead Runbook: ${SKILL_PATH}${existsSync(SKILL_PATH) ? '' : '  (missing!)'}`,
    'Copy/symlink the directory into an Agent Home skills root (e.g. ~/.agents/skills/quant-lead) so skill routing can load it.',
    '',
    '== Stdio alternative (RAW_AGENT_MCP_STDIO) ==',
    report.env.stdioAlternative,
    '',
    '== Memory convention ==',
    'Falsified factors -> project.memory: [FALSIFIED] factor_id | expr_hash | reason | DSR/PBO/OOS-IS | snapshot_id | date',
  ];
  console.log(lines.join('\n'));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('usage: node scripts/register_agent_home.mjs [--dry-run] [--daemon-url URL] [--mcp-url URL] [--bearer-env NAME] [--json]');
    return;
  }
  const token = (process.env.RAW_AGENT_AUTH_TOKEN || '').trim();
  const mcpUrl = clean(args.mcpUrl || process.env.QUANT_MCP_URL || 'http://127.0.0.1:8101/mcp');
  const bearerEnv = args.bearerEnv || 'QUANT_MCP_BEARER_TOKEN';
  const candidates = daemonCandidates(args.daemonUrl);
  const env = buildEnvSnippet(mcpUrl, bearerEnv);

  const report = {
    mode: args.dryRun ? 'dry-run' : 'live',
    botId: BOT_ID,
    skillPath: SKILL_PATH,
    skillExists: existsSync(SKILL_PATH),
    authTokenPresent: Boolean(token),
    daemonCandidates: candidates,
    bearerEnv,
    env,
  };

  if (args.dryRun) {
    const first = candidates[0];
    report.plannedRequests = [
      { method: 'GET', url: `${first.url}/api/bots?includeHidden=1`, headers: { authorization: token ? 'Bearer ***' : '(none)' } },
      { method: 'POST', url: `${first.url}/api/bots`, body: createPayload(), when: `no bot with id/name "${BOT_ID}"` },
      { method: 'PATCH', url: `${first.url}/api/bots/${BOT_ID}`, body: { description: '<persona>', hidden: false }, when: 'bot exists but persona/hidden differs' },
    ];
    if (args.json) {
      console.log(JSON.stringify(report, null, 2));
    } else {
      console.log('[dry-run] Agent Home daemon candidates (first reachable wins in live mode):');
      candidates.forEach((c, i) => console.log(`  ${i + 1}. ${c.url}  <- ${c.source}`));
      console.log(`[dry-run] auth token: ${token ? 'RAW_AGENT_AUTH_TOKEN present' : 'not set (optional)'}`);
      console.log('[dry-run] planned requests:');
      console.log(JSON.stringify(report.plannedRequests, null, 2));
      console.log('[dry-run] bot payload:');
      console.log(JSON.stringify(createPayload(), null, 2));
      printGuide(report);
    }
    if (!report.skillExists) {
      console.error(`warning: skill file not found at ${SKILL_PATH}`);
    }
    return;
  }

  const daemon = await pickDaemon(candidates, token);
  if (!daemon) {
    console.error('Agent Home daemon not reachable. Tried:');
    candidates.forEach((c) => console.error(`  - ${c.url} (${c.source})`));
    console.error('Start it (e.g. `npm run dev:lab` in ppeng-agent-core) or set AGENT_HOME_DAEMON_URL. Use --dry-run to inspect the payload offline.');
    process.exit(1);
  }
  report.daemon = daemon;
  try {
    const result = await registerBot(daemon.url, token);
    report.bot = { action: result.action, id: result.bot?.id, name: result.bot?.name, canonicalSessionId: result.bot?.canonicalSessionId };
  } catch (err) {
    console.error(`registration failed: ${err.message}`);
    process.exit(1);
  }
  if (args.json) {
    console.log(JSON.stringify(report, null, 2));
  } else {
    console.log(`daemon: ${daemon.url} (${daemon.source})`);
    console.log(`bot ${BOT_ID}: ${report.bot.action} (id=${report.bot.id}, session=${report.bot.canonicalSessionId})`);
    printGuide(report);
  }
}

// Only run when executed directly (importing this module must have no side effects).
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    console.error(err?.stack || String(err));
    process.exit(1);
  });
}
