import { Component, type ErrorInfo, type ReactNode } from "react";

export interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  public state: AppErrorBoundaryState = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    if (import.meta.env.DEV) {
      console.error("AppErrorBoundary caught error", error, errorInfo);
    }
  }

  private handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  public render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="command-card command-card-accent-amber m-6 max-w-lg border-red-500/30 p-6">
          <h2 className="command-card-title text-red-200">Something went wrong</h2>
          <p className="mt-2 text-xs leading-relaxed text-slate-400">
            {this.state.error?.message ?? "Unknown error"}
          </p>
          <button type="button" className="btn-pill btn-pill-primary mt-4 self-start text-xs" onClick={this.handleReset}>
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

/** FE-1 docs name — same implementation as {@link AppErrorBoundary}. */
export { AppErrorBoundary as ErrorBoundary };
