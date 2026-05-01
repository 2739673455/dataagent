export function AuthLoadingScreen() {
	const pixelCount = 100;
	const pixelSize = 3;
	const pixelGap = 1;
	const height = 8;
	const width = pixelCount * pixelSize + (pixelCount - 1) * pixelGap;
	const pixelOrder = Array.from(
		{ length: pixelCount },
		(_, index) => index,
	).sort(() => Math.random() - 0.5);

	return (
		<div className="flex min-h-screen items-center justify-center bg-white px-6">
			<svg
				className="block"
				width={width}
				height={height}
				viewBox={`0 0 ${width} ${height}`}
				fill="none"
				role="progressbar"
				aria-label="Loading"
			>
				{Array.from({ length: pixelCount }, (_, index) => {
					const x = index * (pixelSize + pixelGap);
					const order = pixelOrder[index];
					const offset = (order * 0.018).toFixed(3);

					return (
						<rect
							key={x}
							x={x}
							y="0"
							width={pixelSize}
							height={height}
							rx="1"
							fill="#1C1917"
							opacity="0"
						>
							<animate
								attributeName="opacity"
								values="0; 0; 1; 1; 0; 0"
								keyTimes="0; 0.18; 0.5; 0.68; 0.86; 1"
								dur="3.8s"
								begin={`-${offset}s`}
								repeatCount="indefinite"
							/>
						</rect>
					);
				})}
			</svg>
		</div>
	);
}

export function AuthErrorScreen({
	title,
	message,
	actionLabel,
	onAction,
}: {
	title?: string;
	message: string;
	actionLabel?: string;
	onAction?: () => void;
}) {
	return (
		<div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_top,_rgba(245,158,11,0.14),_transparent_30%),linear-gradient(135deg,_#fafaf9_0%,_#f5f5f4_45%,_#e7e5e4_100%)] px-6">
			<div className="relative w-full max-w-lg">
				<div className="mt-6 text-center">
					<h1 className="mt-3 text-2xl font-semibold tracking-tight text-stone-900 sm:text-3xl">
						{title}
					</h1>
					<p className="mt-4 text-sm leading-7 text-stone-600 sm:text-base">
						{message}
					</p>
				</div>
				<div className="mt-10 flex justify-center">
					<button
						className="h-12 rounded-xl bg-stone-900 px-8 text-base text-stone-50 shadow-[0_10px_30px_rgba(28,25,23,0.18)] transition-colors hover:bg-stone-800"
						onClick={onAction}
						type="button"
					>
						{actionLabel}
					</button>
				</div>
			</div>
		</div>
	);
}
