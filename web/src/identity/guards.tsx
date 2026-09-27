import { Fragment, useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { restoreSelection } from "@/identity/session";
import { useIdentityStore } from "@/identity/store";
import { PageLoadingScreen } from "@/components/PageLoadingScreen";
import { ROUTES } from "@/config/settings";

export function RequireUser({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const user = useIdentityStore((state) => state.user);
  const isLoading = useIdentityStore((state) => state.isLoading);

  useEffect(() => {
    if (isLoading) void restoreSelection();
  }, [isLoading]);

  if (isLoading) return <PageLoadingScreen message="正在读取所选用户..." />;
  if (!user) {
    const returnTo = `${location.pathname}${location.search}`;
    return (
      <Navigate
        to={`${ROUTES.selectUser}?${new URLSearchParams({ return_to: returnTo })}`}
        replace
      />
    );
  }
  return <Fragment key={user.id}>{children}</Fragment>;
}
