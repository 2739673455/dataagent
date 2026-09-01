import type { ReactNode } from "react";
import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AdminRoute, ProtectedRoute } from "@/auth";
import { PageLoadingScreen } from "@/components/PageLoadingScreen";
import { ROUTES } from "@/config/settings";

const ChatPage = lazy(() => import("@/pages/Chat"));
const LoginPage = lazy(() => import("@/pages/Login"));
const AdminPage = lazy(() => import("@/pages/Admin"));
const NotFound = lazy(() => import("@/pages/NotFound"));

function SuspenseWrapper({ children, message }: { children: ReactNode; message: string }) {
  return <Suspense fallback={<PageLoadingScreen message={message} />}>{children}</Suspense>;
}

export const router = createBrowserRouter([
  {
    path: ROUTES.admin,
    element: (
      <AdminRoute>
        <SuspenseWrapper message="正在加载管理中心...">
          <AdminPage />
        </SuspenseWrapper>
      </AdminRoute>
    ),
  },
  {
    path: "/",
    element: <Navigate to={ROUTES.chat} replace />,
  },
  {
    path: ROUTES.login,
    element: (
      <SuspenseWrapper message="正在加载登录页面...">
        <LoginPage />
      </SuspenseWrapper>
    ),
  },
  {
    path: `${ROUTES.chat}/:conversationId?`,
    element: (
      <ProtectedRoute>
        <SuspenseWrapper message="正在加载对话...">
          <ChatPage />
        </SuspenseWrapper>
      </ProtectedRoute>
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
