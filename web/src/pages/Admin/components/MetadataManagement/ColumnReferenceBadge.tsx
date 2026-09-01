interface ColumnReferenceBadgeProps {
  columnName: string;
  tableName: string;
}

export function ColumnReferenceBadge({ columnName, tableName }: ColumnReferenceBadgeProps) {
  return (
    <span
      className="inline-block max-w-full break-all rounded bg-[#ebebe6] px-1.5 py-0.5 font-mono text-[10px] leading-tight text-[#27272a]"
      title={`${tableName}.${columnName}`}
    >
      <span className="text-[#52525b]">{tableName}</span>
      <span className="mx-0.5 text-xs font-bold text-[#18181b]">.</span>
      <span className="font-semibold text-[#18181b]">{columnName}</span>
    </span>
  );
}
