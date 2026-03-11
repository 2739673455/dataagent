import { RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { router } from "./router";

export default function App() {
  return (
    <>
      <RouterProvider router={router} />
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
