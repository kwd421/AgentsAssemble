export const MAX_ATTACHMENTS_PER_EVENT = 8;
export const MAX_ATTACHMENTS_MESSAGE = "첨부는 한 메시지에 8개까지 가능합니다.";

export function selectLobbyAttachmentFiles<T>(currentCount: number, selectedItems: T[]) {
  const remainingSlots = MAX_ATTACHMENTS_PER_EVENT - currentCount;
  if (remainingSlots <= 0) {
    return { accepted: [] as T[], error: MAX_ATTACHMENTS_MESSAGE };
  }
  const accepted = selectedItems.slice(0, remainingSlots);
  return {
    accepted,
    error: selectedItems.length > remainingSlots ? MAX_ATTACHMENTS_MESSAGE : "",
  };
}

export function lobbySubmitSuccessDraft<T>() {
  return { message: "", pendingAttachments: [] as T[] };
}

export function lobbySubmitFailureDraft<T>(
  draftMessage: string,
  draftAttachments: T[],
  errorMessage: string
) {
  return {
    message: draftMessage,
    pendingAttachments: draftAttachments,
    error: errorMessage,
  };
}
