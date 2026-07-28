import { expect, test } from "@playwright/test";
import { Buffer } from "node:buffer";

const PROFILE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZfNwAAAAASUVORK5CYII=",
  "base64"
);

const HOST_TOKEN_STORAGE_KEY = "agentsassemble.hostToken.v1";
const E2E_HOST_TOKEN = "e2e-host-token";
const GUEST_SESSION_STORAGE_KEY = "agentsassemble.roomGuestSession.v1";

async function installHostCredential(page: import("@playwright/test").Page) {
  await page.addInitScript(
    ([key, token]) => window.sessionStorage.setItem(key, token),
    [HOST_TOKEN_STORAGE_KEY, E2E_HOST_TOKEN]
  );
}

async function openHostInviteDialog(page: import("@playwright/test").Page) {
  await installHostCredential(page);
  await page.goto("/");
  await page.getByRole("button", { name: "#general", exact: true }).click();
  await page.getByRole("button", { name: "서버에 초대하기" }).first().click();
  return page.getByRole("dialog", { name: /친구를 .*초대하기/ });
}

async function createGuestInviteUrl(page: import("@playwright/test").Page) {
  const inviteDialog = await openHostInviteDialog(page);
  await inviteDialog.getByRole("button", { name: "친구 초대 링크 생성" }).click();
  const guestInviteInput = inviteDialog.getByPlaceholder(
    "공개 URL을 먼저 설정하면 /join?token=... 링크가 여기에 표시됩니다"
  );
  await expect(guestInviteInput).toHaveValue(/^http:\/\/public\.localhost:\d+\/join\?token=/);
  return guestInviteInput.inputValue();
}

async function readGuestSession(page: import("@playwright/test").Page) {
  return page.evaluate((key) => JSON.parse(window.localStorage.getItem(key) || "null"), GUEST_SESSION_STORAGE_KEY);
}

async function joinGuest(
  page: import("@playwright/test").Page,
  inviteUrl: string,
  displayName: string
) {
  await page.goto(inviteUrl);
  const profile = page.getByRole("region", { name: "입장 프로필" });
  await expect(profile).toBeVisible();
  await profile.getByRole("textbox", { name: "이름" }).fill(displayName);
  await profile.getByRole("button", { name: "입장", exact: true }).click();
  await expect(profile).toHaveCount(0);
  await expect.poll(() => readGuestSession(page)).not.toBeNull();
  return readGuestSession(page);
}

async function roomWithLabel(page: import("@playwright/test").Page, label: string) {
  const response = await page.request.get("/api/rooms");
  expect(response.ok()).toBe(true);
  const payload = (await response.json()) as {
    rooms?: Array<{ room_id?: string; label?: string }>;
  };
  return (payload.rooms || []).find((room) => room.label === label) || null;
}

async function openActiveServerSettings(page: import("@playwright/test").Page) {
  const header = page.getByRole("button", { name: /서버 메뉴 열기$/ });
  const accessibleName = await header.getAttribute("aria-label");
  const roomLabel = String(accessibleName || "").replace(/ 서버 메뉴 열기$/, "");
  await page.getByRole("button", { name: roomLabel, exact: true }).click({
    button: "right",
  });
  await page.getByRole("menuitem", { name: "서버 설정", exact: true }).click();
}

