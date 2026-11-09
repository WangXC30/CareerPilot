import { Component, Suspense, lazy, type ReactNode } from "react";

const Markdown = lazy(() =>
  import("./assistant-markdown").then((module) => ({ default: module.AssistantMarkdown })),
);

class MarkdownBoundary extends Component<
  { text: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? (
      <div className="assistant-markdown">{this.props.text}</div>
    ) : (
      this.props.children
    );
  }
}

export function AssistantRender({ text }: { text: string }) {
  if (!text) return null;
  const fallback = <div className="assistant-markdown">{text}</div>;
  return (
    <MarkdownBoundary text={text}>
      <Suspense fallback={fallback}>
        <Markdown text={text} />
      </Suspense>
    </MarkdownBoundary>
  );
}
