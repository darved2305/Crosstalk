import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MarkdownReport } from "./markdown-report";

afterEach(cleanup);

const report = `# E0 report: \`01M4QR2ZKRX0YYT2806MMF7BK0\`

> **DEVELOPMENT RESULT, NOT REPORTABLE.** At least one session is not \`VALID\`.

## Sessions

| Session | Status |
|---|---|
| \`01M4QR302S4F3PBRPH6CXA2JHZ\` | PARTIAL |

![Balanced accuracy by regime and model](figures/e0_balanced_accuracy.png)

\`\`\`
bootstrap B = 100
\`\`\`
`;

it("renders headings, GFM tables, blockquotes and code", () => {
  const { container } = render(<MarkdownReport markdown={report} />);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("E0 report");
  expect(screen.getByRole("heading", { level: 2, name: "Sessions" })).toBeTruthy();
  expect(container.querySelectorAll("table tbody tr")).toHaveLength(1);
  expect(container.querySelector("blockquote")?.textContent).toContain("NOT REPORTABLE");
  expect(container.querySelector("pre code")?.textContent).toContain("bootstrap B = 100");
  expect(screen.getByRole("region", { name: "Report table" })).toBeTruthy();
});

it("does not request report figures the API cannot serve", () => {
  const { container } = render(<MarkdownReport markdown={report} />);
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByText("Figure: Balanced accuracy by regime and model")).toBeTruthy();
  expect(container.textContent).toContain("figures/e0_balanced_accuracy.png");
});

it("never injects raw HTML or script URLs", () => {
  const { container } = render(
    <MarkdownReport
      markdown={'<script>window.pwned = 1</script>\n\n<img src=x onerror="alert(1)">\n\n[click](javascript:alert(1))\n\n[docs](https://example.org)'}
    />,
  );
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("[onerror]")).toBeNull();
  const links = [...container.querySelectorAll("a")];
  expect(links.some((a) => (a.getAttribute("href") ?? "").startsWith("javascript:"))).toBe(false);
  const external = links.find((a) => a.textContent === "docs");
  expect(external?.getAttribute("rel")).toBe("noopener noreferrer");
});
