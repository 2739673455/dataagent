import axios from "axios";
import type { UserResponse } from "@/identity/types";
import { USERS_API_PATH } from "@/config/settings";

export async function listUsers(): Promise<UserResponse[]> {
  return (await axios.get<UserResponse[]>(USERS_API_PATH)).data;
}
