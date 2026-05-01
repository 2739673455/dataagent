import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, LogOut, PanelsTopLeft, Pencil, User } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { clearAccessToken, useAuthStore } from "@/features/auth";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card";
import { Separator } from "@/shared/components/ui/separator";
import { ROUTE_PATHS } from "@/shared/config/settings";
import { useCurrentUser } from "@/features/user/hooks/useCurrentUser";
import { userApi } from "@/features/user/api";
import { EditEmailForm } from "./Profile/EditEmailForm";
import { EditPasswordForm } from "./Profile/EditPasswordForm";
import { EditUsernameForm } from "./Profile/EditUsernameForm";

type EditField = "username" | "email" | "password" | null;

export default function Profile() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { clearAuth, hasScope } = useAuthStore();
  const { data: user, refetch: refetchUser } = useCurrentUser();

  const [sourceReturnUri, setSourceReturnUri] = useState<string | null>(() =>
    new URLSearchParams(location.search).get("redirect_uri")
  );
  const [showReturnButton, setShowReturnButton] = useState(false);
  const [editingField, setEditingField] = useState<EditField>(null);

  useEffect(() => {
    setSourceReturnUri(new URLSearchParams(location.search).get("redirect_uri"));
  }, [location.search]);

  useEffect(() => {
    if (!sourceReturnUri) {
      setShowReturnButton(false);
      return;
    }
    const handleMouseMove = (event: MouseEvent) => {
      const isNearTop = event.clientY <= 72;
      const isNearCenter = Math.abs(event.clientX - window.innerWidth / 2) <= 180;
      setShowReturnButton(isNearTop && isNearCenter);
    };
    window.addEventListener("mousemove", handleMouseMove);
    return () => window.removeEventListener("mousemove", handleMouseMove);
  }, [sourceReturnUri]);

  const handleEditSuccess = async () => {
    await refetchUser();
    setEditingField(null);
  };

  const handleLogout = async () => {
    try {
      await userApi.logout();
    } catch {
      // ignore logout API errors
    }
    clearAccessToken();
    clearAuth();
    queryClient.removeQueries({ queryKey: ["currentUser"] });
    toast.success("已登出");
    window.location.replace(sourceReturnUri || ROUTE_PATHS.home);
  };

  const handleBackToSource = () => {
    if (!sourceReturnUri) return;
    window.location.assign(sourceReturnUri);
  };

  const renderInfoRow = (
    field: EditField,
    label: string,
    value: React.ReactNode,
    editContent: React.ReactNode
  ) => {
    const isEditing = editingField === field;
    return (
      <div className="py-3">
        {isEditing ? (
          editContent
        ) : (
          <div className="flex items-center justify-between w-full">
            <div className="flex items-center gap-4">
              <span className="text-stone-500 w-16">{label}</span>
              <span className="text-stone-700">{value}</span>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setEditingField(null);
                setEditingField(field);
              }}
              className="rounded-lg px-1.5 py-1 text-sm text-stone-600 transition-colors hover:bg-stone-600 hover:text-[#e8e4df]"
            >
              <Pencil className="h-4 w-4 mr-1" />
              修改
            </Button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="min-h-screen flex bg-[#e8e4df]">
      {sourceReturnUri ? (
        <div className="fixed left-1/2 top-0 z-50 -translate-x-1/2 pointer-events-none">
          <Button
            type="button"
            variant="outline"
            onClick={handleBackToSource}
            className={`pointer-events-auto mt-2 bg-[#f0ece6] border-stone-300/60 text-stone-700 hover:bg-stone-200/50 rounded-xl shadow-[6px_6px_12px_#c9c5be,-6px_-6px_12px_#ffffff] transition-all duration-300 ${
              showReturnButton ? "translate-y-0 opacity-100" : "-translate-y-16 opacity-0"
            }`}
          >
            <ArrowLeft className="mr-2 h-4 w-4" />
            返回
          </Button>
        </div>
      ) : null}
      <aside className="w-64 border-r border-stone-300/60 bg-[#f0ece6] flex flex-col">
        <div className="p-4 text-center font-semibold text-lg text-stone-700">用户中心</div>
        <nav className="flex-1 space-y-1 p-2">
          <button
            type="button"
            className="w-full flex items-center gap-3 px-3 py-2 rounded-xl text-sm bg-stone-600 text-white"
          >
            <User className="h-4 w-4" />
            个人信息
          </button>
          {hasScope(["*"]) ? (
            <button
              type="button"
              onClick={() => navigate(ROUTE_PATHS.permission)}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-xl text-sm text-stone-600 hover:bg-stone-200/50"
            >
              <PanelsTopLeft className="h-4 w-4" />
              权限管理
            </button>
          ) : null}
        </nav>
        <div className="p-2">
          <Button
            onClick={handleLogout}
            className="w-full bg-red-500 text-white hover:bg-red-600 rounded-xl"
          >
            <LogOut className="mr-2 h-4 w-4" />
            登出
          </Button>
        </div>
      </aside>
      <main className="flex-1 p-6">
        <Card className="rounded-2xl border-0 bg-[#e8e4df] shadow-none w-full h-full">
          <CardHeader>
            <CardTitle className="text-stone-700">个人信息</CardTitle>
          </CardHeader>
          <CardContent>
            {renderInfoRow(
              "username",
              "用户名",
              user?.username,
              <EditUsernameForm
                defaultUsername={user?.username || ""}
                onSuccess={handleEditSuccess}
                onCancel={() => setEditingField(null)}
              />
            )}
            <Separator className="bg-stone-300/60" />
            {renderInfoRow(
              "email",
              "邮箱",
              user?.email,
              <EditEmailForm onSuccess={handleEditSuccess} onCancel={() => setEditingField(null)} />
            )}
            <Separator className="bg-stone-300/60" />
            {renderInfoRow(
              "password",
              "密码",
              "••••••••",
              <EditPasswordForm
                userEmail={user?.email || ""}
                onSuccess={handleEditSuccess}
                onCancel={() => setEditingField(null)}
              />
            )}
            <Separator className="bg-stone-300/60" />
            <div className="py-3">
              <div className="flex items-center gap-4">
                <span className="text-stone-500 w-16">用户角色</span>
                <div className="flex flex-wrap gap-2">
                  {user?.roles.map((role) => (
                    <Badge
                      key={role}
                      variant="secondary"
                      className="bg-[#f0ece6] text-stone-600 hover:bg-[#f0ece6]"
                    >
                      {role}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
