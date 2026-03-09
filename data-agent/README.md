# 使用方式

## 启动容器
- 准备 `docker/elasticsearch`
- 准备 `docker/volumes/embedding/bge-large-zh-v1.5`
- 准备 `docker/volumes/mysql/sql/meta.sql`
- 执行 `docker compose up -d`
- 修改目录权限 `sudo chown -R 1000:0 docker/volumes/es_data` `sudo chmod -R u+rwX,g+rwX docker/volumes/es_data`
- 重启容器 `docker compose up -d`


## 提前在数据库中导入业务数据

## 在数据库中创建元数据信息表
执行 `app/scripts/meta.sql`

## 配置表信息
编辑 `conf/meta_config.yaml`，配置表信息

## 初始化导入元数据
执行 `uv run -m app.scripts.build_meta_knowledge.py -c conf/meta_config.yaml`

## 启动服务
执行 `uv run main.py`