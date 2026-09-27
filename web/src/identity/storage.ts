import { SELECTED_USER_STORAGE_KEY } from "@/config/settings";

export function getSelectedUserId(): string | null {
  return localStorage.getItem(SELECTED_USER_STORAGE_KEY);
}
