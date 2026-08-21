import { authApi } from "@/auth/api";
import {
  isRefreshSnapshotCurrent,
  sessionLifecycle,
  type RefreshSnapshot,
} from "@/auth/sessionLifecycle";
import { useAuthStore } from "@/auth/store";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "@/auth/token";
import type { LoginRequest, TokenResponse } from "@/auth/types";
import { ROUTES } from "@/config/settings";

interface RefreshTask {
  snapshot: RefreshSnapshot;
  promise: Promise<string>;
}

let refreshTask: RefreshTask | null = null;
const REFRESH_LOCK_NAME = "dataagent:refresh-token";

class SessionSupersededError extends Error {}

function establishSession(payload: TokenResponse): string {
  setTokens(payload.access_token, payload.refresh_token);
  useAuthStore.getState().setAuth(payload.user);
  return payload.access_token;
}

export async function loginUser(body: LoginRequest): Promise<void> {
  const generation = clearSession();
  const payload = (await authApi.login(body)).data;
  if (sessionLifecycle.isCurrent(generation)) establishSession(payload);
}

export async function refreshAccessToken(): Promise<string> {
  const expectedRefreshToken = getRefreshToken();
  if (!expectedRefreshToken) throw new Error("登录状态已失效");
  const snapshot: RefreshSnapshot = {
    generation: sessionLifecycle.current(),
    refreshToken: expectedRefreshToken,
  };
  if (
    refreshTask &&
    refreshTask.snapshot.generation === snapshot.generation &&
    refreshTask.snapshot.refreshToken === snapshot.refreshToken
  ) {
    return refreshTask.promise;
  }

  const rotate = async () => {
    const currentRefreshToken = getRefreshToken();
    if (!isRefreshSnapshotCurrent(snapshot, currentRefreshToken)) {
      throw new SessionSupersededError("登录状态已变更");
    }
    const payload = (await authApi.refresh(snapshot.refreshToken)).data;
    if (!isRefreshSnapshotCurrent(snapshot, getRefreshToken())) {
      throw new SessionSupersededError("登录状态已变更");
    }
    return establishSession(payload);
  };

  const task =
    "locks" in navigator
      ? navigator.locks.request(REFRESH_LOCK_NAME, rotate).then((accessToken) => accessToken)
      : rotate();
  let currentTask: Promise<string>;
  currentTask = task
    .catch((error) => {
      if (
        !(error instanceof SessionSupersededError) &&
        sessionLifecycle.isCurrent(snapshot.generation) &&
        getRefreshToken() === snapshot.refreshToken
      ) {
        clearSession();
      }
      throw error;
    })
    .finally(() => {
      if (refreshTask?.promise === currentTask) refreshTask = null;
    });
  refreshTask = { snapshot, promise: currentTask };
  return currentTask;
}

export async function checkAuth(): Promise<void> {
  const generation = sessionLifecycle.current();
  const token = getAccessToken();
  try {
    const accessToken = token ?? (await refreshAccessToken());
    const user = (await authApi.getMe(accessToken)).data;
    if (sessionLifecycle.isCurrent(generation) && getAccessToken() === accessToken) {
      useAuthStore.getState().setAuth(user);
    }
  } catch {
    if (!sessionLifecycle.isCurrent(generation)) return;
    if (getRefreshToken()) {
      try {
        const accessToken = await refreshAccessToken();
        const user = (await authApi.getMe(accessToken)).data;
        if (sessionLifecycle.isCurrent(generation) && getAccessToken() === accessToken) {
          useAuthStore.getState().setAuth(user);
        }
        return;
      } catch {
        // 刷新失败时统一清理本地登录态
      }
    }
    if (sessionLifecycle.isCurrent(generation)) clearSession();
  }
}

function resetSession(clearStoredTokens: boolean): number {
  const generation = sessionLifecycle.transition();
  if (clearStoredTokens) clearTokens();
  useAuthStore.getState().clearAuth();
  return generation;
}

function clearSession(): number {
  return resetSession(true);
}

export async function logoutUser(): Promise<void> {
  const refreshToken = getRefreshToken();
  clearSession();
  if (refreshToken) await authApi.logout(refreshToken);
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  const accessToken = getAccessToken() ?? (await refreshAccessToken());
  await authApi.changePassword(accessToken, {
    current_password: currentPassword,
    new_password: newPassword,
  });
  clearSession();
}

export async function synchronizeSession(): Promise<void> {
  resetSession(false);
  await checkAuth();
}

export function redirectToLogin(returnTo?: string): void {
  const target = returnTo ?? `${window.location.pathname}${window.location.search}`;
  const query = new URLSearchParams({ return_to: target });
  window.location.replace(`${ROUTES.login}?${query.toString()}`);
}
