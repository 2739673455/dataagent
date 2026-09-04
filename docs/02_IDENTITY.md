# 02. Identity：从账号登录到 Doris 数据授权

## 功能说明

`app/identity` 负责解决两组核心问题：请求代表哪个平台用户；该用户以哪个 Doris 身份执行查询、具备哪些数据资产的访问权限。前一组能力覆盖账号注册、密码安全管理、Access Token 与 Refresh Token 生命周期及认证依赖；后一组能力覆盖 Doris 角色管理、专用查询用户映射、SELECT 权限投影与 Row Policy 行级数据隔离。

本模块的核心职责与底层实现细节如下。

### 1. 平台账号、安全密码与并发防护

平台用户的实体模型由 `app/identity/models/account.py` 中的 `User` 定义，承载用户的身份标识与认证状态。

- **规范化与数据库唯一性约束**：用户注册与登录时，用户名与邮箱强制经过 `strip().casefold()` 规范化处理。应用层首先执行格式预检，数据库层在 `username` 与 `email` 字段上建立唯一约束，并发场景下的重复创建由数据库底层唯一性冲突保证绝对不重。
- **Argon2id 密码哈希与并发控制**：密码哈希由 `Argon2PasswordManager` 实现，遵循 `PasswordManager` Protocol。采用推荐的 Argon2id 算法参数，计算过程通过 `anyio.to_thread.run_sync` 调度至单独的线程池执行，避免 CPU 密集计算阻塞 asyncio 事件循环。同时配置 `asyncio.Semaphore(max_concurrency=2)` 限制单个进程内并发哈希计算的最大数量，防止瞬时并发登录压垮 API 进程的 CPU 与内存。
- **Dummy 校验抵御用户枚举**：登录请求中如果输入的用户名或邮箱不存在，系统自动对预先生成的假哈希值（`dummy_hash`）调用一次耗时相同的密码校验方法 `verify_dummy_password`，使“用户不存在”与“密码错误”消耗接近相同的计算时间，杜绝通过请求响应时间推测账号是否存在的侧信道攻击。
- **管理员安全不变量**：
  - 系统始终保留至少一个处于启用状态的管理员账号。更新或禁用管理员时，必须锁定安全变更锁并核验系统中有效管理员总数，当有效管理员数量小于等于 1 时禁止禁用或降权；
  - 管理员账号禁止注销或删除自身；
  - 密码长度强制校验（下限通过配置定义，通常为 6 至 128 字符）。

### 2. Access Token 与即时撤销机制

系统采用基于不对称安全设计的 JWT 认证体系。

- **JWT 规范与 Claims 约束**：访问令牌由 `JWTCodec` 签发，标准 payload 必须且仅包含六个字段：`sub`（用户主键 ID 字符串）、`auth_version`（用户的当前安全认证版本整数）、`token_type`（强制为 `"access"`）、`iat`（签发时间戳）、`exp`（过期时间戳）、`iss`（配置的签发者标识）。解码时强制要求所有上述 claims 齐全，算法与 issuer 必须完全匹配。
- **鉴权代次（auth_version）即时撤销**：虽然 Access Token 是短生命周期的无状态 JWT，但为了保证封禁账号或修改密码能即时生效，`AccessTokenAuthenticator.authenticate()` 在验证 JWT 签名与过期时间通过后，强制使用当前只读数据库连接重新加载 `User` 记录，核验 `user.is_active` 状态以及 `user.auth_version == claims.auth_version`。当用户修改密码、管理员重置权限或禁用账号时，数据库中的 `auth_version` 递增，所有此前已签发但未过期的 Access Token 在下一次请求时全部判定失效。
- **FastAPI CurrentUserDep 纯快照依赖**：请求依赖 `_get_current_user` 最终返回的是一个不可变的 `AuthenticatedUser` 数据类（`dataclass(frozen=True, slots=True)`），它完全脱离 SQLAlchemy Session 生命周期。上层业务代码只消费用户信息快照，杜绝在控制器层意外触发懒加载查询或隐式修改持久化实体。

### 3. Refresh Token 轮换链与重放检测

