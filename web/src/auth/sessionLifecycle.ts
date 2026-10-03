type SessionResetListener = () => void;

/** 以递增代次标识登录状态，向订阅者广播身份切换。 */
function createSessionLifecycle() {
  let generation = 0;
  const resetListeners = new Set<SessionResetListener>();

  return {
    /** 获取当前登录代次，供异步请求捕获。 */
    current(): number {
      return generation;
    },

    /** 判断异步请求是否仍属于当前登录代次。 */
    isCurrent(capturedGeneration: number): boolean {
      return capturedGeneration === generation;
    },

    /** 推进登录代次并通知订阅者清理用户相关状态。 */
    transition(): number {
      generation += 1;
      for (const listener of resetListeners) listener();
      return generation;
    },

    /** 注册身份切换回调，并返回取消订阅函数。 */
    subscribeReset(listener: SessionResetListener): () => void {
      resetListeners.add(listener);
      return () => resetListeners.delete(listener);
    },
  };
}

export const sessionLifecycle = createSessionLifecycle();

export interface RefreshSnapshot {
  generation: number;
  refreshToken: string;
}

/** 校验刷新请求捕获的登录代次和刷新令牌仍属于当前身份。 */
export function isRefreshSnapshotCurrent(
  snapshot: RefreshSnapshot,
  currentRefreshToken: string | null
): boolean {
  return (
    sessionLifecycle.isCurrent(snapshot.generation) && currentRefreshToken === snapshot.refreshToken
  );
}