test("keeps ordinary invites separate from one-time cross-origin operator pairing", async ({
  browser,
  page,
}) => {
  const inviteDialog = await openHostInviteDialog(page);

  await inviteDialog.getByRole("button", { name: "친구 초대 링크 생성" }).click();
  const guestInviteInput = inviteDialog.getByPlaceholder(
    "공개 URL을 먼저 설정하면 /join?token=... 링크가 여기에 표시됩니다"
  );
  await expect(guestInviteInput).toHaveValue(/^http:\/\/public\.localhost:\d+\/join\?token=/);
  const guestInviteUrl = await guestInviteInput.inputValue();

  await inviteDialog.getByRole("button", { name: "운영자 기기 연결 링크 생성" }).click();
  const pairingInput = inviteDialog.getByPlaceholder("일회용 운영자 기기 연결 링크");
  await expect(pairingInput).toHaveValue(/^http:\/\/public\.localhost:\d+\/pair\?token=aap1_/);
  const pairingUrl = await pairingInput.inputValue();

  const unknownContext = await browser.newContext();
  const unknownPage = await unknownContext.newPage();
  await unknownPage.goto(guestInviteUrl);
  await expect(unknownPage.getByRole("region", { name: "입장 프로필" })).toBeVisible();
  await expect(unknownPage.getByRole("textbox", { name: "이름" })).toBeVisible();

  const wrongOriginContext = await browser.newContext();
  const wrongOriginPage = await wrongOriginContext.newPage();
  const wrongOriginUrl = new URL(pairingUrl);
  wrongOriginUrl.hostname = "127.0.0.1";
  await wrongOriginPage.goto(wrongOriginUrl.toString());
  await expect(wrongOriginPage.getByRole("region", { name: "운영자 기기 연결" })).toContainText(
    "pairing_origin_mismatch"
  );
  expect(await readGuestSession(wrongOriginPage)).toBeNull();

  const pairedContext = await browser.newContext();
  const pairedPage = await pairedContext.newPage();
  await pairedPage.goto(pairingUrl);
  await expect.poll(() => new URL(pairedPage.url()).search).toBe("");
  await expect(pairedPage.getByRole("button", { name: "#general", exact: true })).toBeVisible();
  const pairedSession = await readGuestSession(pairedPage);
  expect(pairedSession).toMatchObject({
    agentId: "operator-local",
    operator: true,
    meetingId: "general",
  });
  await expect(pairedPage.getByRole("region", { name: "입장 프로필" })).toHaveCount(0);

  const replayContext = await browser.newContext();
  const replayPage = await replayContext.newPage();
  await replayPage.goto(pairingUrl);
  await expect(replayPage.getByRole("region", { name: "운영자 기기 연결" })).toContainText(
    "pairing_already_used"
  );
  const replaySession = await replayPage.evaluate(() =>
    window.localStorage.getItem("agentsassemble.roomGuestSession.v1")
  );
  expect(replaySession).toBeNull();

  await unknownContext.close();
  await wrongOriginContext.close();
  await pairedContext.close();
  await replayContext.close();
});

test("rejoins a same-origin browser without changing its participant identity", async ({
  browser,
  page,
}) => {
  const guestInviteUrl = await createGuestInviteUrl(page);
  const guestContext = await browser.newContext();
  const guestPage = await guestContext.newPage();

  const first = await joinGuest(guestPage, guestInviteUrl, "Returning Guest");

  await guestPage.goto(guestInviteUrl);
  await expect(guestPage.getByRole("region", { name: "입장 프로필" })).toHaveCount(0);
  await expect.poll(() => new URL(guestPage.url()).search).toBe("");
  const existingSession = await readGuestSession(guestPage);
  expect(existingSession.agentId).toBe(first.agentId);
  expect(existingSession.sessionToken).toBe(first.sessionToken);

  await guestPage.evaluate((key) => window.localStorage.setItem(key, "null"), GUEST_SESSION_STORAGE_KEY);
  await guestPage.goto(guestInviteUrl);
  await expect(guestPage.getByRole("region", { name: "입장 프로필" })).toHaveCount(0);
  await expect.poll(() => readGuestSession(guestPage)).not.toBeNull();
  const existingMember = await readGuestSession(guestPage);
  expect(existingMember.agentId).toBe(first.agentId);

  await guestPage.evaluate((key) => {
    const session = JSON.parse(window.localStorage.getItem(key) || "null");
    session.sessionToken = "aas1.expired-browser-session";
    session.expiresAt = "2000-01-01T00:00:00+00:00";
    window.localStorage.setItem(key, JSON.stringify(session));
  }, GUEST_SESSION_STORAGE_KEY);
  await guestPage.goto(guestInviteUrl);
  await expect(guestPage.getByRole("region", { name: "입장 프로필" })).toHaveCount(0);
  await expect
    .poll(async () => {
      const session = await readGuestSession(guestPage);
      return Boolean(
        session &&
          session.agentId === first.agentId &&
          session.sessionToken !== "aas1.expired-browser-session"
      );
    })
    .toBe(true);
  const recovered = await readGuestSession(guestPage);
  expect(recovered.sessionToken).not.toBe("aas1.expired-browser-session");
  expect(recovered.agentId).toBe(first.agentId);

  await guestContext.close();
});