长期会话通过持久化的 Refresh Token 轮换机制管理。

- **数据库非明文存储**：数据库表 `refresh_tokens` 绝不保存 JWT 明文，仅保存完整 Token 字符串的 SHA-256 哈希摘要（`token_hash`）、令牌唯一标识（`id: UUID`，对应 JWT 中的 `jti`）、族标识（`family_id: UUID`）、所属用户 ID、过期时间、撤销时间戳（`revoked_at`）以及后继轮换令牌标识（`replacement_id`）。
- **悲观锁与原子轮换流程**：客户端发起 `/refresh` 请求时，`AuthService.refresh()` 在单个数据库事务中执行：
  1. 解码 Refresh Token 并计算 SHA-256 摘要；
  2. 使用 `FOR UPDATE` 依次对目标 `User` 行和 `RefreshToken` 行加行级排他锁；
  3. 核对 `user_id`、`family_id`，并使用恒定时间比对函数 `hmac.compare_digest(current.token_hash, token_digest)` 防范时序攻击；
  4. **重放检测**：若该 Token 已经被标记撤销（`revoked_at is not None`），表明该 Token 可能已被攻击者窃取重放，系统立即将该 `family_id` 对应的所有已签发 Refresh Token 全部批量撤销，阻断整个会话族；
  5. **安全轮换**：若该 Token 正常有效，则签发一对新的 Access Token 与 Refresh Token，并在事务中记录当前 Token 的 `revoked_at` 与 `replacement_id` 指向新 Token ID。
- **登出与改密级联撤销**：登出操作（`/logout`）将当前 Token 所在的整个 `family_id` 标记撤销；修改密码操作（`/change-password`）推进用户的 `auth_version` 并撤销该用户名下的所有 Refresh Token。
- **高成本认证限流**：登录和刷新接口在执行 Argon2 密码计算或 JWT 解密前，由 `AuthRateLimitService` 在 Redis 中通过滑动窗口进行限流。限流维度包括客户端 peer IP 与登录标识摘要（不存原始明文）。限流触发时抛出 429 异常并在 HTTP 头中回传 `Retry-After` 秒数。

### 4. Doris 查询身份与平台用户分离

平台用户与底层 Doris 数据库查询身份解耦。

- **三层身份模型**：
  - 平台用户（User）：HTTP 会话主体，归属于系统业务层；
  - Doris 角色（Doris Role）：数据权限的集合，多个平台用户可共享同一个 Doris 角色；
  - Doris 查询用户（Query User）：真正连接 Doris 数据库执行 SQL 的物理数据库账号。
- **DorisQueryIdentity 模型与凭据加密**：
  - 表 `doris_query_identities` 维护角色与查询用户的对应关系，字段包括 `role_name`、`query_user`、`encrypted_password`、`workload_group`、`is_default` 以及 `authorization_epoch`；
  - 查询用户密码通过 `DorisCredentialCipher` 使用 AES-256-GCM 算法进行对称加密，加密密钥仅保存在服务端配置文件中。密文持久化至 PostgreSQL，日志输出、API 响应与领域对象中绝不出现密码明文。
- **QueryPrincipalService 解析**：业务查询执行前，`QueryPrincipalService.resolve(user_id)` 读取用户绑定的 `doris_role_name`，加载其 `DorisQueryIdentity` 并解密密码，输出不可变对象 `ResolvedQueryPrincipal`。Query 模块据此向 `DorisQueryClientRegistry` 索取该角色对应的专用连接池。系统杜绝使用 Doris 管理员连接执行业务数据查询。

### 5. 资产授权投影与层级策略判定

平台在应用层维护 Doris 数据资产授权投影，用于在向大模型提供元数据提示词以及进行 SQL 静态校验时提前收窄范围。

- **四级数据资产层级与 AssetIdentity**：
  - 资产层级表示为：`data_source -> database -> table -> column`；
  - `AssetIdentity.encompasses(other)` 定义了自顶向下的覆盖逻辑：数据源级授权覆盖其下所有数据库、表和字段；数据库级授权覆盖该库内所有表和字段；表级授权覆盖该表下所有字段；字段级授权仅覆盖自身。
