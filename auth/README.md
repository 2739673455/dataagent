# 安装依赖
```bash
uv sync
```

# 启动服务
```bash
uv run -m app.main
```

# 安装测试依赖
```bash
uv sync --group test
```

# 运行测试
```bash
uv run pytest
```