test("recovers a failed join and keeps incognito credentials distinct", async ({ browser, page }) => {
  const guestInviteUrl = await createGuestInviteUrl(page);
  const recoveringContext = await browser.newContext();
  const recoveringPage = await recoveringContext.newPage();
  let failedOnce = false;
  await recoveringPage.route("**/api/room-invite/join", async (route) => {
    if (!failedOnce) {
      failedOnce = true;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "injected_join_failure" }),
      });
      return;
    }
    await route.continue();
  });
  await recoveringPage.goto(guestInviteUrl);
  const recoveringProfile = recoveringPage.getByRole("region", { name: "입장 프로필" });
  await recoveringProfile.getByRole("textbox", { name: "이름" }).fill("Same Display Name");
  const joinButton = recoveringProfile.getByRole("button", { name: "입장", exact: true });
  await joinButton.click();
  await expect(recoveringProfile).toContainText("injected_join_failure");
  await expect(joinButton).toBeEnabled();
  await joinButton.click();
  await expect(recoveringProfile).toHaveCount(0);
  const recoveredSession = await readGuestSession(recoveringPage);

  const incognitoContext = await browser.newContext();
  const incognitoPage = await incognitoContext.newPage();
  const incognitoSession = await joinGuest(incognitoPage, guestInviteUrl, "Same Display Name");

  expect(incognitoSession.displayName).toBe(recoveredSession.displayName);
  expect(incognitoSession.agentId).not.toBe(recoveredSession.agentId);
  expect(incognitoSession.sessionToken).not.toBe(recoveredSession.sessionToken);

  await recoveringContext.close();
  await incognitoContext.close();
});

test("removes a kicked participant immediately and after roster reload", async ({
  browser,
  page,
}) => {
  const guestInviteUrl = await createGuestInviteUrl(page);
  await page.getByRole("button", { name: "초대 닫기" }).click();
  const guestContext = await browser.newContext();
  try {
    const guestPage = await guestContext.newPage();
    const guestSession = await joinGuest(guestPage, guestInviteUrl, "Departing Guest");
    expect(guestSession.meetingId).toBe("general");
    await expect
      .poll(async () => {
        const response = await page.request.get(
          "/api/room-members?meeting_id=general",
          { headers: { "X-Host-Token": E2E_HOST_TOKEN } }
        );
        if (!response.ok()) return [`http-${response.status()}`];
        const payload = (await response.json()) as {
          members?: Array<{ display_name?: string }>;
        };
        return (payload.members || []).map((member) => member.display_name || "");
      })
      .toContain("Departing Guest");

    const guestMember = page
      .locator(".dc-member")
      .filter({ hasText: "Departing Guest" })
      .first();
    await expect(guestMember).toBeVisible();
    await guestMember.click({ button: "right" });
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("menuitem", { name: "내보내기", exact: true }).click();

    await expect(guestMember).toHaveCount(0);
    await page.reload();
    await page.getByRole("button", { name: "#general", exact: true }).click();
    await expect(
      page.locator(".dc-member").filter({ hasText: "Departing Guest" })
    ).toHaveCount(0);
  } finally {
    await guestContext.close();
  }
});

test("expires a stale stored guest session and offers a working exit", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(
    ([key, session]) => window.localStorage.setItem(key, JSON.stringify(session)),
    [
      GUEST_SESSION_STORAGE_KEY,
      {
        inviteToken: "",
        sessionToken: "aas1.expired-startup-session",
        meetingId: "general",
        agentId: "expired-guest",
        displayName: "Expired Guest",
        inviteScope: "room",
        expiresAt: "2000-01-01T00:00:00Z",
        joinedAt: "2000-01-01T00:00:00Z",
        roomLabel: "General",
      },
    ]
  );
  await page.reload();

  await expect(page.getByText("게스트 세션 만료", { exact: true })).toBeVisible();
  await expect.poll(() => readGuestSession(page)).toBeNull();
  await page.getByRole("button", { name: "게스트 화면 나가기" }).click();

  await expect.poll(() => new URL(page.url()).pathname).toBe("/");
  await expect(page.getByLabel("게스트 프로필")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "새 방 만들기" })).toBeVisible();
});