- **AssetAccessPolicy 的双重语义**：
  - `allows(asset)`：当前用户授权集合中是否存在某项授权完全覆盖目标资产。用于实际数据读取权限校验（例如 SQL 查询校验）；
  - `is_visible(asset)`：当前用户是否拥有目标资产本身的权限，或者是否拥有该资产下属某一子资产的权限。用于元数据目录树呈现（例如用户仅有 `orders.amount` 字段权限时，其父表 `orders` 与数据库对用户在目录中“可见”，但用户不能执行 `SELECT * FROM orders`）。
- **DorisRoleAssetGrant 投影与跨系统补偿**：
  - PostgreSQL 中的 `doris_role_asset_grants` 表充当 Doris 权限在应用层的投影；
  - 表上设置 CheckConstraint 约束：指定 `column_name` 时必须指定 `table_name`；指定 `table_name` 时必须指定 `database_name`；
  - 管理员执行授权变更时，流程为：获取认证库安全变更排他锁 -> 校验目标物理表与字段 -> 变更 Doris 底层真实权限 -> 写入/删除 PostgreSQL 授权记录 -> 提交事务。若后半段数据库事务失败，系统启动逆向补偿流程，对已成功的 Doris 操作执行回滚操作，杜绝双写漂移。
- **Row Policy 行级数据隔离**：Row Policy 的最终生效状态保存在 Doris 中。管理员创建行级策略时，应用层通过 `sqlglot` 将输入的策略表达式严格限制为单一的谓词 AST 节点，严禁包含多语句或危险函数，随后提交 Doris 验证字段合法性与布尔返回类型。
- **authorization_epoch 授权失效代次**：
  - 用户的查询经验基于 `role_name + SQL fingerprint` 聚合；
  - 当管理员回收某角色的 SELECT 权限或修改/删除 Row Policy 时，`DorisQueryIdentity` 中的 `authorization_epoch` 自动轮换生成新的 UUID；
  - 召回查询经验时强制比对当前 `authorization_epoch`，防止权限收窄后模型复用在更宽松权限下生成的 SQL 经验。

### 6. 用户注销受理与状态存储

用户注销是跨认证库、助手持久化、沙箱容器与命名卷的复杂流程，Identity 模块负责受理注销并持久化恢复锚点。

- **原子受理注销请求**：`PostgresUserDeletionStateStore.request()` 在单个认证事务中完成：
  1. 锁定安全变更排他锁；
  2. 校验目标用户有效性，若为最后一个启用管理员则拒绝注销；
  3. 将用户 `is_active` 置为 `False`；
  4. 撤销用户全部 Refresh Token；
  5. 在 `user_deletion_tasks` 表中插入或更新一条 `status='pending'` 的注销任务记录。
- **终态保护机制**：认证用户物理记录必须保留至外部资源（会话、Checkpoints、检索快照、Docker 容器与磁盘卷）全部清理完毕后，才由注销工作流调用 `complete()` 执行物理删除。`complete()` 与 `record_failure()` 均通过行级悲观锁锁定任务行，且 `completed` 是不可逆单向终态，迟到的失败回写绝不覆盖已完成状态。

---

## 核心实现代码与模块架构

### 1. 持久化数据模型与约束实现

包含平台用户、刷新令牌、Doris 查询身份、资产授权投影以及注销任务记录：

```python
# app/identity/models/account.py
"""平台用户与认证令牌模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AuthBase


class User(AuthBase):
    """平台用户。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    auth_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    doris_role_name: Mapped[str | None] = mapped_column(
        ForeignKey("doris_query_identities.role_name", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RefreshToken(AuthBase):
    """可轮换的刷新令牌记录（仅存摘要）。"""

    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replacement_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_user_id", "user_id"),
    )
```

