import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PendingProviderRequest } from "../../lib/providerRequestModel";
import ProviderRequestCard from "./ProviderRequestCard";

afterEach(cleanup);

function request(
  values: Partial<PendingProviderRequest> = {}
): PendingProviderRequest {
  return {
    provider_request_id: "provider-request-1",
    request_kind: "permission",
    response_kind: "option",
    status: "open",
    title: "터미널 명령 실행",
    options: [],
    questions: [],
    ...values,
  };
}

describe("ProviderRequestCard", () => {
  it("returns the exact native option identifier selected by the owner", async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined);
    render(
      <ProviderRequestCard
        request={request({
          options: [
            {
              id: "acceptForSession",
              label: "이 세션에서 허용",
              kind: "allow_session",
              description: "",
            },
            { id: "decline", label: "거절", kind: "decline", description: "" },
          ],
        })}
        onResolve={onResolve}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "이 세션에서 허용" }));

    await waitFor(() =>
      expect(onResolve).toHaveBeenCalledWith("provider-request-1", {
        option_id: "acceptForSession",
      })
    );
  });

  it("returns every provider question under its original question identifier", async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined);
    render(
      <ProviderRequestCard
        request={request({
          request_kind: "user_input",
          response_kind: "answers",
          title: "선택이 필요합니다",
          questions: [
            {
              id: "next-step",
              header: "다음 작업",
              question: "무엇을 할까요?",
              options: [
                {
                  id: "run-tests",
                  label: "테스트 실행",
                  kind: "answer",
                  description: "",
                },
              ],
              multiple: false,
              is_other: false,
              is_secret: false,
            },
          ],
        })}
        onResolve={onResolve}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "테스트 실행" }));
    await userEvent.click(screen.getByRole("button", { name: "답변 보내기" }));

    await waitFor(() =>
      expect(onResolve).toHaveBeenCalledWith("provider-request-1", {
        answers: { "next-step": ["테스트 실행"] },
      })
    );
  });

  it("returns all selected values for a provider multi-select question", async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined);
    render(
      <ProviderRequestCard
        request={request({
          request_kind: "user_input",
          response_kind: "answers",
          questions: [
            {
              id: "checks",
              header: "검증",
              question: "어떤 검증을 실행할까요?",
              options: [
                { id: "unit", label: "단위 테스트", kind: "answer", description: "" },
                { id: "build", label: "빌드", kind: "answer", description: "" },
              ],
              multiple: true,
              is_other: false,
              is_secret: false,
            },
          ],
        })}
        onResolve={onResolve}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "단위 테스트" }));
    await userEvent.click(screen.getByRole("button", { name: "빌드" }));
    await userEvent.click(screen.getByRole("button", { name: "답변 보내기" }));

    await waitFor(() =>
      expect(onResolve).toHaveBeenCalledWith("provider-request-1", {
        answers: { checks: ["단위 테스트", "빌드"] },
      })
    );
  });
});
