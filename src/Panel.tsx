import { ButtonItem, DialogButton, Focusable, PanelSection, PanelSectionRow, Spinner, ToggleField } from "@decky/ui";
import { LazyTextField } from "./Fields";
import { ReactNode, useEffect, useState } from "react";
import { useQuickAccessVisible } from "@decky/api";
import * as api from "./backend";
import type { CliInfo, Limits, State } from "./backend";
import { Answer } from "./Answer";
import { SettingsRows } from "./Settings";
import {
  openReader, runningGame, setCachedSettings, setCurrent, setLimits, setPanelOpen, startAsk, useCurrent, useLimits,
} from "./store";

const QUICK = [
  { label: "📷 Что мне делать?", q: "Что мне сейчас делать? Подскажи по скриншоту.", shot: true },
  { label: "📷 Как пройти это?", q: "Как пройти то, что на скриншоте: бой, головоломку или препятствие?", shot: true },
  { label: "📷 Объясни экран", q: "Объясни, что сейчас на экране: интерфейс, предметы, характеристики.", shot: true },
  { label: "🧭 Что я пропустил?", q: "Что важного я мог пропустить на текущем этапе игры?", shot: false },
];

const box = { padding: "8px 10px", borderRadius: "6px", background: "rgba(255,255,255,0.06)" };
const small = { fontSize: "12px", opacity: 0.7 };
const smallButton = { minWidth: 0, flex: 1, padding: "6px 8px", fontSize: "12px" };

function Setup({ cli, onRetry }: { cli: CliInfo; onRetry: () => void }) {
  const steps = !cli.found
    ? [
        "1. Нажмите Steam → Питание → Переключиться на рабочий стол.",
        "2. Откройте Konsole и выполните:",
        "curl -fsSL https://claude.ai/install.sh | bash",
        "3. Затем выполните claude, введите /login и войдите в аккаунт с подпиской.",
        "4. Вернитесь в игровой режим.",
      ]
    : [
        "Claude Code установлен, но вход не выполнен.",
        "В режиме рабочего стола откройте Konsole, выполните claude и введите /login.",
        "Или в настройках ниже выберите вход по API-ключу.",
      ];
  return (
    <PanelSection title="Настройка">
      <PanelSectionRow>
        <div style={{ ...box, fontSize: "12px", lineHeight: "1.45" }}>
          {steps.map((s, i) => (
            <div key={i} style={s.startsWith("curl") ? { fontFamily: "monospace", wordBreak: "break-all", margin: "4px 0" } : {}}>
              {s}
            </div>
          ))}
          {cli.error && <div style={{ ...small, marginTop: "6px" }}>{cli.error}</div>}
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onRetry}>Проверить снова</ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

const pad = (n: number) => String(n).padStart(2, "0");
const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function when(ts: number) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return d.toDateString() === now.toDateString() ? time : `${d.getDate()} ${MONTHS[d.getMonth()]} ${time}`;
}

/** "5 ч: 11% · неделя: 67%" from the last rate-limit event Claude Code reported. */
function LimitsLine({ limits }: { limits: Limits | null }) {
  const windows = limits?.windows;
  if (!windows) return null;
  const now = Date.now() / 1000;
  const parts = ([["five_hour", "5 ч"], ["seven_day", "неделя"]] as const)
    .map(([key, label]) => {
      const w = windows[key];
      if (!w || typeof w.utilization !== "number") return null;
      const reset = w.resets_at && w.resets_at > now;
      const pct = reset ? Math.round(w.utilization * 100) : 0;
      const color = pct >= 90 ? "#ff8080" : pct >= 75 ? "#e8b14d" : undefined;
      return (
        <span key={key} style={{ color }}>
          {label}: {pct}%{reset && pct > 0 ? ` (сброс ${when(w.resets_at!)})` : ""}
        </span>
      );
    })
    .filter(Boolean);
  if (!parts.length) return null;
  const blocked = limits?.status && !["allowed", "allowed_warning"].includes(limits.status) && (limits.resets_at || 0) > now;
  return (
    <PanelSectionRow>
      <div style={small}>
        Лимит подписки: {parts.reduce<ReactNode[]>((acc, p, i) => (i ? [...acc, " · ", p] : [p]), [])}
        {blocked && <div style={{ color: "#ff8080" }}>Лимит исчерпан до {when(limits!.resets_at!)}</div>}
      </div>
    </PanelSectionRow>
  );
}

