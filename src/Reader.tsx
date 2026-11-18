import { DialogButton, Focusable, Navigation } from "@decky/ui";
import { splitSpoiler } from "./backend";
import { Plain } from "./Answer";
import { readerClosed, readerContent } from "./store";
import { useEffect, useRef, useState } from "react";

/**
 * Full-screen page for long answers and guides. Every paragraph is focusable,
 * so the D-pad/stick walks through the text and Steam scrolls it into view.
 */
export function Reader() {
  const { title, text } = readerContent;
  const [visible, hidden] = splitSpoiler(text);
  const [open, setOpen] = useState(false);
  const leaving = useRef(false);
  const back = () => {
    if (leaving.current) return;
    leaving.current = true;
    Navigation.NavigateBack();
  };
  useEffect(() => () => readerClosed(), []);
  const blocks = (open && hidden ? `${visible}\n\n${hidden}` : visible).split(/\n\s*\n/).filter((b) => b.trim());

  return (
    <div style={{ marginTop: "40px", height: "calc(100% - 40px)", overflowY: "scroll", padding: "16px 48px 48px" }}>
      <Focusable onCancelButton={back}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <div style={{ fontSize: "22px", fontWeight: 600 }}>{title}</div>
          <DialogButton style={{ width: "160px" }} onClick={back}>Назад</DialogButton>
        </div>
        {blocks.map((block, i) => (
          <Focusable key={i} onActivate={() => {}} style={{ padding: "6px 8px", borderRadius: "4px" }}>
            <Plain text={block} size={17} />
          </Focusable>
        ))}
        {hidden && !open && (
          <DialogButton style={{ marginTop: "12px" }} onClick={() => setOpen(true)}>🙈 Показать решение</DialogButton>
        )}
      </Focusable>
    </div>
  );
}
