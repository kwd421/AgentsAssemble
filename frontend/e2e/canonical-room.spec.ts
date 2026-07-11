import { expect, test } from "@playwright/test";

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
  await composer.fill("@fake AGENTSASSEMBLE_SESSION_MARKER=ui-e2e-001 기억하고 답해.");
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();
  const firstReply = page.getByText(/fake reply 1; marker=ui-e2e-001/);
  await expect(firstReply).toHaveCount(1);
  await expect(firstReply).toBeVisible();
  await expect(page.getByText("FAKE_CLI_READY", { exact: true })).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });

  async function openMobileSession() {
    await page.getByRole("button", { name: "general 채널 정보 열기" }).click();
    const mobileMember = page.getByRole("button").filter({ hasText: "Fake Interactive CLI" }).first();
    await expect(mobileMember).toBeVisible();
    await mobileMember.click();
    const mobileSession = page.getByRole("region", { name: "Fake Interactive CLI Agent Session" });
    await expect(mobileSession).toBeVisible();
    return mobileSession;
  }

  async function closeMobileSession() {
    await page.getByRole("button", { name: "멤버 목록" }).click();
    await page.getByRole("button", { name: "채널 정보 닫기" }).click();
  }

  session = await openMobileSession();
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
});
