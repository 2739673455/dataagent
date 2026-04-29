from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

DBSessionContextFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
