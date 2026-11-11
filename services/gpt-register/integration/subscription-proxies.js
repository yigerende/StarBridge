'use strict';
// Reuse the registration catalog/providers without sharing their resources or
// changing the registration engine. Published entries and claims live in PG.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { ProxyService } = require('../src/ui-server/proxy-service');
const { flattenedPoolEntries } = require('../src/proxy-catalog');
const { pageRows } = require('../src/ui-server/pagination');
const { fail } = require('../src/platform/security');
const LOCK = 1886018;

function encryptEndpoint(value) {
  const nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(process.env.MASTER_KEY, 'base64'), nonce);
  return Buffer.concat([nonce, cipher.update(value, 'utf8'), cipher.final(), cipher.getAuthTag()]).toString('base64');
}

function createSubscriptionProxies(db, cwd, options = {}) {
  const root = path.join(cwd, '.runtime', 'subscription-country-proxies');
  fs.mkdirSync(path.join(root, '.runtime'), { recursive: true, mode: 0o700 });
  let usage = new Map();
  const service = new ProxyService({
    cwd: root, filePath: path.join(root, 'config.json'),
    readRaw: () => ({ proxyPool: { defaultProtocol: 'http' } }),
    proxyFilePath: () => path.join(root, 'unused-local-proxies.txt'),
    proxyCatalogPath: () => path.join(root, 'catalog.json'),
  }, { ...options, usage: {
    get: id => usage.get(id) || { inUse: false, cooling: false, lastUsedAt: '', releasedAt: '', cooldownUntil: '' },
    isCooling: id => Boolean(usage.get(id)?.cooling),
  } });
  let active = null, flight = null;
  const jobs = new Map();
  async function loadUsage(q = db) {
    const rows = (await q.query('SELECT * FROM subscription_country_usage')).rows;
    const stamp = value => value ? new Date(value).toISOString() : '';
    usage = new Map(rows.map(r => [r.id, { inUse: r.in_use, lastUsedAt: stamp(r.last_used_at),
      releasedAt: stamp(r.released_at), cooldownUntil: stamp(r.cooldown_until),
      cooling: Boolean(r.cooldown_until && new Date(r.cooldown_until).getTime() > Date.now()) }]));
  }
  async function publish(q) {
    const rows = [];
    for (const pool of Object.values(service.catalogSnapshot().countryPools)) {
      const sources = new Map(pool.sources.map(s => [s.id, s]));
      for (const entry of flattenedPoolEntries(pool)) {
        const source = sources.get(entry.sourceId);
        rows.push({ country: pool.countryCode, id: entry.id, endpoint_enc: encryptEndpoint(entry.url),
          session_minutes: source?.lastPulledSettings?.sessionMinutes ?? source?.sessionMinutes ?? 0 });
      }
    }
    await q.query('DELETE FROM subscription_country_entries');
    if (rows.length) await q.query(`INSERT INTO subscription_country_entries(country,id,endpoint_enc,session_minutes)
      SELECT country,id,endpoint_enc,session_minutes FROM jsonb_to_recordset($1::jsonb)
      AS x(country text,id text,endpoint_enc text,session_minutes integer)`, [JSON.stringify(rows)]);
  }
  async function initialize() {
    await db.tx(async q => {
      await q.query('SELECT pg_advisory_xact_lock($1)', [LOCK]);
      await q.query('SELECT subscription_country_reap()');
      await loadUsage(q);
      await publish(q);
    });
  }
  function start(action, actor, operation, catalogMutation = true) {
    fail(!active, '国家代理正在更新或检测，请稍后重试', 409);
    const job = { id: crypto.randomUUID(), state: 'running', action };
    active = job; jobs.set(job.id, job);
    // Keep only bounded, recent results. Neither bodies nor errors enter logs.
    for (const id of [...jobs.keys()].slice(0, -20)) jobs.delete(id);
    const perform = async () => {
      if (!catalogMutation) {
        const result = await operation();
        await service.inventory.flight;
        if (actor) await db.query("INSERT INTO audit_events(actor_id,action,target,detail,ip) VALUES($1,$2,'subscription-country-proxies','操作完成','')", [actor, 'national_proxy.' + action]);
        return { result };
      }
      return db.tx(async q => {
      fail((await q.query('SELECT pg_try_advisory_xact_lock($1) AS ok', [LOCK])).rows[0].ok, '代理正在分配，请稍后重试', 409);
      await q.query('SELECT subscription_country_reap()');
      await loadUsage(q);
      let result, error;
      try {
        result = await operation();
        await service.settingsRefreshFlight;
        await service.inventory.flight;
      } catch (e) { error = e; }
      // Provider operations may save partial progress. Always publish the real
      // final catalog; a restart rebuilds the same inventory if PG is unavailable.
      await publish(q);
      if (actor) await q.query("INSERT INTO audit_events(actor_id,action,target,detail,ip) VALUES($1,$2,'subscription-country-proxies',$3,'')",
        [actor, 'national_proxy.' + action, error ? '操作未完成' : '操作完成']);
      return { result, error };
      });
    };
    flight = perform().then(({ result, error }) => {
      if (error) throw error;
      Object.assign(job, { state: 'completed', result });
    }).catch(e => {
      Object.assign(job, { state: 'failed', error: e.statusCode && e.statusCode < 500 ? e.message : '国家代理操作未完成，请刷新后重试', status: e.statusCode || 500 });
    }).finally(() => { active = null; flight = null; });
    return { operation: job.id };
  }
  async function snapshot() {
    if (!active) await loadUsage();
    const result = service.list();
    result.operation = active ? { id: active.id, state: active.state, action: active.action } : null;
    result.settings = (await db.query("SELECT value FROM settings WHERE key='national_pool'")).rows[0]?.value || { renewal_country: 'PH' };
    result.dynamic.countryPools = result.dynamic.countryPools.map(p => ({ ...p, entries: [], sources: p.sources.map(s => ({ ...s, entries: [] })) }));
    return result;
  }
  return { service, initialize, start, snapshot, jobs, loadUsage,
    async close() { await service.shutdown(); await flight; } };
}

