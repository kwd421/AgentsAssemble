import {
  deleteJsonWithIdentity,
  fetchJsonWithIdentity,
  postJson,
  postJsonWithIdentity,
} from "./http";
import type { RoomInviteJoinResponse } from "./invites";
import type { UserProfileIdentity } from "./room";

export type PublicAccount = {
  account_id: string;
  provider: "google" | string;
  display_name: string;
  email: string;
  avatar_image_url: string;
};

export type AccountStatusResponse = {
  account: PublicAccount | null;
  google: {
    enabled: boolean;
    client_id: string;
    unavailable_reason: string;
  };
};

export type GoogleAccountChallengeResponse = {
  status: "ready";
  client_id: string;
  nonce: string;
};

export type GoogleAccountConnectResponse = {
  status: "connected";
  identity_switched: boolean;
  account: PublicAccount;
  user: {
    user_id: string;
    participant_id: string;
    display_name: string;
    avatar_image_url: string;
  };
};

export function fetchAccountStatus(
  identity: UserProfileIdentity = {}
): Promise<AccountStatusResponse> {
  return fetchJsonWithIdentity<AccountStatusResponse>("/api/account", identity);
}

export function connectGoogleAccount({
  credential,
  nonce,
  discardGuestOnAccountSwitch = false,
  identity = {},
}: {
  credential: string;
  nonce: string;
  discardGuestOnAccountSwitch?: boolean;
  identity?: UserProfileIdentity;
}): Promise<GoogleAccountConnectResponse> {
  return postJsonWithIdentity<GoogleAccountConnectResponse>(
    "/api/account/google",
    {
      credential,
      nonce,
      discard_guest_on_account_switch: discardGuestOnAccountSwitch,
    },
    identity
  );
}

export function startGoogleAccountLogin(
  identity: UserProfileIdentity = {}
): Promise<GoogleAccountChallengeResponse> {
  return postJsonWithIdentity<GoogleAccountChallengeResponse>(
    "/api/account/google/challenge",
    {},
    identity
  );
}

export function disconnectGoogleAccount(
  identity: UserProfileIdentity = {}
): Promise<{ status: "disconnected" }> {
  return deleteJsonWithIdentity<{ status: "disconnected" }>(
    "/api/account/google",
    identity
  );
}

export type GuestRecoveryCodeResponse = {
  status: "issued";
  server_id: string;
  room_id: string;
  recovery_code: string;
  recovery_url: string;
};

export type GuestRecoveryRedeemResponse = RoomInviteJoinResponse & {
  status: "recovered";
  client_id: string;
  room_uid?: string;
  server_id?: string;
  recovery_code: string;
};

export function issueGuestRecoveryCode({
  sessionToken,
  deviceToken,
}: {
  sessionToken: string;
  deviceToken?: string;
}): Promise<GuestRecoveryCodeResponse> {
  return postJsonWithIdentity<GuestRecoveryCodeResponse>(
    "/api/identity/recovery-code",
    {},
    { sessionToken, deviceToken }
  );
}

export function redeemGuestRecoveryCode({
  recoveryCode,
  roomId,
  deviceToken,
  clientId,
}: {
  recoveryCode: string;
  roomId: string;
  deviceToken: string;
  clientId: string;
}): Promise<GuestRecoveryRedeemResponse> {
  return postJson<GuestRecoveryRedeemResponse>("/api/identity/recovery-code/redeem", {
    recovery_code: recoveryCode,
    room_id: roomId,
    device_token: deviceToken,
    client_id: clientId,
  });
}