```python
# app/identity/models/doris.py
"""Doris 查询身份与权限投影模型。"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AuthBase

DORIS_ROLE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def normalize_doris_role_name(value: str) -> str:
    """校验并规范化 Doris 角色名。"""
    normalized = value.strip()
    if DORIS_ROLE_NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Doris 角色名称格式无效")
    return normalized


class AssetScope(StrEnum):
    """数据资产授权粒度。"""

    DATA_SOURCE = "data_source"
    DATABASE = "database"
    TABLE = "table"
    COLUMN = "column"


@dataclass(frozen=True, slots=True)
class DorisRowPolicy:
    """Doris 角色当前生效的行级过滤策略。"""

    policy_name: str
    catalog_name: str
    database_name: str
    table_name: str
    policy_type: Literal["RESTRICTIVE", "PERMISSIVE"]
    predicate: str


class DorisQueryIdentity(AuthBase):
    """Doris 数据角色对应的稳定共享查询身份。"""

    __tablename__ = "doris_query_identities"

    role_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    query_user: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    workload_group: Mapped[str] = mapped_column(String(64), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    authorization_epoch: Mapped[UUID] = mapped_column(
        nullable=False,
        default=uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DorisRoleAssetGrant(AuthBase):
    """角色的数据资产访问授权投影。"""

    __tablename__ = "doris_role_asset_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    role_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("doris_query_identities.role_name", ondelete="CASCADE"),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    data_source: Mapped[str] = mapped_column(String(64), nullable=False)
    database_name: Mapped[str | None] = mapped_column(String(128))
    table_name: Mapped[str | None] = mapped_column(String(128))
    column_name: Mapped[str | None] = mapped_column(String(128))
    resource_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "role_name",
            "resource_key",
            name="uq_doris_role_asset_grants_role_resource",
        ),
        Index("ix_doris_role_asset_grants_role_name", "role_name"),
        CheckConstraint(
            "scope IN ('data_source', 'database', 'table', 'column')",
            name="ck_doris_role_asset_grants_scope",
        ),
        CheckConstraint(
            "(scope != 'column') OR (table_name IS NOT NULL AND column_name IS NOT NULL)",
            name="ck_doris_role_asset_grants_column_target",
        ),
        CheckConstraint(
            "(scope != 'table') OR (database_name IS NOT NULL AND table_name IS NOT NULL AND column_name IS NULL)",
            name="ck_doris_role_asset_grants_table_target",
        ),
        CheckConstraint(
            "(scope != 'database') OR (database_name IS NOT NULL AND table_name IS NULL AND column_name IS NULL)",
            name="ck_doris_role_asset_grants_database_target",
        ),
        CheckConstraint(
            "(scope != 'data_source') OR (database_name IS NULL AND table_name IS NULL AND column_name IS NULL)",
            name="ck_doris_role_asset_grants_data_source_target",
        ),
    )
```

```python
# app/identity/models/lifecycle.py
"""用户生命周期模型。"""

from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AuthBase


class UserDeletionTask(AuthBase):
    """跨存储用户注销任务。"""

    __tablename__ = "user_deletion_tasks"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed')",
            name="ck_user_deletion_task_status",
        ),
    )
```

### 2. 密码管理与 JWT 编解码实现

