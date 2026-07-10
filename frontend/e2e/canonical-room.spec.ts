import { expect, test } from "@playwright/test";

test("streams one canonical Agent Session message and controls the persistent CLI", async ({ page }) => {
  await page.goto("/");
  const roomButton = page.getByRole("button", { name: "#general", exact: true });
  await expect(roomButton).toBeVisible();
  await roomButton.click();

  const sessionCard = page.getByRole("article").filter({ hasText: "Fake Interactive CLI" });
  await expect(sessionCard).toHaveCount(1);
  await sessionCard.getByRole("button", { name: "Start", exact: true }).click();
  await expect(sessionCard.getByText("대기", { exact: true })).toBeVisible();

  const composer = page.getByRole("textbox", { name: "채팅 입력" });
  await composer.fill("@fake AGENTSASSEMBLE_SESSION_MARKER=ui-e2e-001 기억하고 답해.");
  await page.getByRole("button", { name: "채팅 메시지 보내기" }).click();

  const reply = page.getByText(/fake reply 1; marker=ui-e2e-001/);
  await expect(reply).toHaveCount(1);
  await expect(reply).toBeVisible();
  await expect(page.getByText("FAKE_CLI_READY", { exact: true })).toHaveCount(0);
  await sessionCard.getByText("진단", { exact: true }).click();
  await expect(sessionCard.getByText("runtime live_cli · pty", { exact: true })).toBeVisible();
  await expect(sessionCard.getByText(/input \d+ chars · \d+ events/)).toBeVisible();
  await expect(sessionCard.getByText(/stderr \d+ bytes · warnings \d+/)).toBeVisible();
  await expect(page.getByText("다음 턴 호출", { exact: true })).toHaveCount(0);

  await sessionCard.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(sessionCard.getByText("중지됨", { exact: true })).toBeVisible();
});
