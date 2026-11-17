import { ButtonItem, Focusable } from "@decky/ui";
import { Fragment, ReactNode, useState } from "react";
import { splitSpoiler } from "./backend";

/** Tiny renderer for the plain-text answers: paragraphs, "- " bullets, **bold**. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <b key={i}>{part.slice(2, -2)}</b> : <Fragment key={i}>{part}</Fragment>,
  );
}

export function Plain({ text, dim, size = 13 }: { text: string; dim?: boolean; size?: number }) {
  const lines = text.replace(/^#+\s*/gm, "").split("\n");
  return (
    <div style={{ fontSize: `${size}px`, lineHeight: "1.45", opacity: dim ? 0.75 : 1, wordBreak: "break-word" }}>
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

/**
 * An answer whose solution part (after the spoiler marker) stays hidden until asked for.
 * `actions` gets the text that is currently shown (for "read aloud" and "full screen").
 */
export function Answer({ text, dim, size, actions }: {
  text: string; dim?: boolean; size?: number; actions?: (shown: string, full: boolean) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [visible, hidden] = splitSpoiler(text);
  return (
    <div>
      {visible && <Plain text={visible} dim={dim} size={size} />}
      {hidden && !open && (
        <Focusable style={{ marginTop: "6px" }}>
          <ButtonItem layout="below" onClick={() => setOpen(true)}>🙈 Показать решение</ButtonItem>
        </Focusable>
      )}
      {hidden && open && (
        <div style={{ marginTop: "8px", paddingTop: "6px", borderTop: "1px dashed rgba(255,255,255,0.25)" }}>
          <Plain text={hidden} dim={dim} size={size} />
        </div>
      )}
      {actions?.(open && hidden ? `${visible}\n\n${hidden}` : visible, open || !hidden)}
    </div>
  );
}
