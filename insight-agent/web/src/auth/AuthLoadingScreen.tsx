export function AuthLoadingScreen() {
  const pixelCount = 100;
  const pixelSize = 3;
  const pixelGap = 1;
  const height = 8;
  const width = pixelCount * pixelSize + (pixelCount - 1) * pixelGap;
  const pixelOrder = Array.from({ length: pixelCount }, (_, index) => index).sort(
    () => Math.random() - 0.5
  );

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
