import { isValidElement, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useText } from "../shared/ui";

function CodeBlock({ children }: { children?: ReactNode }) {
  const t = useText();
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const element = isValidElement<{ className?: string; children?: ReactNode }>(
    children,
  )
    ? children
    : null;
  const language = element?.props.className?.match(/language-([^\s]+)/)?.[1];
  const code =
    typeof element?.props.children === "string"
      ? element.props.children.replace(/\n$/, "")
      : "";
  return (
    <div className="reply-code-block">
      <div className="reply-code-header">
        <span>{language || t("代码", "Code")}</span>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(code);
              setCopied(true);
              setFailed(false);
            } catch {
              setCopied(false);
              setFailed(true);
            }
          }}
        >
          {failed
            ? t("复制失败", "Copy failed")
            : copied
              ? t("已复制", "Copied")
              : t("复制代码", "Copy code")}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

export function AssistantMarkdown({ text }: { text: string }) {
  return (
    <div className="assistant-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
