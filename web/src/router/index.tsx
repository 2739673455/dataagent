import type { ReactNode } from "react";
import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { RequireUser } from "@/identity";
import { PageLoadingScreen } from "@/components/PageLoadingScreen";
import { ROUTES } from "@/config/settings";

const ChatPage = lazy(() => import("@/pages/Chat"));
const SelectUserPage = lazy(() => import("@/pages/SelectUser"));
const NotFound = lazy(() => import("@/pages/NotFound"));

function SuspenseWrapper({ children, message }: { children: ReactNode; message: string }) {
  return <Suspense fallback={<PageLoadingScreen message={message} />}>{children}</Suspense>;
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to={ROUTES.chat} replace />,
  },
  {
    path: ROUTES.selectUser,
    element: (
      <SuspenseWrapper message="正在加载用户选择...">
        <SelectUserPage />
      </SuspenseWrapper>
    ),
  },
  {
    path: `${ROUTES.chat}/:conversationId?`,
    element: (
      <RequireUser>
        <SuspenseWrapper message="正在加载对话...">
          <ChatPage />
        </SuspenseWrapper>
      </RequireUser>
    ),
  },
  {
    path: "*",
    element: (
      <SuspenseWrapper message="正在加载页面...">
        <NotFound />
      </SuspenseWrapper>
    ),
  },
]);
