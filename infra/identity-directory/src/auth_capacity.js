// AUTH source accounting shares the existing expiring rate_limits queue. Its
// own rows cost three cleanup units too; SQL triggers enforce both daily caps.
// Keep the source reservations and the protected insert in one D1 transaction.
export async function authWrite(db, source, statement, cost, rate = {}) {
  if (!source?.ip) throw new Error("AUTH source is required");
  const prefix = source.purpose === "AUTH" ? "" : "anonymous:";
  const person = source.personId ? `${prefix}auth-person:${source.personId}` : "";
  const ip = `${prefix}${source.ip}`;
  const reservation = await db.prepare(`WITH clock AS (
      SELECT CAST(strftime('%s', 'now') AS INTEGER) / 86400 * 86400 AS day
    ) SELECT day,
      CASE WHEN ? <> '' AND EXISTS (SELECT 1 FROM rate_limits WHERE bucket = ? AND window_start = ?)
        THEN 0 ELSE ? END
      + CASE WHEN EXISTS (SELECT 1 FROM rate_limits WHERE bucket = ? AND window_start = day) THEN 0 ELSE 3 END
      + CASE WHEN ? = '' OR EXISTS (SELECT 1 FROM rate_limits WHERE bucket = ? AND window_start = day) THEN 0 ELSE 3 END
      AS debt FROM clock`)
    .bind(rate.bucket || "", rate.bucket || "", rate.windowStart || 0, cost, ip, person, person).first();
  // Concurrent first use may conservatively reserve the same row's debt twice
  // in a source cap. The shared pool charges only actual INSERTs. A midnight
  // race fails closed in the source trigger and can be retried in the new day.
  const reservations = [person, ip].filter(Boolean).map(bucket => db.prepare(
    `INSERT INTO rate_limits (bucket, window_start, count) VALUES (?, ?, ?)
     ON CONFLICT(bucket, window_start) DO UPDATE SET count = rate_limits.count + excluded.count`
  ).bind(bucket, reservation.day, reservation.debt));
  // Recheck the day after the protected write as well: a batch crossing midnight
  // must not charge yesterday's source allowance against today's shared pool.
  const dayGuard = db.prepare(`UPDATE rate_limits SET count = count
    WHERE bucket = ? AND window_start = ?`).bind(ip, reservation.day);
  const writes = Array.isArray(statement) ? statement : [statement];
  const results = await db.batch([...reservations, ...writes, dayGuard]);
  return results.at(-2);
}
