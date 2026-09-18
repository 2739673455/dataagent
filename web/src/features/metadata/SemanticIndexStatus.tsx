interface SemanticIndexStatusProps {
  indexVersion: number;
  metaVersion: number;
}

export function SemanticIndexStatus({ indexVersion, metaVersion }: SemanticIndexStatusProps) {
  if (indexVersion >= metaVersion && indexVersion > 0) {
    return (
      <span
        className="inline-flex items-center whitespace-nowrap rounded bg-[#1e2024] px-1.5 py-0.5 font-mono text-[10px] font-medium text-[#ffffff]"
        title={`语义索引版本与元数据版本一致 (v${metaVersion})`}
      >
        已同步 (v{metaVersion})
      </span>
    );
  }

  const missing = indexVersion <= 0;
  return (
    <span
      className="inline-flex items-center whitespace-nowrap rounded bg-[#e5e5df] px-1.5 py-0.5 font-mono text-[10px] font-medium text-[#71717a]"
      title={
        missing
          ? `尚未建立语义索引，当前元数据版本 v${metaVersion}`
          : `语义索引版本 v${indexVersion} 落后于元数据版本 v${metaVersion}`
      }
    >
      {missing ? "未同步" : `待同步 (v${indexVersion}/v${metaVersion})`}
    </span>
  );
}
