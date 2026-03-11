import { Loader2 } from "lucide-react";
import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { createBrowserRouter } from "react-router-dom";
import { EntryRoute, ProtectedRoute } from "./guards";

const AuthRedirect = lazy(() => import("@/pages/AuthRedirect"));
const AuthCallback = lazy(() => import("@/pages/AuthCallback"));
const ChatPage = lazy(() => import("@/pages/Chat"));
const NotFound = lazy(() => import("@/pages/NotFound"));

function PageLoading() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 className="h-8 w-8 animate-spin text-primary" />
    </div>
  );
}

function SuspenseWrapper({ children }: { children: ReactNode }) {
  return <Suspense fallback={<PageLoading />}>{children}</Suspense>;
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <EntryRoute />,
  },
  {
    path: "/login",
    element: (
      <SuspenseWrapper>
        <AuthRedirect />
      </SuspenseWrapper>
    ),
  },
  {
    path: "/auth/callback",
    element: (
      <SuspenseWrapper>
        <AuthCallback />
      </SuspenseWrapper>
    ),
  },
  {
    path: "/chat",
    element: (
      <ProtectedRoute>
        <SuspenseWrapper>
          <ChatPage />
        </SuspenseWrapper>
      </ProtectedRoute>
    ),
  },
  {
    path: "/chat/:conversationId",
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
