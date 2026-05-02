import { useQuery } from "@tanstack/react-query";
import { userApi } from "@/features/user/api";
import { useAuthStore } from "@/features/auth";

export function useCurrentUser() {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  return useQuery({
    queryKey: ["currentUser"],
    queryFn: () => userApi.getCurrentUser(),
    enabled: isAuthenticated,
  });
}
