"use client";

export default function ErrorView({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <section className="errorPanel">
      <p className="eyebrow">REQUEST FAILED</p>
      <h2>Evidence could not be loaded</h2>
      <p>{error.message}</p>
      <button onClick={reset}>Retry</button>
    </section>
  );
}
