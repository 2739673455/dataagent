import { BarChart3, BookOpen, Bot, Database, Search } from "lucide-react";
import type { ComponentType } from "react";

export interface PlatformFeatureConfig {
	id: string;
	name: string;
	description: string;
	icon: ComponentType<{ className?: string }>;
	href: string;
	scopes: string[];
}

export const PLATFORM_FEATURES: PlatformFeatureConfig[] = [
	{
		id: "wenshu",
		name: "掌柜问数",
		description: "数据查询智能体",
		icon: Database,
		href: "https://www.baidu.com",
		scopes: ["read:data-agent"],
	},
	{
		id: "zhiku",
		name: "掌柜智库",
		description: "知识库管理与查询",
		icon: BookOpen,
		href: "#",
		scopes: [],
	},
	{
		id: "sousuo",
		name: "深度搜索",
		description: "搜索与分析智能体",
		icon: Search,
		href: "#",
		scopes: [],
	},
	{
		id: "kefu",
		name: "智能客服",
		description: "客户服务智能体",
		icon: Bot,
		href: "#",
		scopes: [],
	},
	{
		id: "guiyin",
		name: "归因分析",
		description: "数据分析与洞察",
		icon: BarChart3,
		href: "http://localhost:7300",
		scopes: ["read:insight-agent"],
	},
];
