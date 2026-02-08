import uuid
from urllib.parse import urlparse

from app.config import CFG
from qcloud_cos import CosConfig, CosS3Client  #  uv add cos-python-sdk-v5

# COS Python SDK: https://cloud.tencent.com/document/product/436/12269

client = CosS3Client(
    CosConfig(
        Region=CFG.cos.region,
        SecretId=CFG.cos.secret_id,
        SecretKey=CFG.cos.secret_key,
        Token=CFG.cos.token,
        Scheme=CFG.cos.scheme,
    )
)

if not (client.bucket_exists(CFG.cos.bucket)):  # 如果存储桶不存在
    # 创建存储桶
    client.create_bucket(CFG.cos.bucket)
    # 配置 CORS 规则
    client.put_bucket_cors(
        Bucket=CFG.cos.bucket,
        CORSConfiguration={
            "CORSRule": [
                {
                    "AllowedOrigin": ["*"],
                    "AllowedMethod": ["PUT", "GET"],
                    "AllowedHeader": ["*"],
                    "ExposeHeader": ["ETag", "Content-Length", "Content-Type"],
                    "MaxAgeSeconds": 600,
                }
            ]
        },
    )


async def get_upload_presigned_url(key: str) -> str:
    """获取带预签名的上传 url"""
    return client.get_presigned_url(
        Method="PUT", Bucket=CFG.cos.bucket, Key=key, Expired=300
    )


async def get_get_presigned_url(key: str) -> str:
    """获取带预签名的下载 url"""
    return client.get_presigned_url(
        Method="GET", Bucket=CFG.cos.bucket, Key=key, Expired=300
    )


def extract_cos_key(url: str) -> str:
    """
    从 url 中提取 cos_key

    支持两种格式：
    - 数据库中存储的 cos_url:
        cos://user_id/conversation_id/images/abc.jpg
    - 前端返回的预签名 url:
        https://cos.xxx.com/user_id/conversation_id/images/abc.jpg?signature=xxx
    """

    if url.startswith("cos://"):
        return url[6:]
    else:
        parsed = urlparse(url)
        return parsed.path.lstrip("/")


def generate_cos_key(user_id: int, conversation_id: int, suffix: str) -> str:
    """生成 cos_key"""
    return f"{user_id}/{conversation_id}/{uuid.uuid4()}.{suffix}"
