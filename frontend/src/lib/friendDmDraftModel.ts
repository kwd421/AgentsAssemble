export type FriendDmDrafts = Record<string, string>;

function friendDraftKey(friendId: string): string {
  return String(friendId || "").trim();
}

export function friendDmDraftValue(drafts: FriendDmDrafts, friendId: string): string {
  const key = friendDraftKey(friendId);
  return key ? drafts[key] || "" : "";
}

export function updateFriendDmDraft(
  drafts: FriendDmDrafts,
  friendId: string,
  nextDraft: string
): FriendDmDrafts {
  const key = friendDraftKey(friendId);
  if (!key) return drafts;
  const value = String(nextDraft || "");
  if ((drafts[key] || "") === value) return drafts;
  const next = { ...drafts };
  if (value) {
    next[key] = value;
  } else {
    delete next[key];
  }
  return next;
}

export function clearFriendDmDraft(drafts: FriendDmDrafts, friendId: string): FriendDmDrafts {
  return updateFriendDmDraft(drafts, friendId, "");
}