/** Buttons under an answer: full-screen reader and, when TTS is on, read aloud. */
function AnswerActions({ title, text, shown, full, tts }: { title: string; text: string; shown: string; full: boolean; tts: boolean }) {
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState("");
  const speak = async () => {
    setError("");
    if (speaking) {
      await api.stopSpeaking();
      setSpeaking(false);
      return;
    }
    setSpeaking(true);
    const r = await api.speak(shown);
    setSpeaking(false);
    if (!r.ok) setError(r.error || "Не получилось озвучить");
  };
  return (
    <>
      <Focusable style={{ display: "flex", gap: "6px", marginTop: "8px" }}>
        <DialogButton style={smallButton} onClick={() => openReader(title, full ? shown : text)}>⤢ На весь экран</DialogButton>
        {tts && <DialogButton style={smallButton} onClick={speak}>{speaking ? "⏹ Стоп" : "🔊 Прочитать"}</DialogButton>}
      </Focusable>
      {error && <div style={{ fontSize: "12px", color: "#ff8080", marginTop: "4px" }}>{error}</div>}
    </>
  );
}

function CurrentAnswer({ tts }: { tts: boolean }) {
  const cur = useCurrent();
  if (!cur) return null;
  const busy = !cur.done;
  const text = cur.done ? cur.answer || cur.text : cur.text;
  const title = cur.mode === "deep" ? "🔬 Глубокий разбор" : cur.question;
  return (
    <PanelSectionRow>
      <div style={box}>
        <div style={{ ...small, marginBottom: "4px" }}>{cur.mode === "deep" ? title : `${cur.shot ? "📷 " : ""}${title}`}</div>
        {busy && (
          <div style={{ display: "flex", alignItems: "center", gap: "8px", ...small, marginBottom: text ? "6px" : 0 }}>
            <Spinner style={{ width: "16px", height: "16px" }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{cur.status || "Думаю…"}</span>
          </div>
        )}
        {cur.warning && <div style={{ ...small, color: "#e8b14d" }}>{cur.warning}</div>}
        {text && (
          <Answer key={cur.id} text={text} dim={busy}
            actions={cur.done ? (shown, full) => <AnswerActions title={title} text={text} shown={shown} full={full} tts={tts} /> : undefined} />
        )}
        {cur.done && cur.error && <div style={{ fontSize: "12px", color: "#ff8080", marginTop: "4px", whiteSpace: "pre-wrap" }}>{cur.error}</div>}
      </div>
    </PanelSectionRow>
  );
}

/** Push-to-talk inside the panel: first press records, second press transcribes into the question field. */
function MicButton({ onText, disabled }: { onText: (t: string) => void; disabled: boolean }) {
  const [phase, setPhase] = useState<"idle" | "rec" | "busy">("idle");
  const [error, setError] = useState("");
  const press = async () => {
    setError("");
    if (phase === "idle") {
      const r = await api.voiceRecordStart();
      if (r.ok) setPhase("rec");
      else setError(r.error || "Не удалось включить микрофон");
    } else if (phase === "rec") {
      setPhase("busy");
      const r = await api.voiceRecordStop();
      setPhase("idle");
      if (r.ok && r.text) onText(r.text);
      else setError(r.error || "Не расслышал, попробуйте ещё раз");
    }
  };
  return (
    <>
      <DialogButton style={{ ...smallButton, flex: "0 0 auto", minWidth: "96px" }} disabled={disabled || phase === "busy"} onClick={press}>
        {phase === "idle" ? "🎤 Сказать" : phase === "rec" ? "⏹ Готово" : "Распознаю…"}
      </DialogButton>
      {error && <div style={{ fontSize: "12px", color: "#ff8080" }}>{error}</div>}
    </>
  );
}

export function Panel() {
  const [state, setState] = useState<State | null>(null);
  const [cli, setCli] = useState<CliInfo | null>(null);
  const [question, setQuestion] = useState("");
  const [withShot, setWithShot] = useState(true);
  const [notes, setNotesText] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const cur = useCurrent();
  const limits = useLimits();
  const game = runningGame();
  const busy = !!cur && !cur.done;

  const refresh = async () => {
    const s = await api.getState(game.appid);
    setState(s);
    setCachedSettings(s.settings);
    setNotesText(s.game.notes);
    setLimits(s.limits);
    if (s.current && (!cur || cur.id === s.current.id)) setCurrent(s.current);
    if (s.settings.update_check) {
      api.checkUpdate(false)
        .then((u) => u && setState((st) => (st ? { ...st, update: u } : st)))
        .catch(() => {});
    }
  };
  const recheck = async () => setCli(await api.checkCli());

  const visible = useQuickAccessVisible();
  useEffect(() => {
    setPanelOpen(visible);
    if (visible) refresh();
  }, [visible]);

  useEffect(() => {
    recheck();
    return () => setPanelOpen(false);
  }, []);

  useEffect(() => {
    if (cur?.done) refresh();
  }, [cur?.done]);

  const ready = cli?.found && cli.loggedIn;
  const s = state?.settings;
  const tts = !!s?.tts_enabled && !!state?.voice.tts_voice && !!state?.voice.player;
  const mic = !!state?.voice.stt_binary && !!state?.voice.stt_model && !!state?.voice.recorder;
  const update = async (patch: Partial<api.Settings>) => {
    const next = await api.setSettings(patch);
    setState((st) => (st ? { ...st, settings: next } : st));
    setCachedSettings(next);
    if ("auth_mode" in patch || "api_key" in patch) recheck();
  };
  const ask = (q: string, shot: boolean) => startAsk(q, shot, "quick", "panel");
  const upd = state?.update;

  return (
    <>
      {upd?.newer && (
        <PanelSection title="Обновление">
          <PanelSectionRow>
            <ButtonItem layout="below" description={`Установлена ${upd.current}. Decky спросит подтверждение.`}
              onClick={() => api.installUpdate(upd.url, upd.latest)}>
              ⬆️ Обновить до {upd.latest}
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
      {cli && !ready && <Setup cli={cli} onRetry={recheck} />}
      <PanelSection title={game.name || "Игра не запущена"}>
        {s?.auth_mode === "subscription" && <LimitsLine limits={limits} />}
        <CurrentAnswer tts={tts} />
        {busy && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => api.cancel()}>Остановить</ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <Focusable style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" }}>
            {QUICK.map((b) => (
              <ButtonItem key={b.label} layout="below" disabled={busy || !ready || (b.shot && !game.appid)}
                onClick={() => ask(b.q, b.shot)}>
                <span style={{ fontSize: "12px" }}>{b.label}</span>
              </ButtonItem>
            ))}
          </Focusable>
        </PanelSectionRow>
        <PanelSectionRow>
          <LazyTextField label="Свой вопрос" value={question} onCommit={setQuestion} disabled={busy} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField label="Приложить скриншот" checked={withShot} disabled={!game.appid} onChange={setWithShot} />
        </PanelSectionRow>
        <PanelSectionRow>
          <Focusable style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {mic && <MicButton disabled={busy} onText={(t) => setQuestion((q) => (q.trim() ? `${q.trim()} ${t}` : t))} />}
            <DialogButton style={{ ...smallButton, fontSize: "14px" }} disabled={busy || !ready || !question.trim()}
              onClick={() => { ask(question.trim(), withShot && !!game.appid); setQuestion(""); }}>
              Спросить
            </DialogButton>
          </Focusable>
        </PanelSectionRow>
      </PanelSection>

      {game.appid > 0 && (
        <PanelSection title="Глубокий разбор">
          <PanelSectionRow>
            <div style={small}>
              Claude скопирует сохранения, изучит файлы игры и интернет и напишет гайд по твоему прохождению.
              Занимает 3–10 минут, можно играть дальше. Свой вопрос выше тоже учтётся.
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy || !ready}
              onClick={() => { startAsk(question.trim(), false, "deep", "panel"); setQuestion(""); }}>
              🔬 {state?.game.guide ? "Обновить гайд" : "Разобрать сохранения"}
            </ButtonItem>
          </PanelSectionRow>
          {state?.game.guide && (
            <>
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={() => openReader(`Гайд: ${game.name}`, state.game.guide)}>
                  📖 Открыть гайд
                </ButtonItem>
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={async () => { await api.deleteGuide(game.appid); refresh(); }}>
                  Удалить гайд
                </ButtonItem>
              </PanelSectionRow>
            </>
          )}
          <PanelSectionRow>
            <LazyTextField label="Где я сейчас (заметка для Claude)" value={notes} delay={1500}
              onCommit={(v) => { setNotesText(v); api.setNotes(game.appid, v); }} />
          </PanelSectionRow>
        </PanelSection>
      )}

      {state && (
        <PanelSection title="История">
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => setShowHistory(!showHistory)}>
              {showHistory ? "Скрыть" : `Прошлые ответы (${state.game.history_total})`}
            </ButtonItem>
          </PanelSectionRow>
          {showHistory && state.game.history.slice().reverse().map((h) => (
            <PanelSectionRow key={h.ts + h.q}>
              <div style={box}>
                <div style={{ ...small, marginBottom: "4px" }}>{h.mode === "deep" ? "🔬 " : h.shot ? "📷 " : ""}{h.q || "Глубокий разбор"}</div>
                <Answer text={h.a} dim={!h.ok}
                  actions={(shown, full) => <AnswerActions title={h.q || "Глубокий разбор"} text={h.a} shown={shown} full={full} tts={tts} />} />
              </div>
            </PanelSectionRow>
          ))}
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy}
              onClick={async () => { await api.newChat(game.appid); setCurrent(null); refresh(); }}>
              Новый разговор
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}

      {s && state && (
        <PanelSection title="Настройки">
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => setShowSettings(!showSettings)}>
              {showSettings ? "Скрыть настройки" : "Показать настройки"}
            </ButtonItem>
          </PanelSectionRow>
          {showSettings && (
            <SettingsRows state={state} cli={cli} busy={busy} update={update} refresh={refresh} />
          )}
        </PanelSection>
      )}
    </>
  );
}
