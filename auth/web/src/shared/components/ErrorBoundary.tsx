import { AlertCircle } from "lucide-react";
import { Component, type ReactNode } from "react";
import { Button } from "@/shared/components/ui/button";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-[#e8e4df] p-8">
          <div className="text-center max-w-md">
            <AlertCircle className="h-12 w-12 text-red-400 mx-auto mb-4" />
            <h2 className="text-xl font-semibold text-stone-700 mb-2">页面出现了错误</h2>
            <p className="text-stone-500 mb-6 text-sm break-all">{this.state.error.message}</p>
            <Button
              onClick={() => {
                this.setState({ error: null });
                window.location.reload();
              }}
              className="bg-stone-600 hover:bg-stone-700 rounded-xl"
            >
              刷新页面
            </Button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
