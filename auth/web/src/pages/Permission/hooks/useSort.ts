import { useState } from "react";

type SortOrder = "asc" | "desc";

interface SortState<T> {
	field: T;
	order: SortOrder;
}

export function useSort<T extends string>(defaultField: T) {
	const [sort, setSort] = useState<SortState<T>>({
		field: defaultField,
		order: "asc",
	});

	const toggle = (field: T) => {
		setSort((s) =>
			s.field === field
				? { ...s, order: s.order === "asc" ? "desc" : "asc" }
				: { field, order: "asc" },
		);
	};

	return { sort, toggle };
}

export type { SortState, SortOrder };
