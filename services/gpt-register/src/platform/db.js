"use strict";
const { Pool, types } = require("pg");
const fs = require("node:fs");
const path = require("node:path");
const { id, passwordHash } = require("./security");
const { brandDefaults } = require("./branding");
const { features } = require("./site-controls");
const defaults = {
  features,
  custom_menus: [],
  announcements: [],
  site_name: "GPT Register",
  brand: brandDefaults,
  announcement: "",
  registration_open: true,
  login_slider_enabled: true,
  invitations: {enabled:true,stages:[{until_days:30,rate_percent:10},{until_days:60,rate_percent:5},{until_days:90,rate_percent:2}]},
  email_verify_enabled: false,
  email_domains: [],
  orders_open: false,
  api_enabled: true,
  worker_enabled: true,
  price_standard: 50,
  price_oauth: 100,
  max_quantity: 100,
  registration_concurrency: 15,
  user_registration_concurrency: 15,
  max_open_orders: 3,
  attempt_limit: 2,
  resource_retry_limit: 2,
  timeout_minutes: 20,
  template: {},
  smtp: { host: "", port: 465, secure: true, user: "", password: "", from: "" },
};
class Database {
  constructor(url) {
    this.pool = new Pool({
      connectionString: url,
      max: 12,
      connectionTimeoutMillis: 10000,
      ...(process.env.XUFU_INTEGRATION === '1' ? { types: {
        getTypeParser: (oid, format) => oid === 1700 && format !== 'binary' ? Number : types.getTypeParser(oid, format),
      } } : {}),
    });
  }
  query(sql, params = []) {
    return this.pool.query(sql, params);
  }
  async one(sql, params = []) {
    return (await this.query(sql, params)).rows[0];
  }
  async tx(fn) {
    const c = await this.pool.connect();
    try {
      await c.query("BEGIN");
      const result = await fn(c);
      await c.query("COMMIT");
      return result;
    } catch (e) {
      await c.query("ROLLBACK");
      throw e;
    } finally {
      c.release();
    }
  }
  async initialize(credentials) {
    await this.tx(async (c) => {
      await c.query("SELECT pg_advisory_xact_lock(8963001)");
      await c.query(
        fs.readFileSync(path.join(__dirname, "schema.sql"), "utf8"),
      );
      await c.query(fs.readFileSync(path.join(__dirname, "risk-schema.sql"), "utf8"));
      await c.query(fs.readFileSync(path.join(__dirname, "session-proxy-schema.sql"), "utf8"));
      await c.query(
        "INSERT INTO platform_settings(id,value) VALUES(1,$1) ON CONFLICT DO NOTHING",
        [defaults],
      );
      await c.query("UPDATE platform_settings SET value=value || $1::jsonb WHERE id=1 AND NOT value ? 'invitations'", [{invitations:defaults.invitations}]);
      await c.query("UPDATE platform_settings SET value=value || '{\"login_slider_enabled\":true}' WHERE id=1 AND NOT value ? 'login_slider_enabled'");
      if (
        process.env.XUFU_INTEGRATION !== '1' && credentials &&
        !(
          await c.query(
            "SELECT id FROM platform_users WHERE role='admin' LIMIT 1",
          )
        ).rows.length
      ) {
        await c.query(
          "INSERT INTO platform_users(id,email,name,password_hash,role) VALUES($1,$2,$3,$4,'admin')",
          [
            id(),
            credentials.username.toLowerCase(),
            "管理员",
            await passwordHash(credentials.password),
          ],
        );
      }
      if (process.env.XUFU_INTEGRATION === '1') {
        await c.query(fs.readFileSync(path.join(__dirname, '../../integration/schema.sql'), 'utf8'));
        await c.query(fs.readFileSync(path.join(__dirname, '../../integration/telemetry.sql'), 'utf8'));
      } else {
        await c.query('CREATE OR REPLACE VIEW registration_members AS SELECT * FROM platform_users');
      }
    });
  }
  async settings() {
    const value = (
      await this.one("SELECT value FROM platform_settings WHERE id=1")
    ).value;
    if (process.env.XUFU_INTEGRATION === '1') {
      const rows=(await this.query("SELECT key,value FROM settings WHERE key IN ('features','invites','site','security')")).rows;
      const unified=Object.fromEntries(rows.map(row=>[row.key,row.value]));
      value.site_name=unified.site?.name || '星桥';
      value.registration_open=unified.security?.registration_open!==false;
      value.login_slider_enabled=unified.security?.login_slider_enabled!==false;
      value.email_verify_enabled=unified.security?.email_verify_enabled===true;
      value.email_domains=unified.security?.email_domain_whitelist||[];
      value.announcement='';
      value.announcements=(await this.query('SELECT id,title,body,tone,published AS enabled,login_visible,sort,starts_at,ends_at FROM announcements')).rows;
      value.brand=require('../../integration/branding').unifiedBrand(unified.site);
      value.invitations=unified.invites || {enabled:false,stages:[]};
      value.features={...value.features};
      for(const key of ['home','wallet','keys','docs'])value.features[key]=unified.features?.[key]!==false;
      value.features.partners=unified.features?.invites!==false && value.invitations.enabled===true;
    }
    return {
      ...defaults,
      ...value,
      brand: { ...brandDefaults, ...value.brand },
      features: { ...features, ...value.features },
    };
  }
  async limit(key, max, seconds = 60) {
    const r = await this.one(
      "INSERT INTO platform_rate_limits(key,hits,expires_at) VALUES($1,1,now()+$2*interval '1 second') ON CONFLICT(key) DO UPDATE SET hits=CASE WHEN platform_rate_limits.expires_at<now() THEN 1 ELSE platform_rate_limits.hits+1 END,expires_at=CASE WHEN platform_rate_limits.expires_at<now() THEN now()+$2*interval '1 second' ELSE platform_rate_limits.expires_at END RETURNING hits",
      [key, seconds],
    );
    return r.hits <= max;
  }
  close() {
    return this.pool.end();
  }
}
const audit = (q, actor, action, target = "", detail = "") =>
  q.query(
    "INSERT INTO platform_audit(actor_id,action,target,detail) VALUES($1,$2,$3,$4)",
    [actor, action, target, detail],
  );
module.exports = { Database, defaults, audit };
