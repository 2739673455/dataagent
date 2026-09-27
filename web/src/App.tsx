import { useEffect, useRef } from "react";
import { RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { SELECTED_USER_STORAGE_KEY, synchronizeSelection } from "@/identity";
import { router } from "./router";

export default function App() {
  const routerRef = useRef(router);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== SELECTED_USER_STORAGE_KEY && event.key !== null) {
        return;
      }
      void synchronizeSelection();
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
