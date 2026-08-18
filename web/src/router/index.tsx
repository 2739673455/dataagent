import type { ReactNode } from "react";
import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AuthLoadingScreen, ProtectedRoute } from "@/auth";
import { ROUTES } from "@/config/settings";

const ChatPage = lazy(() => import("@/pages/Chat"));
const LoginPage = lazy(() => import("@/pages/Login"));
const NotFound = lazy(() => import("@/pages/NotFound"));

function SuspenseWrapper({ children }: { children: ReactNode }) {
  return <Suspense fallback={<AuthLoadingScreen />}>{children}</Suspense>;
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to={ROUTES.chat} replace />,
  },
  {
    path: ROUTES.login,
    element: (
      <SuspenseWrapper>
        <LoginPage />
      </SuspenseWrapper>
    ),
  },
  {
    path: ROUTES.chat,
    element: (
      <ProtectedRoute>
        <SuspenseWrapper>
          <ChatPage />
        </SuspenseWrapper>
      </ProtectedRoute>
    ),
  },
  {
    path: `${ROUTES.chat}/:conversationId`,
    element: (
      <ProtectedRoute>
        <SuspenseWrapper>
          <ChatPage />
        </SuspenseWrapper>
      </ProtectedRoute>
    ),
  },
  {
    path: "*",
    element: (
      <SuspenseWrapper>
        <NotFound />
      </SuspenseWrapper>
    ),
  },
]);
