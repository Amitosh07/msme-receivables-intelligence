import React, { Component, ErrorInfo, ReactNode } from "react";
import { AlertCircle } from "./Icons";

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
  fallbackMessage?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Uncaught error caught by ErrorBoundary:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="state error" role="alert" style={{ margin: "24px 0" }}>
          <AlertCircle size={32} />
          <div>
            <strong>{this.props.fallbackTitle || "We couldn’t display this view"}</strong>
            <span>
              {this.state.error?.message ||
                this.props.fallbackMessage ||
                "An unexpected rendering error occurred. Please try again."}
            </span>
          </div>
          <button
            className="button primary"
            onClick={() => {
              this.setState({ hasError: false, error: null });
              window.location.reload();
            }}
          >
            Reload page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
