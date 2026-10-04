import { useCallback, useEffect, useRef, useState } from "react";
import {
  addRoomFriend,
  deleteRoomFriend,
  fetchRoomFriends,
  type ParticipantType,
  type RoomFriend,
  type RoomFriendsResponse,
} from "../api";
import type { FriendListFilter, HomeFilter } from "./friendsDirectoryTypes";

type UseFriendsDirectoryOptions = {
  enabled: boolean;
};

type ManualFriendDraft = {
  displayName: string;
  participantType: ParticipantType;
  providerKind: string;
};

type MutationApplication = () => void;

const EMPTY_PAYLOAD: RoomFriendsResponse = { friends: [], candidates: [] };

function homeFilterForFriend(friend: RoomFriend): HomeFilter {
  if (friend.participant_type === "human") return "human";
  if (friend.participant_type === "subscription_ai") return "subscription_ai";
  if (friend.participant_type === "api") return "api";
  if (friend.participant_type === "local") return "local";
  if (friend.participant_type === "remote") return "remote";
  return "friends";
}

export function useFriendsDirectory({ enabled }: UseFriendsDirectoryOptions) {
  const [payload, setPayload] = useState<RoomFriendsResponse>(EMPTY_PAYLOAD);
  const [loading, setLoading] = useState(enabled);
  const [status, setStatus] = useState("");
  const [busyId, setBusyId] = useState("");
  const [homeFilter, setHomeFilter] = useState<HomeFilter>("friends");
  const [friendListFilter, setFriendListFilter] = useState<FriendListFilter>("online");
  const [selectedFriendId, setSelectedFriendId] = useState("");
  const [addDraftName, setAddDraftName] = useState("");

  const enabledRef = useRef(enabled);
  const previousEnabledRef = useRef(enabled);
  const enableEpochRef = useRef(0);
  const revisionRef = useRef(0);
  const refreshRequestRef = useRef(0);
  const mutationQueueRef = useRef(Promise.resolve());
  const payloadRef = useRef(payload);
  const selectedFriendIdRef = useRef(selectedFriendId);
  if (previousEnabledRef.current !== enabled) {
    previousEnabledRef.current = enabled;
    enableEpochRef.current += 1;
  }
  enabledRef.current = enabled;
  if (enabled) {
    payloadRef.current = payload;
    selectedFriendIdRef.current = selectedFriendId;
  } else {
    payloadRef.current = EMPTY_PAYLOAD;
    selectedFriendIdRef.current = "";
  }

  const replacePayload = useCallback((nextPayload: RoomFriendsResponse) => {
    const friendIds = new Set(nextPayload.friends.map((friend) => friend.friend_id));
    payloadRef.current = nextPayload;
    setPayload(nextPayload);
    setSelectedFriendId((previous) => {
      const nextSelection =
        !previous
          ? nextPayload.friends[0]?.friend_id || ""
          : friendIds.has(previous)
            ? previous
            : "";
      selectedFriendIdRef.current = nextSelection;
      return nextSelection;
    });
  }, []);

  const refresh = useCallback(async () => {
    if (!enabledRef.current) {
      setLoading(false);
      return false;
    }

    const requestId = ++refreshRequestRef.current;
    const requestRevision = revisionRef.current;
    setLoading(true);
    try {
      const nextPayload = await fetchRoomFriends();
      if (
        !enabledRef.current ||
        requestId !== refreshRequestRef.current ||
        requestRevision !== revisionRef.current
      ) {
        return false;
      }
      replacePayload(nextPayload);
      setStatus("");
      return true;
    } catch (error) {
      if (
        enabledRef.current &&
        requestId === refreshRequestRef.current &&
        requestRevision === revisionRef.current
      ) {
        setStatus(error instanceof Error ? error.message : "친구 목록을 불러오지 못했습니다");
      }
      return false;
    } finally {
      if (requestId === refreshRequestRef.current && requestRevision === revisionRef.current) {
        setLoading(false);
      }
    }
  }, [replacePayload]);

  useEffect(() => {
    revisionRef.current += 1;
    if (!enabled) {
      payloadRef.current = EMPTY_PAYLOAD;
      selectedFriendIdRef.current = "";
      setPayload(EMPTY_PAYLOAD);
      setSelectedFriendId("");
      setStatus("");
      setBusyId("");
      setLoading(false);
      return;
    }
    void refresh();
  }, [enabled, refresh]);

  const invalidateRefreshRequests = useCallback(() => {
    revisionRef.current += 1;
    refreshRequestRef.current += 1;
    setLoading(false);
  }, []);

  const runMutation = useCallback(
    (id: string, operation: () => Promise<MutationApplication>, failureMessage: string) => {
      const mutationEpoch = enableEpochRef.current;
      const execute = async () => {
        if (!enabledRef.current || mutationEpoch !== enableEpochRef.current) return false;
        invalidateRefreshRequests();
        setBusyId(id);
        setStatus("");
        try {
          const apply = await operation();
          if (!enabledRef.current || mutationEpoch !== enableEpochRef.current) return false;
          invalidateRefreshRequests();
          apply();
          return true;
        } catch (error) {
          if (!enabledRef.current || mutationEpoch !== enableEpochRef.current) return false;
          invalidateRefreshRequests();
          setStatus(error instanceof Error ? error.message : failureMessage);
          return false;
        } finally {
          if (mutationEpoch === enableEpochRef.current) setBusyId("");
        }
      };
      const queued = mutationQueueRef.current.then(execute, execute);
      mutationQueueRef.current = queued.then(
        () => undefined,
        () => undefined
      );
      return queued;
    },
    [invalidateRefreshRequests]
  );

  const changeHomeFilter = useCallback((filter: HomeFilter) => {
    setHomeFilter(filter);
    setFriendListFilter((previous) => {
      if (previous !== "add") return previous;
      return filter === "friends" ? "online" : "all";
    });
  }, []);

  const showDirectory = useCallback((filter: FriendListFilter) => {
    setFriendListFilter(filter);
  }, []);

  const selectFriend = useCallback((friend: RoomFriend) => {
    setSelectedFriendId(friend.friend_id);
    selectedFriendIdRef.current = friend.friend_id;
  }, []);

  const selectHomeFriend = useCallback(
    (friend: RoomFriend) => {
      setSelectedFriendId(friend.friend_id);
      selectedFriendIdRef.current = friend.friend_id;
      setFriendListFilter("all");
      setHomeFilter(homeFilterForFriend(friend));
    },
    []
  );

  const openAddFriend = useCallback((draftName = "") => {
    setAddDraftName(draftName.trim());
    setHomeFilter("friends");
    setFriendListFilter("add");
  }, []);

  const addCandidate = useCallback(
    (friend: RoomFriend) =>
      runMutation(
        friend.friend_id,
        async () => {
          const result = await addRoomFriend(friend);
          const nextCandidates = payloadRef.current.candidates.filter(
            (candidate) => candidate.friend_id !== friend.friend_id
          );
          return () => {
            replacePayload({ friends: result.friends, candidates: nextCandidates });
            setSelectedFriendId(result.friend.friend_id);
            selectedFriendIdRef.current = result.friend.friend_id;
            setFriendListFilter("all");
            setStatus(`${friend.display_name} 추가됨`);
          };
        },
        "친구 추가 실패"
      ),
    [replacePayload, runMutation]
  );

  const addManual = useCallback(
    ({ displayName, participantType, providerKind }: ManualFriendDraft) => {
      const name = displayName.trim();
      if (!name) {
        setStatus("이름을 입력하세요");
        return Promise.resolve(false);
      }
      return runMutation(
        "manual",
        async () => {
          const result = await addRoomFriend({
            display_name: name,
            participant_type: participantType,
            provider_kind: providerKind.trim(),
            status: "offline",
            source: "manual",
          });
          const nextCandidates = payloadRef.current.candidates;
          return () => {
            replacePayload({ friends: result.friends, candidates: nextCandidates });
            setSelectedFriendId(result.friend.friend_id);
            selectedFriendIdRef.current = result.friend.friend_id;
            setFriendListFilter("all");
            setStatus(`${name} 추가됨`);
          };
        },
        "친구 추가 실패"
      );
    },
    [replacePayload, runMutation]
  );

  const deleteFriend = useCallback(
    (friend: RoomFriend, preferredNextVisibleFriendId = "") =>
      runMutation(
        `delete:${friend.friend_id}`,
        async () => {
          const result = await deleteRoomFriend(friend.friend_id);
          const friendIds = new Set(result.friends.map((candidate) => candidate.friend_id));
          const selectedBeforeDelete = selectedFriendIdRef.current;
          const shouldMoveSelection = selectedBeforeDelete === friend.friend_id;
          return () => {
            payloadRef.current = result;
            setPayload(result);
            setSelectedFriendId((previous) => {
              const nextSelection =
                shouldMoveSelection && preferredNextVisibleFriendId && friendIds.has(preferredNextVisibleFriendId)
                  ? preferredNextVisibleFriendId
                  : !previous
                    ? result.friends[0]?.friend_id || ""
                    : friendIds.has(previous)
                      ? previous
                      : "";
              selectedFriendIdRef.current = nextSelection;
              return nextSelection;
            });
            setStatus(`${friend.display_name} 삭제됨`);
          };
        },
        "친구 삭제 실패"
      ),
    [runMutation]
  );

  return {
    payload: enabled ? payload : EMPTY_PAYLOAD,
    loading: enabled && loading,
    status: enabled ? status : "",
    busyId: enabled ? busyId : "",
    homeFilter,
    friendListFilter,
    selectedFriendId: enabled ? selectedFriendId : "",
    addDraftName,
    refresh,
    changeHomeFilter,
    showDirectory,
    selectHomeFriend,
    selectFriend,
    openAddFriend,
    addCandidate,
    addManual,
    deleteFriend,
  };
}
