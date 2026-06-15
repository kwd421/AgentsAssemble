/**
 * AgentsAssemble room redirector.
 *
 * The room server runs behind a Cloudflare quick tunnel whose hostname rotates
 * on every restart. This worker is the permanent entrypoint: the server writes
 * its current tunnel URL into KV (key "target") on startup, and this worker
 * 302-redirects every request there, preserving path and query — so one stable
 * link (including /join?token=... invites) keeps working across restarts.
 */
const OFFLINE_PAGE = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>AgentsAssemble</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:system-ui;background:#0f1014;color:#f2f3f5;display:grid;place-items:center;min-height:100vh;margin:0}
main{text-align:center;padding:24px}h1{font-size:20px}p{color:#949ba4}</style></head>
<body><main><h1>방 서버가 잠시 꺼져 있어요</h1>
<p>호스트가 서버를 켜면 이 주소 그대로 다시 접속됩니다.<br>잠시 후 새로고침해 주세요.</p></main></body></html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/__health") {
      const target = await env.ROOM_TARGET.get("target");
      return Response.json({ ok: true, target: target || null });
    }
    const target = await env.ROOM_TARGET.get("target");
    if (!target) {
      return new Response(OFFLINE_PAGE, {
        status: 503,
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }
    const destination = new URL(url.pathname + url.search, target);
    return Response.redirect(destination.toString(), 302);
  },
};
