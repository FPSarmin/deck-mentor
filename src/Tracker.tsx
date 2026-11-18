import { DialogButton, Focusable, Navigation, Spinner } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import * as api from "./backend";
import type { TrackerData, TrackerItem } from "./backend";
import { readerClosed, startAsk, trackerContent } from "./store";

const ICONS: Record<string, string> = { done: "✅", missing: "⭕", missable: "⚠️", lost: "❌", unknown: "❔" };
const ORDER: Record<string, number> = { missable: 0, missing: 1, unknown: 2, lost: 3, done: 4 };
const button = { minWidth: 0, width: "auto", padding: "6px 14px", fontSize: "14px" };
const dim = { fontSize: "13px", opacity: 0.65 };

function when(ts: number) {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function Item({ it, hideWhere }: { it: TrackerItem; hideWhere: boolean }) {
  const [shown, setShown] = useState(!hideWhere);
  return (
    <Focusable onActivate={() => setShown(true)}
      style={{ display: "flex", gap: "10px", padding: "6px 8px", borderRadius: "4px", opacity: it.status === "done" ? 0.6 : 1 }}>
      <div style={{ flex: "0 0 auto" }}>{ICONS[it.status] || "❔"}</div>
      <div>
        <div style={{ fontSize: "16px" }}>{it.name}{it.kind && <span style={dim}> · {it.kind}</span>}</div>
        {it.note && <div style={{ fontSize: "13px", color: "#e8b14d" }}>{it.note}</div>}
        {it.where && (shown
          ? <div style={dim}>{it.where}</div>
          : <div style={{ ...dim, fontStyle: "italic" }}>Нажмите A, чтобы показать, где это</div>)}
      </div>
    </Focusable>
  );
}

/**
 * Full-screen tracker: what the player has collected nearby and what not, from the game's tracker
 * script run on a fresh save. Opens with the last result and refreshes itself right away.
 */
export function Tracker() {
  const { appid, game, spoiler } = trackerContent;
  const [data, setData] = useState<TrackerData | null>(trackerContent.data);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showDone, setShowDone] = useState(false);
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const leaving = useRef(false);

  const back = () => {
    if (leaving.current) return;
    leaving.current = true;
    Navigation.NavigateBack();
  };
  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await api.trackerRefresh(appid, game);
      if (r.data) setData(r.data);
      if (!r.ok) setError(r.error || "Не получилось");
    } catch (e) {
      setError(String(e));
    }
    setBusy(false);
  };
  const askClaude = (problem: string) => {
    startAsk(problem, false, "tracker", "panel");
    back();
  };

  useEffect(() => {
    refresh();
    return () => readerClosed();
  }, []);

  const groups = data?.groups || [];
  const doneTotal = groups.reduce((n, g) => n + g.items.filter((i) => i.status === "done").length, 0);

  return (
    <div style={{ marginTop: "40px", height: "calc(100% - 40px)", overflowY: "scroll", padding: "16px 48px 48px" }}>
      <Focusable onCancelButton={back}>
        <Focusable style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
          <div style={{ fontSize: "22px", fontWeight: 600, flex: 1 }}>Трекер: {game}</div>
          <DialogButton style={button} disabled={busy} onClick={refresh}>🔄 Обновить по сейву</DialogButton>
          <DialogButton style={button} onClick={() => setShowDone(!showDone)}>
            {showDone ? "Скрыть собранное" : `Показать собранное (${doneTotal})`}
          </DialogButton>
          <DialogButton style={button} onClick={back}>Назад</DialogButton>
        </Focusable>
        <div style={{ ...dim, display: "flex", alignItems: "center", gap: "8px", minHeight: "20px" }}>
          {busy && <Spinner style={{ width: "16px", height: "16px" }} />}
          {busy ? "Читаю свежий сейв…" : data?.updated ? `Обновлено ${when(data.updated)}` : ""}
          <span>· ✅ есть · ⭕ ещё нет · ⚠️ можно упустить · ❌ упущено · ❔ неясно</span>
        </div>
        {error && (
          <div style={{ margin: "8px 0", fontSize: "14px", color: "#ff8080", whiteSpace: "pre-wrap" }}>
            {error}
            <DialogButton style={{ ...button, marginTop: "6px" }} onClick={() => askClaude(error)}>
              🛠 Попросить Claude исправить трекер
            </DialogButton>
          </div>
        )}
        {data?.location && <div style={{ fontSize: "17px", marginTop: "10px" }}>📍 {data.location}</div>}
        {data?.summary && <div style={{ fontSize: "15px", opacity: 0.85 }}>{data.summary}</div>}
        {!data && !busy && !error && <div style={{ marginTop: "12px" }}>Пока пусто.</div>}

        {groups.map((g, gi) => {
          const items = g.items.slice().sort((a, b) => (ORDER[a.status] ?? 2) - (ORDER[b.status] ?? 2));
          const visible = showDone ? items : items.filter((i) => i.status !== "done");
          const done = items.length - items.filter((i) => i.status !== "done").length;
          const expanded = open[gi] ?? g.current;
          return (
            <div key={gi} style={{ marginTop: "14px" }}>
              <Focusable onActivate={() => setOpen({ ...open, [gi]: !expanded })}
                style={{ fontSize: "18px", fontWeight: 600, padding: "4px 8px", borderRadius: "4px" }}>
                {expanded ? "▾" : "▸"} {g.title} <span style={dim}>{done}/{items.length}</span>
              </Focusable>
              {expanded && visible.map((it, i) => <Item key={`${gi}-${i}-${it.name}`} it={it} hideWhere={spoiler === "hint"} />)}
              {expanded && !visible.length && <div style={{ ...dim, padding: "4px 8px" }}>Здесь всё собрано.</div>}
            </div>
          );
        })}

        {!!data?.uncovered?.length && (
          <div style={{ marginTop: "16px" }}>
            <div style={dim}>Трекер пока не знает: {data.uncovered.join(", ")}.</div>
            <DialogButton style={{ ...button, marginTop: "6px" }}
              onClick={() => askClaude(`The player has reached areas the tracker doesn't cover yet: ${data.uncovered.join(", ")}. Extend the checklist.`)}>
              🛠 Дополнить трекер (Claude, несколько минут)
            </DialogButton>
          </div>
        )}
      </Focusable>
    </div>
  );
}
