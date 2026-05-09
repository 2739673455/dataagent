import { useEffect } from "react";
import { toast } from "sonner";
import { AuthLoadingScreen } from "@/auth/AuthLoadingScreen";
import { handleAuthCallback } from "@/auth/authorize";
import { clearAccessToken } from "@/auth/token";

let inflightCode: string | null = null;
let inflightTask: Promise<string> | null = null;
const handledCodes: string[] = [];

export default function AuthCallbackPage() {
  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      const searchParams = new URLSearchParams(window.location.search);
      const code = searchParams.get("code");
      const state = searchParams.get("state");

      if (!code || !state) {
        clearAccessToken();
        if (!code) toast.error("缺少授权码");
        if (!state) toast.error("缺少授权状态参数");
        window.location.replace("/");
        return;
      }

      // 防止重复消费同一授权码
      if (handledCodes.includes(code)) {
        toast.error("重复处理授权码");
        window.location.replace("/");
        return;
      }

      try {
        if (!inflightTask || inflightCode !== code) {
          inflightCode = code;
          inflightTask = handleAuthCallback(code, state).finally(() => {
            inflightCode = null;
            inflightTask = null;
          });
        }

        const returnTo = await inflightTask;
        handledCodes.push(code);
        if (handledCodes.length > 20) handledCodes.shift();
        if (cancelled) return;
        window.location.replace(returnTo);
      } catch (error) {
        if (cancelled) return;
        clearAccessToken();
        const message = error instanceof Error ? error.message : "登录状态建立失败，请重试";
        toast.error(message);
        window.location.replace("/");
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  return <AuthLoadingScreen />;
}
