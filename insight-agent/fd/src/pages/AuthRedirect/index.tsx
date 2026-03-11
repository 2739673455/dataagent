import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { redirectToAuthorize } from "@/lib/redirect";

export default function AuthRedirect() {
  const location = useLocation();

  useEffect(() => {
    const from = (location.state as { from?: string } | null)?.from;
    redirectToAuthorize(from);
  }, [location.state]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 className="h-8 w-8 animate-spin text-primary" />
    </div>
  );
}
