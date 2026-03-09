# 使用方式

## 提前在数据库中导入业务数据

## 在数据库中创建元数据信息表
执行 `app/scripts/meta.sql`

## 配置表信息
编辑 `conf/meta_config.yaml`，配置表信息

## 初始化导入元数据
执行 `uv run -m app.scripts.build_meta_knowledge.py -c conf/meta_config.yaml`

## 启动服务
执行 `uv run main.py`