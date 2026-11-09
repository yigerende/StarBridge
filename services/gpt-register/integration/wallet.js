'use strict';
async function ledger(q, userId, reference, kind, points, frozen, note) {
  const result = await q.query('SELECT registration_post_ledger($1,$2,$3,$4::numeric,$5::numeric,$6) AS inserted',
    [userId, reference, kind, points.toFixed(2), frozen.toFixed(2), note]);
  return result.rows[0].inserted;
}
module.exports = { ledger };
