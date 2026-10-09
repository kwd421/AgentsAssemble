// The existing day-reserved cleanup owner drains terminal account dependencies.
// Costs are row + schema indexes; real D1 may charge more and remains authoritative.
export const ACCOUNT_CHILDREN = [
  ['devices','person_id'], ['sessions','person_id'], ['recovery_credentials','person_id'],
  ['external_identities','person_id'], ['google_handoffs','person_id'], ['servers','owner_person_id'],
  ['person_servers','person_id'], ['member_servers','person_id'], ['server_connect_grants','person_id'],
  ['server_owner_resolutions','owner_person_id'],
];
const TERMINAL = "SELECT person_id FROM persons WHERE status='disabled' AND deleted_at <= ?";
export const ACCOUNT_QUEUES = [
  ['request_nonces',`session_id IN (SELECT session_id FROM sessions WHERE person_id IN (${TERMINAL}))`,3,true],
  ['server_connect_grants',`person_id IN (${TERMINAL})`,5,true],
  ['sessions',`person_id IN (${TERMINAL})
    AND NOT EXISTS (SELECT 1 FROM request_nonces WHERE session_id=sessions.session_id)
    AND NOT EXISTS (SELECT 1 FROM server_connect_grants WHERE session_id=sessions.session_id)`,7,true],
  ['recovery_credentials',`person_id IN (${TERMINAL})`,4,true],
  ['external_identities',`person_id IN (${TERMINAL})`,4,true],
  ['google_handoffs',`person_id IN (${TERMINAL})`,3,true],
  ['devices',`person_id IN (${TERMINAL}) AND NOT EXISTS (SELECT 1 FROM sessions WHERE device_id=devices.device_id)`,3,true],
  ['person_servers',`person_id IN (${TERMINAL})`,3,true],
  ['member_servers',`person_id IN (${TERMINAL})`,6,true],
  ['server_owner_resolutions',`owner_person_id IN (${TERMINAL})`,2,true],
];

export async function purgeAccountRoots(env,day,cutoff,allowance) {
  // One unique person costs update1 + receipt-clear1 + delete(row/PK)2. No cascade.
  const count=Math.min(100,Math.floor(allowance/4));
  if(count<1)return {spent:0,queries:0};
  const absent=ACCOUNT_CHILDREN.map(([table,column])=>
    `NOT EXISTS (SELECT 1 FROM ${table} WHERE ${column}=persons.person_id)`).join(' AND ');
  const candidate=`SELECT person_id FROM persons WHERE status='disabled' AND deleted_at<=?
    AND ${absent} AND CAST(strftime('%s','now') AS INTEGER)/86400=? ORDER BY rowid LIMIT ?`;
  const ready=`SELECT person_id FROM persons WHERE purge_ready=1 AND person_id IN (${candidate})`;
  const results=await env.DB.batch([
    env.DB.prepare(`UPDATE persons SET purge_ready=1 WHERE person_id IN (${candidate})`).bind(cutoff,day,count),
    env.DB.prepare(`DELETE FROM account_deletions WHERE person_id IN (${ready})`).bind(cutoff,day,count),
    env.DB.prepare(`DELETE FROM persons WHERE person_id IN (${ready})`).bind(cutoff,day,count),
  ]);
  let spent=0;
  for(let i=0;i<results.length;i++) {
    const measured=results[i].meta?.rows_written;
    if(!Number.isSafeInteger(measured)||measured<0)throw new Error('account cleanup rows_written unavailable');
    spent+=Math.max(measured,results[0].meta.changes*(i===2?2:1));
  }
  if(spent>allowance)throw new Error('account cleanup exceeded reserved write bound');
  return {spent,queries:3};
}
