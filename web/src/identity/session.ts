import { listUsers } from "@/identity/api";
import { sessionLifecycle } from "@/identity/sessionLifecycle";
import { useIdentityStore } from "@/identity/store";
import { getSelectedUserId } from "@/identity/storage";
import type { UserResponse } from "@/identity/types";
import { ROUTES, SELECTED_USER_STORAGE_KEY } from "@/config/settings";

export function selectUser(user: UserResponse): void {
  if (useIdentityStore.getState().user?.id === user.id) return;
  sessionLifecycle.transition();
  localStorage.setItem(SELECTED_USER_STORAGE_KEY, String(user.id));
  useIdentityStore.getState().setUser(user);
}

export function clearSelection(): void {
  sessionLifecycle.transition();
  localStorage.removeItem(SELECTED_USER_STORAGE_KEY);
  useIdentityStore.getState().setUser(null);
}

export async function restoreSelection(): Promise<void> {
  const generation = sessionLifecycle.current();
  const userId = getSelectedUserId();
  if (!userId) {
    useIdentityStore.getState().setUser(null);
    return;
  }
  try {
    const users = await listUsers();
    if (!sessionLifecycle.isCurrent(generation) || getSelectedUserId() !== userId) return;
    const user = users.find((item) => String(item.id) === userId);
    if (user) useIdentityStore.getState().setUser(user);
    else clearSelection();
  } catch {
    if (sessionLifecycle.isCurrent(generation)) useIdentityStore.getState().setUser(null);
  }
}

export async function synchronizeSelection(): Promise<void> {
  sessionLifecycle.transition();
  useIdentityStore.getState().setUser(null);
  await restoreSelection();
}

export function redirectToUserSelection(returnTo?: string): void {
  const target = returnTo ?? `${window.location.pathname}${window.location.search}`;
  const query = new URLSearchParams({ return_to: target });
  window.location.replace(`${ROUTES.selectUser}?${query.toString()}`);
}