function registerSubscriptionProxies(app, db, cwd) {
  const pool = createSubscriptionProxies(db, cwd);
  const base = '/api/admin/v1/subscription-proxies';
  app.addHook('onReady', () => pool.initialize());
  app.addHook('preClose', () => pool.close());
  app.get(base, () => pool.snapshot());
  app.get(base + '/operations/:id', r => {
    const job = pool.jobs.get(r.params.id); fail(job, '操作记录已过期，请刷新代理列表', 404); return job;
  });
  app.get(base + '/pools/:country/entries', r => {
    const p = pool.service.countryPools().find(p => p.countryCode === r.params.country);
    return pageRows((p?.entries || []).filter(e => e.sourceId === 'manual'), r.query);
  });
  app.get(base + '/inventory', async r => { await pool.snapshot(); return pool.service.inventory.list(r.query); });
  app.post(base + '/preview', r => pool.service.preview({ ...r.body, scope: 'dynamic' }));
  const mutation = (method, suffix, action, fn) => app.route({ method, url: base + suffix,
    handler: r => pool.start(action, r.user.xufu_user_id, () => fn(r)) });
  mutation('POST', '/import', 'import', r => pool.service.import({ ...r.body, scope: 'dynamic' }));
  mutation('POST', '/api/build-pull', 'build', r => pool.service.buildAndPullApi(r.body));
  mutation('PUT', '/provider-settings', 'provider', r => pool.service.saveProviderSettings(r.body));
  mutation('POST', '/sources/pull-all', 'refresh_all', r => pool.service.pullAll(r.body));
  mutation('POST', '/sources/:id/pull', 'refresh', r => pool.service.pull(r.params.id, r.body));
  mutation('DELETE', '/dynamic', 'delete', r => pool.service.removeDynamic(r.body));
  mutation('DELETE', '', 'clear', () => pool.service.clear({ scope: 'dynamic' }));
  app.post(base + '/inventory/test', r => pool.start('test', r.user.xufu_user_id,
    () => pool.service.inventory.start({ ...r.body, filters: { ...r.body?.filters, scope: 'country' } }), false));
  app.put(base + '/settings', async r => {
    const code = r.body?.renewal_country;
    fail(typeof code === 'string' && /^[A-Z]{2}$/.test(code), '请选择独立续费默认国家', 400);
    await db.tx(async q => {
      await q.query('SELECT pg_advisory_xact_lock($1)', [LOCK]);
      const mode = (await q.query("SELECT value->>'mode' mode FROM settings WHERE key='proxy'")).rows[0]?.mode;
      if (mode === 'national_pool') fail((await q.query("SELECT 1 FROM subscription_country_entries WHERE country IN ($1,'GLOBAL') LIMIT 1", [code])).rowCount, '该国家暂无可用代理', 400);
      await q.query("INSERT INTO settings(key,value) VALUES('national_pool',$1) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [{ renewal_country: code }]);
      await q.query("INSERT INTO audit_events(actor_id,action,target,detail,ip) VALUES($1,'national_proxy.settings','subscription-country-proxies',$2,'')", [r.user.xufu_user_id, '独立续费国家：' + code]);
    });
    return { ok: true };
  });
  return pool;
}
module.exports = { createSubscriptionProxies, registerSubscriptionProxies, encryptEndpoint };
