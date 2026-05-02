import { useEffect, useRef } from "react";
import { RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { ACCESS_TOKEN_STORAGE_KEY, checkAuth, useAuthStore } from "@/auth";
import { router } from "./router";

export default function App() {
  const routerRef = useRef(router);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== ACCESS_TOKEN_STORAGE_KEY) return;
      const authStore = useAuthStore.getState();

      if (event.newValue === null) {
        authStore.clearAuth();
        return;
      }

      void checkAuth();
    };

    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return (
    <>
      <RouterProvider router={routerRef.current} />
      <Toaster
        position="top-center"
        richColors
        toastOptions={{
          style: {
            border: "none",
            boxShadow: "0 2px 8px rgba(0, 0, 0, 0.1)",
          },
        }}
      />
    </>
  );
}
