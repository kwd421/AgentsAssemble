import { escapeHtml, roleMeta, state } from "./shared.js";

export function renderArchive(payload) {
  const archive = document.querySelector("#archive");
  const entries = buildArchiveEntries(payload);
  if (!entries[state.archiveKey]) state.archiveKey = Object.keys(entries)[0];
  const currentDocument = entries[state.archiveKey] || "";
  const manifest = buildArchiveManifest(payload, entries);
  archive.innerHTML = `
    <section class="archive-view">
    <div class="room-strip">
      <div>
        <strong>아카이브</strong>
        <small>회의 산출물, 인수인계 기록, 에이전트별 자료를 장기 기록으로 보관합니다.</small>
      </div>
      <div class="room-actions">
        <span class="room-status">${escapeHtml(Object.keys(entries).length)}개 문서</span>
        <span class="room-status room-status-hot">${escapeHtml(archiveKindLabel(state.archiveKey))}</span>
      </div>
    </div>
    <section class="archive-vault">
      <div class="archive-vault-copy">
        <span class="room-kicker">record vault</span>
        <strong>${escapeHtml(archiveOwnerLabel(state.archiveKey, payload))}</strong>
        <p>공식 결정, 회의록, 리서치, 복귀 패킷을 분리해서 보관합니다. 에이전트가 나중에 돌아와도 어떤 자료를 근거로 움직여야 하는지 추적할 수 있어야 합니다.</p>
      </div>
      <div class="archive-vault-stats">
        ${renderArchiveStat("공용", manifest.publicCount)}
        ${renderArchiveStat("역할별", manifest.roleCount)}
        ${renderArchiveStat("리서치", manifest.researchCount)}
        ${renderArchiveStat("복귀", manifest.returnCount)}
      </div>
    </section>
    <div class="archive-layout">
      <aside class="archive-list">
        <div class="archive-head">
          <strong>문서 목록</strong>
          <span>소유자와 문서 유형별로 분리됩니다.</span>
        </div>
        ${renderArchiveGroups(payload, entries)}
      </aside>
      <section class="archive-document">
        <div class="archive-document-head">
          <div>
            <strong>${escapeHtml(state.archiveKey || "문서")}</strong>
            <small>${escapeHtml(archiveOwnerLabel(state.archiveKey, payload))} · ${escapeHtml(documentStat(currentDocument))}</small>
          </div>
          <div class="archive-actions">
            <span>${escapeHtml(archiveOwnerLabel(state.archiveKey, payload))}</span>
            <span>${escapeHtml(archiveKindLabel(state.archiveKey))}</span>
            <button type="button" data-archive-command="copy">복사</button>
            <button type="button" data-archive-command="download">내보내기</button>
          </div>
        </div>
        <pre class="archive-preview">${escapeHtml(currentDocument)}</pre>
      </section>
    </div>
    </section>
  `;
  archive.querySelectorAll("[data-archive]").forEach((button) => {
    button.addEventListener("click", () => {
      state.archiveKey = button.dataset.archive;
      renderArchive(payload);
    });
  });
  archive.querySelectorAll("[data-archive-command]").forEach((button) => {
    button.addEventListener("click", () => handleArchiveCommand(button.dataset.archiveCommand, state.archiveKey, currentDocument, button));
  });
}

function buildArchiveManifest(payload, entries) {
  const keys = Object.keys(entries);
  const roleIds = (payload.meeting.roles || []).map((role) => role.id);
  return {
    publicCount: keys.filter((key) => !key.includes("/")).length,
    roleCount: keys.filter((key) => roleIds.some((roleId) => key.includes(`/${roleId}/`) || key.endsWith(`/${roleId}.md`))).length,
    researchCount: keys.filter((key) => key.includes("research/")).length,
    returnCount: keys.filter((key) => key.includes("return_packets/")).length,
  };
}

function renderArchiveStat(label, value) {
  return `<div class="archive-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? 0)}</strong></div>`;
}

function documentStat(value) {
  const text = String(value || "");
  const lines = text ? text.split("\n").length : 0;
  return `${lines} lines · ${text.length} chars`;
}

async function handleArchiveCommand(command, key, content, button) {
  if (command === "copy") {
    const copied = await copyText(content);
    showArchiveCommandFeedback(button, copied ? "복사됨" : "복사 실패", copied ? "success" : "error");
    return;
  }
  if (command === "download") {
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = archiveDownloadName(key);
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showArchiveCommandFeedback(button, "내보냄", "success");
  }
}

async function copyText(content) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(content);
      return true;
    } catch {
      return copyTextWithTextarea(content);
    }
  }
  return copyTextWithTextarea(content);
}

function copyTextWithTextarea(content) {
  const textarea = document.createElement("textarea");
  textarea.value = content;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand?.("copy") || false;
  textarea.remove();
  return copied;
}

function showArchiveCommandFeedback(button, label, tone = "success") {
  if (!button) return;
  const original = button.textContent;
  button.textContent = label;
  button.classList.add(tone === "error" ? "is-error" : "is-confirmed");
  setTimeout(() => {
    button.textContent = original;
    button.classList.remove("is-confirmed", "is-error");
  }, 1400);
}

function archiveDownloadName(key) {
  return String(key || "archive.md").split("/").pop() || "archive.md";
}

