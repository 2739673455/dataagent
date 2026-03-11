# Insight Agent Frontend

## Stack

- Bun
- React 19
- React Router 7
- Zustand
- shadcn/ui
- Vite
- TypeScript

## Run

```bash
bun install
bun run dev
```

## Env

Create `.env.local` in this directory if needed

```bash
VITE_APP_API_BASE_URL=http://127.0.0.1:8001
VITE_AUTH_API_BASE_URL=http://127.0.0.1:7777
VITE_AUTH_CLIENT_ID=insight-agent
VITE_APP_WS_BASE_URL=ws://127.0.0.1:8001
```

If `VITE_APP_WS_BASE_URL` is omitted it will be derived from `VITE_APP_API_BASE_URL`
