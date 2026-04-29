import { LayoutGrid, Loader2, User } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { buildAuthProfileUrl, useAuthStore } from "@/auth";
import { Button } from "@/components/ui/button";
import {
	PLATFORM_FEATURES,
	type PlatformFeatureConfig,
} from "@/configs/platform";
import { useCurrentUserStore } from "@/stores/user";

// 预定义的渐变色列表
const gradientColors = [
	"from-blue-500 to-cyan-500",
	"from-purple-500 to-pink-500",
	"from-orange-500 to-amber-500",
	"from-rose-500 to-pink-500",
	"from-indigo-500 to-violet-500",
	"from-teal-500 to-cyan-500",
	"from-yellow-500 to-orange-500",
	"from-sky-500 to-blue-500",
	"from-slate-500 to-gray-500",
	"from-emerald-500 to-green-500",
	"from-fuchsia-500 to-purple-500",
	"from-red-500 to-rose-500",
	"from-lime-500 to-green-500",
	"from-cyan-500 to-blue-500",
];

// 根据字符串生成固定索引（确保同一功能每次颜色相同）
function getColorIndex(str: string): number {
	let hash = 0;
	for (let i = 0; i < str.length; i++) {
		hash = str.charCodeAt(i) + ((hash << 5) - hash);
	}
	return Math.abs(hash) % gradientColors.length;
}

// 获取功能对应的颜色
function getFeatureColor(featureId: string): string {
	return gradientColors[getColorIndex(featureId)];
}

export default function Platform() {
	const navigate = useNavigate();
	const { hasScope, isAuthenticated, isLoading } = useAuthStore();
	const { user } = useCurrentUserStore();
	const [hoveredFeature, setHoveredFeature] = useState<string | null>(null);

	// 为每个功能预计算颜色
	const featureColors = useMemo(() => {
		const colors: Record<string, string> = {};
		for (const feature of PLATFORM_FEATURES) {
			colors[feature.id] = getFeatureColor(feature.id);
		}
		return colors;
	}, []);

	const filteredFeatures = PLATFORM_FEATURES;

	const handleFeatureClick = (feature: PlatformFeatureConfig) => {
		// 检查权限
		if (feature.scopes && feature.scopes.length > 0) {
			if (!hasScope(feature.scopes)) {
				toast.error("权限不足");
				return;
			}
		}

		if (feature.href === "#") {
			toast.info("功能开发中，敬请期待！");
			return;
		}

		// 判断是否为外部链接
		if (
			feature.href.startsWith("http://") ||
			feature.href.startsWith("https://")
		) {
			window.location.href = feature.href;
		} else {
			navigate(feature.href);
		}
	};

	// 加载中显示
	if (isLoading) {
		return (
			<div className="min-h-screen flex items-center justify-center bg-[#e8e4df]">
				<Loader2 className="h-8 w-8 animate-spin text-stone-600" />
			</div>
		);
	}

	// 未登录不显示内容（会被重定向）
	if (!isAuthenticated) {
		return null;
	}

	return (
		<div className="min-h-screen bg-[#e8e4df]">
			{/* 顶部导航栏 */}
			<header className="sticky top-0 z-50 w-full border-b border-stone-300/60 bg-[#f0ece6] shadow-[0_4px_12px_rgba(0,0,0,0.08)]">
				<div className="flex h-16 items-center justify-between px-6">
					{/* Logo */}
					<div className="flex items-center gap-3">
						<div className="flex h-10 w-10 items-center justify-center rounded-xl bg-stone-600 shadow-lg">
							<LayoutGrid className="h-5 w-5 text-white" />
						</div>
						<span className="text-xl font-bold text-stone-700">平台中心</span>
					</div>

					{/* 用户信息按钮 - 点击跳转到用户中心 */}
					<Button
						variant="ghost"
						className="group flex items-center gap-2 px-4 py-2 h-10 rounded-full bg-transparent border !border-transparent hover:!bg-transparent hover:!border-stone-400 hover:shadow-[8px_8px_16px_#c9c5be,-8px_-8px_16px_#ffffff] transition-all duration-300"
						onClick={() =>
							window.location.assign(buildAuthProfileUrl(window.location.href))
						}
					>
						<div className="flex h-8 w-8 items-center justify-center rounded-full bg-stone-600 group-hover:bg-stone-700 transition-colors">
							<User className="h-4 w-4 text-white" />
						</div>
						<span className="text-sm font-medium text-stone-700">
							{user?.username || "用户"}
						</span>
					</Button>
				</div>
			</header>

			{/* 主内容区 */}
			<main className="container mx-auto px-6 py-12">
				{/* 功能网格 */}
				<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6 max-w-7xl mx-auto">
					{filteredFeatures.map((feature) => (
						<button
							type="button"
							key={feature.id}
							onClick={() => handleFeatureClick(feature)}
							onMouseEnter={() => setHoveredFeature(feature.id)}
							onMouseLeave={() => setHoveredFeature(null)}
							className="group relative flex flex-col items-center p-8 rounded-2xl bg-[#f0ece6] border border-stone-300/60 shadow-[6px_6px_12px_#c9c5be,-6px_-6px_12px_#ffffff] hover:shadow-[8px_8px_16px_#c9c5be,-8px_-8px_16px_#ffffff] transition-all duration-300 text-left"
						>
							{/* 图标 */}
							<div
								className={`flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br ${featureColors[feature.id]} text-white shadow-lg mb-5 transition-transform duration-300 group-hover:scale-110 group-hover:rotate-3`}
							>
								<feature.icon className="h-8 w-8" />
							</div>

							{/* 文字内容 */}
							<h3 className="text-lg font-semibold text-stone-700 mb-1">
								{feature.name}
							</h3>
							<p className="text-sm text-stone-500 text-center">
								{feature.description}
							</p>

							{/* 悬停指示器 */}
							<div
								className={`absolute bottom-0 left-1/2 -translate-x-1/2 h-1 rounded-full bg-gradient-to-r ${featureColors[feature.id]} transition-all duration-300 ${
									hoveredFeature === feature.id ? "w-16" : "w-0"
								}`}
							/>
						</button>
					))}
				</div>
			</main>
		</div>
	);
}
