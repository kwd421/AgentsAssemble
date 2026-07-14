import { expect, test } from "@playwright/test";
import { Buffer } from "node:buffer";

const PROFILE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZfNwAAAAASUVORK5CYII=",
  "base64"
);

test("streams on desktop and controls the same canonical session on mobile", async ({ page }) => {
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
  await expect(page.getByText("입력 중…", { exact: true })).toBeVisible();
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
  await expect(session.getByText("일시정지", { exact: true })).toBeVisible();
  await closeMobileSession();

  await composer.fill("@fake AGENTSASSEMBLE_SESSION_MARKER=ui-e2e-paused 재개 뒤에만 답해.");
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();
  const resumedReply = page.getByText(/fake reply 2; marker=ui-e2e-paused/);
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
