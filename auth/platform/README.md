# 开发

项目配置：

- [./src/configs/settings.ts](./src/configs/settings.ts)

```bash
# 安装依赖
bun install

# 启动开发服务器
bun dev

# 构建生产版本
bun run build
```

# 技术栈

| 功能        | 框架           |
| ----------- | -------------- |
| 包管理器    | Bun            |
| UI 组件库   | shadcn/ui      |
| 前端框架    | React 19       |
| 路由        | React Router 7 |
| 状态管理    | Zustand        |
| HTTP 客户端 | Axios          |
| 构建工具    | Vite           |
| CSS 框架    | Tailwind CSS 4 |
| 编程语言    | TypeScript     |

# 页面

## 平台首页

- 展示平台功能卡片列表（掌柜问数、掌柜智库、深度搜索、智能客服、归因分析）
- 卡片采用新拟态风格设计，带图标、名称、描述
- 点击卡片跳转到对应功能页面或外部链接
- 需要先完成认证才能访问

## 授权回调页

- 处理认证中心授权完成后的回调
- 用授权码换取访问令牌
- 获取用户信息并跳转到原请求页面

## 404 页面

- 兜底路由，展示未找到页面提示
