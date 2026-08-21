type SessionResetListener = () => void;

function createSessionLifecycle() {
  let generation = 0;
  const resetListeners = new Set<SessionResetListener>();

  return {
    current(): number {
      return generation;
    },

    isCurrent(capturedGeneration: number): boolean {
      return capturedGeneration === generation;
    },

    transition(): number {
      generation += 1;
      for (const listener of resetListeners) listener();
      return generation;
    },

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

export function isRefreshSnapshotCurrent(
  snapshot: RefreshSnapshot,
  currentRefreshToken: string | null
): boolean {
  return (
    sessionLifecycle.isCurrent(snapshot.generation) && currentRefreshToken === snapshot.refreshToken
  );
}