```python
# app/identity/services/auth.py（核心实现）
"""用户认证与令牌生命周期服务。"""

import asyncio
import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import jwt
from anyio import to_thread
from loguru import logger
from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError

from app.identity import errors as auth_error
from app.identity.models.account import RefreshToken, User
from app.identity.repositories.identity import IdentityPGRepo
from app.shared.config.app_config import AuthConfig

ARGON2_MAX_CONCURRENCY = 2


class PasswordManager(Protocol):
    """异步密码哈希接口。"""

    async def hash(self, password: str) -> str: ...
    async def verify(self, password: str, password_hash: str) -> bool: ...
    async def verify_dummy_password(self, password: str) -> None: ...


class Argon2PasswordManager:
    """基于 Argon2id 的异步密码哈希实现。"""

    def __init__(self, *, max_concurrency: int = ARGON2_MAX_CONCURRENCY) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency 必须为正整数")
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash("dataagent-dummy-password")
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def hash(self, password: str) -> str:
        async with self._semaphore:
            return await to_thread.run_sync(self._password_hash.hash, password)

    async def verify(self, password: str, password_hash: str) -> bool:
        async with self._semaphore:
            return await to_thread.run_sync(
                self._password_hash.verify,
                password,
                password_hash,
            )

    async def verify_dummy_password(self, password: str) -> None:
        """执行等价开销校验以防止用户枚举。"""
        await self.verify(password, self._dummy_hash)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """脱离数据库会话的认证用户快照。"""

    id: int
    username: str
    email: str
    auth_version: int
    is_active: bool
    is_admin: bool
    doris_role_name: str | None
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "AuthenticatedUser":
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            auth_version=user.auth_version,
            is_active=user.is_active,
            is_admin=user.is_admin,
            doris_role_name=user.doris_role_name,
            created_at=user.created_at,
        )


class JWTCodec:
    """应用 JWT 编解码器。"""

    def __init__(self, config: AuthConfig) -> None:
        self._config = config
        self._secret = config.jwt_secret.get_secret_value()

    def issue_access_token(self, user: User, now: datetime) -> str:
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "auth_version": user.auth_version,
            "token_type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=self._config.access_token_minutes),
            "iss": self._config.issuer,
        }
        return jwt.encode(payload, self._secret, algorithm=self._config.jwt_algorithm)

    def issue_refresh_token(
        self, user_id: int, token_id: UUID, family_id: UUID, now: datetime
    ) -> str:
        return jwt.encode(
            {
                "sub": str(user_id),
                "jti": str(token_id),
                "family_id": str(family_id),
                "token_type": "refresh",
                "iat": now,
                "exp": now + timedelta(days=self._config.refresh_token_days),
                "iss": self._config.issuer,
            },
            self._secret,
            algorithm=self._config.jwt_algorithm,
        )

    def decode_access_token(self, token: str) -> tuple[int, int]:
        """校验并返回 (user_id, auth_version)。"""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._config.jwt_algorithm],
                issuer=self._config.issuer,
                leeway=5,
                options={
                    "require": ["sub", "auth_version", "token_type", "iat", "exp", "iss"]
                },
            )
        except jwt.PyJWTError as exc:
            raise auth_error.InvalidTokenError from exc
        if payload.get("token_type") != "access":
            raise auth_error.InvalidTokenError(detail="非预期的令牌类型")
        return int(payload["sub"]), int(payload["auth_version"])

    def decode_refresh_token(self, token: str) -> tuple[int, UUID, UUID]:
        """校验并返回 (user_id, token_id, family_id)。"""
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._config.jwt_algorithm],
                issuer=self._config.issuer,
                leeway=5,
                options={
                    "require": [
                        "sub",
                        "jti",
                        "family_id",
                        "token_type",
                        "iat",
                        "exp",
                        "iss",
                    ]
                },
            )
        except jwt.PyJWTError as exc:
            raise auth_error.InvalidTokenError from exc
        if payload.get("token_type") != "refresh":
            raise auth_error.InvalidTokenError(detail="非预期的令牌类型")
        return (
            int(payload["sub"]),
            UUID(str(payload["jti"])),
            UUID(str(payload["family_id"])),
        )


class AccessTokenAuthenticator:
    """使用只读会话校验访问令牌并验证用户状态与代次。"""

    def __init__(self, repo: IdentityPGRepo, config: AuthConfig) -> None:
        self._repo = repo
        self._codec = JWTCodec(config)

    async def authenticate(self, access_token: str) -> AuthenticatedUser:
        user_id, auth_version = self._codec.decode_access_token(access_token)
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise auth_error.InvalidTokenError
        if not user.is_active:
            raise auth_error.InactiveUserError
        if user.auth_version != auth_version:
            raise auth_error.InvalidTokenError
        return AuthenticatedUser.from_user(user)
```

### 3. 登录与刷新令牌轮换逻辑实现