test("sends and restores an attachment-only canonical room message", async ({ browser, page }) => {
  await installHostCredential(page);
  await page.goto("/");
  await page.getByRole("button", { name: "#general", exact: true }).click();

  await page.getByLabel("채팅 첨부 선택").setInputFiles({
    name: "attachment-only.png",
    mimeType: "image/png",
    buffer: PROFILE_PNG,
  });
  await expect(page.getByText("attachment-only.png", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();

  const postedImage = page.getByRole("img", { name: "attachment-only.png" });
  await expect(postedImage).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: "#general", exact: true }).click();
  await expect(page.getByRole("img", { name: "attachment-only.png" })).toBeVisible();

  const observerContext = await browser.newContext();
  const observerPage = await observerContext.newPage();
  await installHostCredential(observerPage);
  await observerPage.goto("/");
  await observerPage.getByRole("button", { name: "#general", exact: true }).click();
  await expect(observerPage.getByRole("img", { name: "attachment-only.png" })).toBeVisible();
  await observerContext.close();
});

test("keeps unsent lobby and side-chat drafts scoped to their server", async ({ page }) => {
  const serverLabel = "E2E Draft Scope Server";
  await page.setViewportSize({ width: 1440, height: 900 });
  await installHostCredential(page);
  await page.goto("/");
  await page.getByRole("button", { name: "새 방 만들기" }).click();
  await openActiveServerSettings(page);

  let settings = page.getByRole("dialog", { name: "서버 설정" });
  await settings.getByLabel("서버 이름").first().fill(serverLabel);
  await expect.poll(() => roomWithLabel(page, serverLabel)).not.toBeNull();
  await settings.getByRole("button", { name: "설정 닫기" }).click();

  const lobbyInput = page.getByLabel("채팅 입력");
  await lobbyInput.fill("created room lobby draft");
  await page.getByLabel("채팅 첨부 선택").setInputFiles({
    name: "created-room-draft.png",
    mimeType: "image/png",
    buffer: PROFILE_PNG,
  });
  await expect(page.getByText("created-room-draft.png", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "사이드챗" }).click();
  const sideChatInput = page.getByLabel("비공식 사이드챗 입력");
  await sideChatInput.fill("created room side draft");

  await page.getByRole("button", { name: "#general", exact: true }).click();
  await expect(lobbyInput).toHaveValue("");
  await expect(page.getByText("created-room-draft.png", { exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "사이드챗" }).click();
  await expect(sideChatInput).toHaveValue("");
  await lobbyInput.fill("general lobby draft");
  await sideChatInput.fill("general side draft");

  await page.getByRole("button", { name: serverLabel, exact: true }).click();
  await expect(lobbyInput).toHaveValue("created room lobby draft");
  await expect(page.getByText("created-room-draft.png", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "사이드챗" }).click();
  await expect(sideChatInput).toHaveValue("created room side draft");

  await openActiveServerSettings(page);
  settings = page.getByRole("dialog", { name: "서버 설정" });
  await settings.getByRole("link", { name: "서버 삭제" }).click();
  await settings.getByLabel("서버 이름").last().fill(serverLabel);
  await settings.getByRole("button", { name: "서버 영구 삭제" }).click();
  await expect.poll(() => roomWithLabel(page, serverLabel)).toBeNull();
});

test("persists a created server and removes it from every connected browser", async ({
  browser,
  page,
}) => {
  const serverLabel = "E2E Lifecycle Server";
  const serverTopic = "Persists through reload and disappears after deletion";
  await installHostCredential(page);
  await page.goto("/");
  await page.getByRole("button", { name: "새 방 만들기" }).click();
  await openActiveServerSettings(page);

  let settings = page.getByRole("dialog", { name: "서버 설정" });
  await settings.getByLabel("서버 이름").first().fill(serverLabel);
  await settings.getByLabel("방 주제").fill(serverTopic);
  await expect.poll(() => roomWithLabel(page, serverLabel)).not.toBeNull();
  const createdRoom = await roomWithLabel(page, serverLabel);
  expect(createdRoom?.room_id).toBeTruthy();
  await settings.getByRole("button", { name: "설정 닫기" }).click();

  await page.reload();
  const firstRoomButton = page.getByRole("button", {
    name: serverLabel,
    exact: true,
  });
  await expect(firstRoomButton).toBeVisible();
  await firstRoomButton.click();
  await openActiveServerSettings(page);
  settings = page.getByRole("dialog", { name: "서버 설정" });
  await expect(settings.getByLabel("서버 이름").first()).toHaveValue(serverLabel);
  await expect(settings.getByLabel("방 주제")).toHaveValue(serverTopic);
  await settings.getByRole("button", { name: "설정 닫기" }).click();

  const observerContext = await browser.newContext();
  try {
    const observerPage = await observerContext.newPage();
    await installHostCredential(observerPage);
    await observerPage.goto("/");
    const observerRoomButton = observerPage.getByRole("button", {
      name: serverLabel,
      exact: true,
    });
    await expect(observerRoomButton).toBeVisible();
    await observerRoomButton.click();

    await openActiveServerSettings(page);
    settings = page.getByRole("dialog", { name: "서버 설정" });
    await settings.getByRole("link", { name: "서버 삭제" }).click();
    await settings.getByLabel("서버 이름").last().fill(serverLabel);
    await settings.getByRole("button", { name: "서버 영구 삭제" }).click();

    await expect(firstRoomButton).toHaveCount(0);
    await expect(observerRoomButton).toHaveCount(0);
    await expect.poll(() => roomWithLabel(page, serverLabel)).toBeNull();

    await observerPage.reload();
    await expect(
      observerPage.getByRole("button", { name: serverLabel, exact: true })
    ).toHaveCount(0);
  } finally {
    await observerContext.close();
  }
});

test("streams on desktop and controls the same canonical session on mobile", async ({ page }) => {
  await installHostCredential(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  const roomButton = page.getByRole("button", { name: "#general", exact: true });
  await expect(roomButton).toBeVisible();
  await roomButton.click();

  const desktopMember = page.getByRole("button").filter({ hasText: "Fake Interactive CLI" }).first();
  await expect(desktopMember).toBeVisible();
  await desktopMember.click();
  let session = page.getByRole("region", { name: "Fake Interactive CLI Agent Session" });
  await session.getByRole("button", { name: "시작", exact: true }).click();
  await expect(session.getByText("대기", { exact: true })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "멤버 정보 닫기" }).click();

  const composer = page.getByRole("textbox", { name: "채팅 입력" });
  await composer.fill(
    "@fake AGENTSASSEMBLE_SESSION_MARKER=ui-e2e-001 AGENTSASSEMBLE_RESPONSE_DELAY_MS=500 기억하고 답해."
  );
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();
  await expect(page.getByText("입력중...", { exact: true })).toBeVisible();
  const firstReply = page.getByText(/fake reply 1; marker=ui-e2e-001/);
  await expect(firstReply).toHaveCount(1);
  await expect(firstReply).toBeVisible();
  await expect(page.getByText("입력 중…", { exact: true })).toHaveCount(0);
  await expect(page.getByText("FAKE_CLI_READY", { exact: true })).toHaveCount(0);

  await desktopMember.click();
  const profileDialog = page.getByRole("dialog", { name: "나's Fake Interactive CLI" });
  await profileDialog.getByRole("textbox", { name: "이름" }).fill("Makima");
  await profileDialog.locator('input[type="file"]').setInputFiles({
    name: "makima.png",
    mimeType: "image/png",
    buffer: PROFILE_PNG,
  });
  await profileDialog.getByRole("button", { name: "적용", exact: true }).click();
  await expect(profileDialog.getByText("프로필 사진 준비됨", { exact: true })).toBeVisible();
  await profileDialog.getByRole("button", { name: "저장", exact: true }).click();
  const renamedProfileDialog = page.getByRole("dialog", { name: "나's Makima" });
  await expect(renamedProfileDialog.getByText("에이전트 프로필 저장됨", { exact: true })).toBeVisible();
  const savedAvatar = renamedProfileDialog.locator("img.dc-member-avatar-image").first();
  await expect(savedAvatar).toBeVisible();
  const savedAvatarUrl = await savedAvatar.getAttribute("src");
  expect(savedAvatarUrl).toMatch(/^\/api\/attachments\//);
  await renamedProfileDialog.getByRole("button", { name: "멤버 정보 닫기" }).click();
  const renamedReply = page.locator(".dc-message").filter({ hasText: "fake reply 1; marker=ui-e2e-001" });
  await expect(renamedReply.getByText("Makima", { exact: true })).toBeVisible();
  await expect(renamedReply.locator("img.dc-message-avatar-image")).toHaveAttribute(
    "src",
    savedAvatarUrl || ""
  );
  await expect(page.getByRole("button").filter({ hasText: "Makima" }).first()).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: "#general", exact: true }).click();
  const reloadedReply = page.locator(".dc-message").filter({ hasText: "fake reply 1; marker=ui-e2e-001" });
  await expect(reloadedReply.getByText("Makima", { exact: true })).toBeVisible();
  await expect(reloadedReply.locator("img.dc-message-avatar-image")).toHaveAttribute(
    "src",
    savedAvatarUrl || ""
  );

  await composer.fill("| 에이전트 | 상태 |\n| --- | --- |\n| Fake CLI | 대기 |");
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();
  const markdownTable = page.locator(".dc-message").filter({ hasText: "Fake CLI" }).last().locator("table");
  await expect(markdownTable).toBeVisible();
  await expect(markdownTable.getByRole("columnheader", { name: "에이전트" })).toBeVisible();

  await composer.fill("같은 화자의 연속 메시지 첫 번째");
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();
  await composer.fill("같은 화자의 연속 메시지 두 번째");
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();
  const groupedFollowUp = page.locator(".dc-message").filter({ hasText: "같은 화자의 연속 메시지 두 번째" });
  await expect(groupedFollowUp.locator(".dc-message-avatar")).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });

  async function openMobileSession() {
    await page.getByRole("button", { name: "general 채널 정보 열기" }).click();
    const mobileMember = page.getByRole("button").filter({ hasText: "Makima" }).first();
    await expect(mobileMember).toBeVisible();
    await mobileMember.click();
    const mobileSession = page.getByRole("region", { name: "Makima Agent Session" });
    await expect(mobileSession).toBeVisible();
    return mobileSession;
  }

  async function closeMobileSession() {
    await page.getByRole("button", { name: "멤버 목록" }).click();
    await page.getByRole("button", { name: "채널 정보 닫기" }).click();
  }

  session = await openMobileSession();
  const activityToggle = session.getByRole("checkbox", { name: /켜짐/ });
  await expect(activityToggle).toBeChecked();
  await activityToggle.uncheck();
  await expect(session.getByRole("checkbox", { name: /꺼짐/ })).not.toBeChecked();
  await session.getByText("고급 진단", { exact: true }).click();
  await expect(session.getByText("Runtime", { exact: true })).toBeVisible();
  await expect(session.getByText(/input \d+ chars · \d+ events/)).toBeVisible();
  await expect(session.getByText(/stderr \d+ bytes · warnings \d+/)).toBeVisible();
  await session.getByRole("button", { name: "일시정지", exact: true }).click();
  await expect(
    session.locator(".dc-member-session-location-head").getByText("일시정지", { exact: true })
  ).toBeVisible();
  await closeMobileSession();

  await composer.fill("@fake AGENTSASSEMBLE_SESSION_MARKER=ui-e2e-paused 재개 뒤에만 답해.");
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();
  const resumedReply = page.getByText(/fake reply \d+; marker=ui-e2e-paused/);
  await page.waitForTimeout(300);
  await expect(resumedReply).toHaveCount(0);

  session = await openMobileSession();
  await session.getByRole("button", { name: "재개", exact: true }).click();
  await closeMobileSession();
  await expect(resumedReply).toHaveCount(1);
  await expect(resumedReply).toBeVisible();

  session = await openMobileSession();
  await session.getByRole("button", { name: "중지", exact: true }).click();
  await expect(session.getByText("중지됨", { exact: true })).toBeVisible();
  await expect(page.getByText("다음 턴 호출", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "멤버 목록" }).click();
  await page.getByRole("button", { name: "채널 정보 닫기" }).click();

  await page.getByRole("button", { name: "채널 목록 열기" }).click();
  await page.getByRole("button", { name: "서버 설정 열기" }).click();
  const settings = page.getByRole("dialog", { name: "서버 설정" });
  const deleteSection = settings.locator("#settings-delete");
  await expect(deleteSection.getByText("이 작업은 복구할 수 없습니다.")).toBeVisible();
  const deleteButton = deleteSection.getByRole("button", { name: "서버 영구 삭제" });
  await expect(deleteButton).toBeDisabled();
  await deleteSection.getByRole("textbox", { name: "서버 이름" }).fill("#general");
  await expect(deleteButton).toBeEnabled();
  await deleteButton.click();
  await expect(page.getByRole("button", { name: "#general", exact: true })).toHaveCount(0);
});
