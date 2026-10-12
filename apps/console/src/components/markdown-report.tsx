import type { ComponentPropsWithoutRef } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  // Wide report tables scroll inside their own container, never the page.
  table: (props: ComponentPropsWithoutRef<"table">) => (
    <div className="reportTable" tabIndex={0} role="region" aria-label="Report table">
      <table {...props} />
    </div>
  ),
  // Report figures are files next to report.md that the API does not serve.
  // Show the caption and path instead of requesting an unauthenticated URL.
  img: ({ alt, src }) => (
    <span className="reportFigure" role="note">
      <strong>Figure: {alt || "untitled"}</strong>
      <span>
        <code>{typeof src === "string" ? src : ""}</code> is stored with the
        experiment artifacts; the API does not serve report images yet.
      </span>
    </span>
  ),
  a: ({ href, children }) => {
    const external = typeof href === "string" && /^https?:/i.test(href);
    return (
      <a
        href={href}
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      >
        {children}
      </a>
    );
  },
};

/**
 * Renders report.md as formatted GitHub-flavoured markdown. Raw HTML in the
 * source is skipped (never injected) and URLs pass react-markdown's safe
 * transform, so javascript: links are dropped.
 */
export function MarkdownReport({ markdown }: { markdown: string }) {
  return (
    <div className="markdownReport">
      <Markdown remarkPlugins={[remarkGfm]} skipHtml components={components}>
        {markdown}
      </Markdown>
    </div>
  );
}

