import { useEffect, useRef } from "react";
import { RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { ACCESS_TOKEN_STORAGE_KEY, REFRESH_TOKEN_STORAGE_KEY, synchronizeSession } from "@/auth";
import { router } from "./router";

export default function App() {
  const routerRef = useRef(router);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (![ACCESS_TOKEN_STORAGE_KEY, REFRESH_TOKEN_STORAGE_KEY].includes(event.key ?? "")) {
        return;
      }
      void synchronizeSession();
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
