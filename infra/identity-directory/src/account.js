import { HttpError, json, parseJson } from "./http.js";

export async function logoutOtherSessions(session, env, now) {
  const result = await env.DB
    .prepare(
      `UPDATE sessions SET revoked_at = ?
       WHERE person_id = ? AND session_id != ? AND revoked_at IS NULL`
    )
    .bind(now, session.person_id, session.session_id)
    .run();
  return json({
    status: "logged_out_other_sessions",
    revoked_sessions: Number(result.meta?.changes || 0),
  });
}

export async function deleteAccount(session, env, text) {
  const body = parseJson(text);
  if (String(body.confirmation || "") !== `delete:${session.person_id}`) {
    throw new HttpError(
      400,
      "account_deletion_confirmation_required",
      "Account deletion confirmation did not match the signed-in identity."
    );
  }
  const result = await env.DB
    .prepare("DELETE FROM persons WHERE person_id = ?")
    .bind(session.person_id)
    .run();
  if (Number(result.meta?.changes || 0) !== 1) {
    throw new HttpError(404, "account_not_found");
  }
  return json({ status: "account_deleted" });
}
