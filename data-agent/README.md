# 使用方式

## 启动容器
- 准备 `docker/elasticsearch`
- 准备 `docker/volumes/embedding/bge-large-zh-v1.5`
- 准备 `docker/volumes/mysql/sql/meta.sql`
- 执行 `docker compose up -d`
- 修改目录权限 `sudo chown -R 1000:0 docker/volumes/es_data` `sudo chmod -R u+rwX,g+rwX docker/volumes/es_data`
- 执行 `docker compose up -d`

## 在数据库中导入业务数据

## 修改配置信息
编辑 `conf/app_config.yaml`，配置数据库信息
编辑 `conf/meta_config.yaml`，配置表信息

## 初始化导入元数据
执行 `uv run -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml`

## 启动服务
执行 `uv run main.py`