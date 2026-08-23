import { ArrowLeft, Database, Shield, Users } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { ROUTES } from "@/config/settings";
import { DorisRoleManagement, MetadataManagement, UserManagement } from "./components";

export default function AdminPage() {
  const [activeTab, setActiveTab] = useState<"metadata" | "users" | "roles">("metadata");

  return (
    <main className="min-h-screen bg-[#f4f4f0] p-4 font-mono text-[#1e2024] md:p-8">
      <div className="mx-auto max-w-7xl space-y-6">
        {/* 顶部控制台标题 */}
        <header className="flex flex-wrap items-center justify-between gap-4 rounded border border-[#d4d4ce] bg-[#ffffff] p-5 shadow-xs">
          <div>
            <h1 className="text-xl font-bold text-[#18181b]">管理中心</h1>
          </div>
          <div className="flex gap-2.5">
            <Button asChild variant="default" size="sm" className="text-sm">
              <Link to={ROUTES.chat}>
                <ArrowLeft className="h-4 w-4 mr-1.5" />
                返回对话
              </Link>
            </Button>
          </div>
        </header>

        {/* 模块 Tab 切换导航 */}
        <div className="flex gap-2 overflow-x-auto rounded border-b border-[#d4d4ce] bg-[#ffffff] p-1.5 text-sm shadow-xs">
          <button
            type="button"
            onClick={() => setActiveTab("metadata")}
            className={`flex shrink-0 items-center gap-1.5 rounded px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${
              activeTab === "metadata"
                ? "bg-[#1e2024] text-[#ffffff]"
                : "text-[#52525b] hover:bg-[#ebebe6] hover:text-[#18181b]"
            }`}
          >
            <Database className="h-4 w-4" />
            <span>元数据管理</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("users")}
            className={`flex shrink-0 items-center gap-1.5 rounded px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${
              activeTab === "users"
                ? "bg-[#1e2024] text-[#ffffff]"
                : "text-[#52525b] hover:bg-[#ebebe6] hover:text-[#18181b]"
            }`}
          >
            <Users className="h-4 w-4" />
            <span>用户账号管理</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("roles")}
            className={`flex shrink-0 items-center gap-1.5 rounded px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${
              activeTab === "roles"
                ? "bg-[#1e2024] text-[#ffffff]"
                : "text-[#52525b] hover:bg-[#ebebe6] hover:text-[#18181b]"
            }`}
          >
            <Shield className="h-4 w-4" />
            <span>Doris 角色管理</span>
          </button>
        </div>

        {/* 1. Doris 角色与权限管理 Tab */}
        {activeTab === "roles" && <DorisRoleManagement />}

        {/* 2. 用户账号管理 Tab */}
        {activeTab === "users" && <UserManagement />}

        {/* 3. 元数据管理 Tab */}
        {activeTab === "metadata" && <MetadataManagement />}
      </div>
    </main>
  );
}
