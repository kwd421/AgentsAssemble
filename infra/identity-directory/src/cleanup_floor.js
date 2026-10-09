// The existing day-reserved cleanup owner owns the migration snapshot too.
// Two statements, <=100 source rows, at most 3 indexed entries per inventory
// insertion plus one singleton update. Preservation triggers cover cursor churn.
export async function cleanupFloor(env,day,allowance) {
  const count=Math.min(100,Math.floor((allowance-1)/3));
  if(count<1) return {spent:0,queries:0};
  const selected=`SELECT rowid FROM servers WHERE rowid > (SELECT scan_cursor FROM account_deletion_floor WHERE id=1)
    AND rowid <= (SELECT source_high_rowid FROM account_deletion_floor WHERE id=1) ORDER BY rowid LIMIT ?`;
  const results=await env.DB.batch([
    env.DB.prepare(`INSERT OR IGNORE INTO account_deletion_incarnations(server_id,registration_epoch,
      host_key_fingerprint,owner_person_id,host_public_key_jwk,ingress_origin,created_at,last_seen_at)
      SELECT servers.server_id,servers.registration_epoch,servers.host_key_fingerprint,servers.owner_person_id,
        servers.host_public_key_jwk,COALESCE(server_endpoints.origin,''),servers.created_at,servers.created_at
      FROM servers LEFT JOIN server_endpoints USING(server_id)
      WHERE servers.rowid IN (${selected}) AND EXISTS(SELECT 1 FROM account_deletion_floor WHERE id=1 AND closed=0)
      AND CAST(strftime('%s','now') AS INTEGER)/86400=?`).bind(count,day),
    env.DB.prepare(`UPDATE account_deletion_floor SET
      scan_cursor=COALESCE((SELECT MAX(rowid) FROM (${selected})),source_high_rowid),
      closed=CASE WHEN NOT EXISTS(SELECT 1 FROM servers WHERE rowid >
        COALESCE((SELECT MAX(rowid) FROM (${selected})),source_high_rowid)
        AND rowid<=source_high_rowid) THEN 1 ELSE 0 END,
      closure_revision=closure_revision+1
      WHERE id=1 AND closed=0 AND CAST(strftime('%s','now') AS INTEGER)/86400=?`)
      .bind(count,count,day),
  ]);
  let spent=0;
  for(let i=0;i<results.length;i++){
    const written=results[i].meta?.rows_written;
    const measured=written;
    if(!Number.isSafeInteger(measured)||measured<0) throw new Error('floor rows_written unavailable');
    spent+=Math.max(measured,results[i].meta.changes*(i===0?3:1));
  }
  if(spent>allowance) throw new Error('floor cost exceeded reservation');
  return {spent,queries:2};
}