function archiveKindLabel(key) {
  if (!key) return "기록";
  if (key.includes("evidence/")) return "근거 표";
  if (key.includes("research/")) return "리서치";
  if (key.includes("tasks/")) return "작업 배정";
  if (key.includes("return_packets/")) return "세션 복귀";
  if (key === "decision.md") return "결정";
  if (key === "transcript.md") return "회의록";
  if (key === "agenda.md") return "안건";
  return "기록";
}

function archiveOwnerLabel(key, payload) {
  if (!key) return "공용 기록";
  const roles = payload?.meeting?.roles || [];
  const role = roles.find((candidate) => key.includes(`/${candidate.id}/`) || key.endsWith(`/${candidate.id}.md`));
  if (role) return role.display_name;
  return "공용 기록";
}

function buildArchiveEntries(payload) {
  return {
    ...payload.artifacts,
    ...Object.fromEntries(Object.entries(payload.tasks).map(([key, value]) => [`tasks/${key}`, value])),
    ...Object.fromEntries(Object.entries(payload.return_packets || {}).map(([key, value]) => [`return_packets/${key}`, value])),
    ...Object.fromEntries(Object.entries(payload.research).map(([key, value]) => [`research/${key}`, value])),
    ...buildEvidenceArchiveEntries(payload),
  };
}

function buildEvidenceArchiveEntries(payload) {
  return Object.fromEntries(
    Object.entries(payload.research_json || {})
      .filter(([, research]) => research && typeof research === "object")
      .map(([roleId, research]) => [`evidence/${roleId}.md`, renderEvidenceArchiveMarkdown(roleId, research, payload)])
  );
}

function renderEvidenceArchiveMarkdown(roleId, research, payload) {
  const role = (payload.meeting.roles || []).find((candidate) => candidate.id === roleId);
  const gate = research.evidence_gate || {};
  return [
    `# Evidence Table: ${role?.display_name || roleId}`,
    "",
    `Status: ${gate.status || "unknown"}`,
    `Sources: ${gate.source_count || 0}`,
    `Confidence: ${gate.confidence_after || research.confidence || "unknown"}`,
    "",
    "## Counts",
    "",
    `- Supported: ${gate.supported_claim_count || 0}`,
    `- Weak: ${gate.weak_claim_count || 0}`,
    `- Unsupported: ${gate.unsupported_claim_count || 0}`,
    `- Verifier rejected: ${gate.verifier_rejected_claim_count || 0}`,
    "",
    renderEvidenceArchiveSection("Supported Claims", research.claim_evidence || []),
    renderEvidenceArchiveSection("Weak Claims", research.weak_claims || []),
    renderEvidenceArchiveSection("Unsupported Claims", research.unsupported_claims || []),
    renderEvidenceArchiveSection("Verifier Rejected Claims", research.verifier_rejected_claims || []),
  ].join("\n");
}

function renderEvidenceArchiveSection(title, claims) {
  if (!claims.length) return `## ${title}\n\nNone.\n`;
  return [
    `## ${title}`,
    "",
    "| Claim | Reason / Interpretation | Sources |",
    "| --- | --- | --- |",
    ...claims.map((claim) => {
      const urls = claim.evidence || claim.sources || [];
      return `| ${tableCell(claim.claim)} | ${tableCell(claim.reason || claim.interpretation || claim.why_it_matters)} | ${tableCell(urls.join("<br>") || "출처 없음")} |`;
    }),
    "",
  ].join("\n");
}

function tableCell(value) {
  return String(value || "")
    .replaceAll("|", "\\|")
    .replaceAll("\n", "<br>");
}

function renderArchiveGroups(payload, entries) {
  const publicKeys = ["agenda.md", "transcript.md", "decision.md", "meeting.json"].filter((key) => key in entries);
  const roleGroups = (payload.meeting.roles || []).map((role) => {
    const meta = roleMeta[role.id] || { color: "purple", title: role.lens, badge: role.lens, avatar: "/static/avatar-moderator.svg" };
    const keys = Object.keys(entries)
      .filter((key) => key.includes(`/${role.id}/`) || key.endsWith(`/${role.id}.md`))
      .sort();
    return { role, meta, keys };
  });

  return [
    renderArchiveGroup("공용 기록", "회의 전체 문서", publicKeys, entries),
    ...roleGroups.map(({ role, meta, keys }) =>
      renderArchiveGroup(role.display_name, meta.badge, keys, entries, meta)
    ),
  ].join("");
}

function renderArchiveGroup(title, subtitle, keys, entries, meta) {
  if (!keys.length) return "";
  const avatar = meta
    ? `<img class="profile profile-tiny" src="${escapeHtml(meta.avatar)}" alt="" />`
    : `<span class="archive-dot"></span>`;
  return `
    <section class="archive-group">
      <div class="archive-group-title">
        ${avatar}
        <div>
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(subtitle)} · ${keys.length}개</span>
        </div>
      </div>
      ${keys.map(renderArchiveButton).join("")}
    </section>
  `;
}

function renderArchiveButton(key) {
  const label = key
    .replace("research/", "")
    .replace("evidence/", "evidence · ")
    .replace("tasks/", "task · ")
    .replace("/research.md", " · research.md");
  return `
    <button type="button" class="${key === state.archiveKey ? "is-active" : ""}" data-archive="${escapeHtml(key)}">
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(archiveKindLabel(key))}</span>
    </button>
  `;
}
