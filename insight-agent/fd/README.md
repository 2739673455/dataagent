# Insight Agent 前端

## 配置

项目配置分成两类：

- 开发期配置：通过 `.env` 注入
- 项目常量：统一写在 `src/config/constants.ts`

`.env` 只保留开发/构建相关项：

- `VITE_DEV_SERVER_PORT`：本地 Vite 端口
- `VITE_DEV_AUTH_PROXY_TARGET`：本地开发代理到认证后端的地址
- `VITE_DEV_APP_PROXY_TARGET`：本地开发代理到 insight 后端服务的地址，HTTP 与 WebSocket 共用

`.env` 里的字段按必填处理：

- 缺少字段时，`bun dev` / `bun run build` 会直接报错

## 技术栈

- Bun
- React 19
- React Router 7
- Zustand
- shadcn/ui
- Vite
- TypeScript

## 启动

```bash
bun install
bun run dev
bun run build
```
