import { Fragment, ReactNode } from "react";

/** Tiny renderer for the plain-text answers: paragraphs, "- " bullets, **bold**. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <b key={i}>{part.slice(2, -2)}</b> : <Fragment key={i}>{part}</Fragment>,
  );
}

export function Answer({ text, dim }: { text: string; dim?: boolean }) {
  const lines = text.replace(/^#+\s*/gm, "").split("\n");
  return (
    <div style={{ fontSize: "13px", lineHeight: "1.4", opacity: dim ? 0.75 : 1, wordBreak: "break-word" }}>
      {lines.map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={i} style={{ height: "6px" }} />;
        const bullet = /^([-*•]|\d+[.)])\s+/.exec(trimmed);
        if (bullet) {
          const marker = /\d/.test(bullet[1]) ? bullet[1] : "•";
          return (
            <div key={i} style={{ display: "flex", gap: "6px", marginLeft: "2px" }}>
              <span style={{ flexShrink: 0 }}>{marker}</span>
              <span>{inline(trimmed.slice(bullet[0].length))}</span>
            </div>
          );
        }
        return <div key={i}>{inline(trimmed)}</div>;
      })}
    </div>
  );
}