```python
# app/identity/services/auth.py（续）
class AuthService:
    """登录、刷新与密码会话管理。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        config: AuthConfig,
        password_manager: PasswordManager,
    ) -> None:
        self._repo = repo
        self._config = config
        self._password_manager = password_manager
        self._codec = JWTCodec(config)

    async def login(self, identifier: str, password: str) -> tuple[User, str, str]:
        """校验凭据并签发 Token 对。"""
        normalized = identifier.strip().casefold()
        async with self._repo.session.begin():
            user = (
                await self._repo.get_user_by_email_for_update(normalized)
                if "@" in normalized
                else await self._repo.get_user_by_username_for_update(normalized)
            )
            if user is None:
                await self._password_manager.verify_dummy_password(password)
                raise auth_error.InvalidCredentialsError
            if not await self._password_manager.verify(password, user.password_hash):
                raise auth_error.InvalidCredentialsError
            if not user.is_active:
                raise auth_error.InactiveUserError

            now = datetime.now(UTC)
            access_token = self._codec.issue_access_token(user, now)
            family_id = uuid4()
            token_id = uuid4()
            refresh_token = self._codec.issue_refresh_token(
                user.id, token_id, family_id, now
            )
            await self._repo.add_refresh_token(
                RefreshToken(
                    id=token_id,
                    family_id=family_id,
                    user_id=user.id,
                    token_hash=hashlib.sha256(refresh_token.encode()).hexdigest(),
                    expires_at=now + timedelta(days=self._config.refresh_token_days),
                )
            )
        return user, access_token, refresh_token

    async def refresh(self, refresh_token: str) -> tuple[User, str, str]:
        """轮换刷新令牌并防止重放攻击。"""
        user_id, token_id, family_id = self._codec.decode_refresh_token(refresh_token)
        token_digest = hashlib.sha256(refresh_token.encode()).hexdigest()
        now = datetime.now(UTC)

        async with self._repo.session.begin():
            user = await self._repo.get_user_by_id_for_update(user_id)
            current = await self._repo.get_refresh_token_for_update(token_id)
            if (
                user is None
                or current is None
                or current.user_id != user_id
                or current.family_id != family_id
                or not hmac.compare_digest(current.token_hash, token_digest)
            ):
                raise auth_error.InvalidTokenError

            if current.revoked_at is not None:
                # 检测到已撤销 Token 重放，批量撤销整族令牌
                await self._repo.revoke_refresh_family(family_id, now)
                raise auth_error.RefreshTokenReuseError(detail="该刷新令牌已被注销")

            if not user.is_active:
                raise auth_error.InactiveUserError

            # 正常轮换：签发新令牌并链接 replacement
            replacement_id = uuid4()
            new_access_token = self._codec.issue_access_token(user, now)
            new_refresh_token = self._codec.issue_refresh_token(
                user.id, replacement_id, family_id, now
            )
            await self._repo.add_refresh_token(
                RefreshToken(
                    id=replacement_id,
                    family_id=family_id,
                    user_id=user.id,
                    token_hash=hashlib.sha256(new_refresh_token.encode()).hexdigest(),
                    expires_at=now + timedelta(days=self._config.refresh_token_days),
                )
            )
            self._repo.rotate_refresh_token(current, replacement_id, now)
        return user, new_access_token, new_refresh_token
```

### 4. FastAPI 认证与权限依赖注入实现

```python
# app/identity/api/auth/dependencies.py
"""FastAPI 认证与权限依赖。"""

from typing import Annotated
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.identity import errors as auth_error
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.auth import (
    AccessTokenAuthenticator,
    AuthenticatedUser,
)
from app.shared.clients.postgres_client_manager import auth_postgres_client_manager
from app.shared.config.app_config import cfg

_bearer = HTTPBearer(auto_error=False)


async def _get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthenticatedUser:
    """解析 Bearer Token 并核查数据库。"""
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise auth_error.AuthenticationRequiredError
    async with auth_postgres_client_manager.session() as session:
        return await AccessTokenAuthenticator(
            IdentityPGRepo(session),
            cfg.auth,
        ).authenticate(credentials.credentials)


CurrentUserDep = Annotated[AuthenticatedUser, Depends(_get_current_user)]


async def _require_admin(current_user: CurrentUserDep) -> AuthenticatedUser:
    """强制要求管理员权限。"""
    if not current_user.is_admin:
        raise auth_error.PermissionDeniedError(detail="需要平台管理员权限")
    return current_user


AdminUserDep = Annotated[AuthenticatedUser, Depends(_require_admin)]
```

