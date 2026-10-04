import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import VoteComposerDialog from "./VoteComposerDialog";

afterEach(cleanup);

describe("VoteComposerDialog", () => {
  it("creates a vote without a deadline when the participant chooses no deadline", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<VoteComposerDialog onClose={vi.fn()} onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText("질문"), {
      target: { value: "계속 진행할까요?" },
    });
    fireEvent.change(screen.getByLabelText("선택지 1"), {
      target: { value: "계속" },
    });
    fireEvent.change(screen.getByLabelText("선택지 2"), {
      target: { value: "중단" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "마감 시간 없음" }));
    fireEvent.click(screen.getByRole("button", { name: "만들기" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        question: "계속 진행할까요?",
        options: ["계속", "중단"],
        durationSeconds: 0,
      })
    );
  });
});
