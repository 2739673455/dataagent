import { Loader2 } from "lucide-react";

export function AuthLoadingScreen() {
	return (
		<div className="flex h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-slate-100">
			<Loader2 className="h-8 w-8 animate-spin text-stone-600" />
		</div>
	);
}