### 5. Doris 查询身份解析与凭据加解密实现

```python
# app/identity/services/credential.py
"""Doris 查询凭据加解密。"""

import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DorisCredentialCipher:
    """使用 AES-256-GCM 加解密 Doris 查询密码。"""

    def __init__(self, base64_key: str) -> None:
        self._key = base64.b64decode(base64_key)
        self._aesgcm = AESGCM(self._key)

    def encrypt(self, plain_text: str) -> str:
        nonce = b"\x00" * 12  # 示例固定 nonce 或使用随机 nonce
        ciphertext = self._aesgcm.encrypt(nonce, plain_text.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, encrypted_text: str) -> str:
        raw = base64.b64decode(encrypted_text.encode("ascii"))
        nonce, ciphertext = raw[:12], raw[12:]
        return self._aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
```

```python
# app/identity/services/query_principal.py
"""按用户解析受限的 Doris 查询身份。"""

from dataclasses import dataclass, field
from uuid import UUID

from app.identity import errors as auth_error
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.credential import DorisCredentialCipher


@dataclass(frozen=True, slots=True)
class ResolvedQueryPrincipal:
    """服务端为一次查询解析出的受限 Doris 身份。"""

    role_name: str
    authorization_epoch: UUID
    query_user: str
    workload_group: str
    password: str = field(repr=False)


class QueryPrincipalService:
    """解析当前用户绑定的 Doris 查询身份。"""

    def __init__(self, repo: IdentityPGRepo, cipher: DorisCredentialCipher) -> None:
        self._repo = repo
        self._cipher = cipher

    async def resolve(self, user_id: int) -> ResolvedQueryPrincipal:
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise auth_error.UserNotFoundError
        if not user.is_active:
            raise auth_error.InactiveUserError
        if user.doris_role_name is None:
            raise RuntimeError("用户尚未配置 Doris 角色")
        identity = await self._repo.get_query_identity(user.doris_role_name)
        if identity is None:
            raise RuntimeError("用户的 Doris 角色尚未配置可用的查询身份")
        return ResolvedQueryPrincipal(
            role_name=user.doris_role_name,
            authorization_epoch=identity.authorization_epoch,
            query_user=identity.query_user,
            password=self._cipher.decrypt(identity.encrypted_password),
            workload_group=identity.workload_group,
        )
```

### 6. 资产策略判断与授权投影实现

```python
# app/identity/services/authorization.py（核心实现）
"""RBAC 与数据资产白名单授权服务。"""

from dataclasses import dataclass
from uuid import UUID
from app.shared.contracts.assets import asset_resource_key


@dataclass(frozen=True)
class AssetIdentity:
    """层级化数据资产标识。"""

    data_source: str
    database_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None

    def __post_init__(self) -> None:
        if not self.data_source:
            raise ValueError("data_source 不能为空")
        if self.column_name is not None and self.table_name is None:
            raise ValueError("指定 column_name 时必须同时指定 table_name")
        if self.table_name is not None and self.database_name is None:
            raise ValueError("指定 table_name 时必须同时指定 database_name")

    def encompasses(self, other: "AssetIdentity") -> bool:
        """判断当前授权是否向下覆盖目标资产。"""
        own_parts = (
            self.data_source,
            self.database_name,
            self.table_name,
            self.column_name,
        )
        other_parts = (
            other.data_source,
            other.database_name,
            other.table_name,
            other.column_name,
        )
        return all(
            own is None or own == target
            for own, target in zip(own_parts, other_parts, strict=True)
        )


@dataclass(frozen=True)
class AssetAccessPolicy:
    """用户资产访问策略快照。"""

    user_id: int
    role_name: str | None = None
    authorization_epoch: UUID | None = None
    grants: frozenset[AssetIdentity] = frozenset()

    def allows(self, asset: AssetIdentity) -> bool:
        """判断是否拥有目标资产的完整读取访问权（用于 SQL 执行）。"""
        return any(grant.encompasses(asset) for grant in self.grants)

    def is_visible(self, asset: AssetIdentity) -> bool:
        """判断资产或其任一下级资产是否可见（用于目录展示）。"""
        return self.allows(asset) or any(
            asset.encompasses(grant) for grant in self.grants
        )
```

