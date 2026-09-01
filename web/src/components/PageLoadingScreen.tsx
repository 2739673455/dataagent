import { DotMatrixLoader } from "@/components/DotMatrixLoader";

interface PageLoadingScreenProps {
  message: string;
}

export function PageLoadingScreen({ message }: PageLoadingScreenProps) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6 font-mono text-[#52525b]">
      <div className="flex items-center gap-2 whitespace-nowrap text-sm">
        <DotMatrixLoader className="text-[#1e2024]" label={message} />
        <span>{message}</span>
      </div>
    </div>
  );
}
