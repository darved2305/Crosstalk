import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./styles.css";

export const metadata: Metadata = {
  title: "Crosstalk Research Console",
  description: "Validated encrypted-stream experiment evidence",
};

export const dynamic = "force-dynamic";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <div>
            <span className="eyebrow">CROSSTALK</span>
            <h1>Research console</h1>
          </div>
          <nav aria-label="Research views">
            <Link href="/">Runs</Link>
            <Link href="/traces">Trace explorer</Link>
            <Link href="/results">Attack results</Link>
            <Link href="/frontier">Leakage / cost</Link>
          </nav>
        </header>
        <main>{children}</main>
        <footer>Observer evidence is primary. Oracle data is validation-only.</footer>
      </body>
    </html>
  );
}