### 7. 用户注销状态存储实现

```python
# app/identity/services/user_deletion_store.py
"""用户注销认证状态存储。"""

from datetime import datetime
from app.identity import errors as auth_error
from app.identity.repositories.identity import IdentityPGRepo
from app.shared.clients.postgres_client_manager import PostgresClientManager


class PostgresUserDeletionStateStore:
    """在认证数据库中维护注销状态与终态保护。"""

    def __init__(self, postgres: PostgresClientManager) -> None:
        self._postgres = postgres

    async def request(self, user_id: int, requested_at: datetime) -> bool:
        """禁用用户、吊销令牌并创建注销任务。"""
        async with self._postgres.session() as session:
            repo = IdentityPGRepo(session)
            async with session.begin():
                await repo.lock_security_mutation()
                user = await repo.get_user_by_id_for_update(user_id)
                task = await repo.get_user_deletion_task_for_update(user_id)
                if user is None:
                    if task is not None and task.status == "completed":
                        return False
                    raise auth_error.UserNotFoundError
                if user.is_active and user.is_admin and await repo.count_admins() <= 1:
                    raise auth_error.LastAdministratorError
                await repo.set_user_active(user, False)
                await repo.revoke_user_refresh_tokens(user.id, requested_at)
                await repo.enqueue_user_deletion(user.id, requested_at)
        return True

    async def complete(self, user_id: int, completed_at: datetime) -> None:
        """物理删除认证用户并将注销任务标记为 completed 终态。"""
        async with self._postgres.session() as session:
            repo = IdentityPGRepo(session)
            async with session.begin():
                await repo.lock_security_mutation()
                task = await repo.get_user_deletion_task_for_update(user_id)
                if task is None:
                    raise RuntimeError("用户注销任务记录不存在")
                user = await repo.get_user_by_id_for_update(user_id)
                if user is not None:
                    await repo.delete_user(user)
                await repo.complete_user_deletion(task, completed_at)
```

---

## 阶段学习与验证要点

### 阶段 1：验证账号规范化与密码安全

1. **输入规范化验证**：注册账号 `  AdminUser@example.com  `，验证数据库中保存的实际为小写无空格 `adminuser@example.com`。
2. **Argon2id 密码哈希与并发截流验证**：同时发起 10 个并发注册请求，验证进程内 Semaphore 将并发计算限制在 2 个线程内，事件循环持续响应健康检查。
3. **Dummy Verify 时间恒定性验证**：分别对存在的用户和不存在的用户发起密码错误的登录请求，对比响应延迟，验证两者的耗时分布基本一致。

### 阶段 2：验证 Access Token 即时失效与 Refresh Token 轮换

1. **改密即时失效验证**：用户登录获取 Access Token 后调用改密接口，成功后立即使用原 Access Token 请求受保护接口 `/api/v1/auth/me`，验证系统因 `auth_version` 不匹配返回 401 错误。
2. **Refresh Token 正常轮换验证**：调用 `/refresh` 接口，验证旧 Refresh Token 的 `revoked_at` 被打标且返回新的 Token 对。
3. **重放攻击拦截验证**：使用已被轮换的旧 Refresh Token 再次调用 `/refresh`，验证系统捕获重放攻击，并将该用户当前 family 下的所有 Refresh Token 全部撤销。

### 阶段 3：验证 Doris 查询身份与资产授权

1. **查询身份解析隔离验证**：调用 `QueryPrincipalService.resolve()`，验证输出的密码已完成解密，但领域对象脱离 ORM 会话。
2. **层级授权判定差异验证**：授予角色 `orders.amount` 字段权限，调用 `policy.is_visible(orders)` 返回 `True`，调用 `policy.allows(orders)` 返回 `False`。
3. **authorization_epoch 轮换验证**：管理员回收该角色某字段权限，验证 `doris_query_identities` 表中的 `authorization_epoch` 生成了新的 UUID。
