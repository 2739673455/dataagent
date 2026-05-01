import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AuthLoadingScreen, GuestOnlyRoute, RequireAuth } from "@/features/auth";
import { BASE_URL, ROUTE_PATHS } from "@/shared/config/settings";

// 懒加载页面组件
const Login = lazy(() => import("@/features/user/pages/Login"));
const AuthCallback = lazy(() => import("@/features/auth/pages/AuthCallbackPage"));
const Register = lazy(() => import("@/features/user/pages/Register"));
const ForgetPassword = lazy(() => import("@/features/user/pages/ForgetPassword"));
const Profile = lazy(() => import("@/features/user/pages/Profile"));
const PermissionPanel = lazy(() => import("@/features/permission/pages/PermissionPanel"));
const NotFound = lazy(() => import("@/shared/components/NotFound"));

export function createAppRouter() {
  return createBrowserRouter(
    [
      {
        path: "/",
        element: <Navigate to={ROUTE_PATHS.profile} replace />,
      },
      {
        path: ROUTE_PATHS.authCallback,
        element: (
          <Suspense fallback={<AuthLoadingScreen />}>
            <AuthCallback />
          </Suspense>
        ),
      },
      {
        path: ROUTE_PATHS.login,
        element: (
          <GuestOnlyRoute>
            <Suspense fallback={<AuthLoadingScreen />}>
              <Login />
            </Suspense>
          </GuestOnlyRoute>
        ),
      },
      {
        path: ROUTE_PATHS.register,
        element: (
          <GuestOnlyRoute>
            <Suspense fallback={<AuthLoadingScreen />}>
              <Register />
            </Suspense>
          </GuestOnlyRoute>
        ),
      },
      {
        path: ROUTE_PATHS.forgetPassword,
        element: (
          <GuestOnlyRoute>
            <Suspense fallback={<AuthLoadingScreen />}>
              <ForgetPassword />
            </Suspense>
          </GuestOnlyRoute>
        ),
      },
      {
        path: ROUTE_PATHS.profile,
        element: (
          <RequireAuth>
            <Suspense fallback={<AuthLoadingScreen />}>
              <Profile />
            </Suspense>
          </RequireAuth>
        ),
      },
      {
        path: ROUTE_PATHS.permission,
        element: (
          <RequireAuth requiredScopes={["*"]}>
            <Suspense fallback={<AuthLoadingScreen />}>
              <PermissionPanel />
            </Suspense>
          </RequireAuth>
        ),
      },
      {
        path: "*",
        element: (
          <Suspense fallback={<AuthLoadingScreen />}>
            <NotFound />
          </Suspense>
        ),
      },
    ],
    {
      basename: BASE_URL.replace(/\/+$/, ""),
    }
  );
}
