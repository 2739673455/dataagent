SELECT 'CREATE DATABASE auth OWNER atguigu'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'auth'
)
\gexec

SELECT 'CREATE DATABASE meta OWNER atguigu'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'meta'
)
\gexec

SELECT 'CREATE DATABASE langgraph OWNER atguigu'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'langgraph'
)
\gexec
