import {
  ButtonItem,
  DropdownItem,
  Focusable,
  PanelSection,
  PanelSectionRow,
  Spinner,
  TextField,
  ToggleField,
} from "@decky/ui";
import { useEffect, useState } from "react";
import { useQuickAccessVisible } from "@decky/api";
import * as api from "./backend";
import type { CliInfo, State } from "./backend";
import { Answer } from "./Answer";
import { runningGame, setCachedSettings, setCurrent, setPanelOpen, startAsk, useCurrent } from "./store";

const QUICK = [
  { label: "📷 Что мне делать?", q: "Что мне сейчас делать? Подскажи по скриншоту.", shot: true },
  { label: "📷 Как пройти это?", q: "Как пройти то, что на скриншоте: бой, головоломку или препятствие?", shot: true },
  { label: "📷 Объясни экран", q: "Объясни, что сейчас на экране: интерфейс, предметы, характеристики.", shot: true },
  { label: "🧭 Что я пропустил?", q: "Что важного я мог пропустить на текущем этапе игры?", shot: false },
];

const box = { padding: "8px 10px", borderRadius: "6px", background: "rgba(255,255,255,0.06)" };
const small = { fontSize: "12px", opacity: 0.7 };
const mb = (n: number) => `${(n / 1e6).toFixed(n < 1e7 ? 1 : 0)} МБ`;

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

function CurrentAnswer() {
  const cur = useCurrent();
  if (!cur) return null;
  const busy = !cur.done;
  const text = cur.done ? cur.answer || cur.text : cur.text;
  return (
    <PanelSectionRow>
      <div style={box}>
        <div style={{ ...small, marginBottom: "4px" }}>
          {cur.mode === "deep" ? "🔬 Глубокий разбор" : `${cur.shot ? "📷 " : ""}${cur.question}`}
        </div>
        {busy && (
          <div style={{ display: "flex", alignItems: "center", gap: "8px", ...small, marginBottom: text ? "6px" : 0 }}>
            <Spinner style={{ width: "16px", height: "16px" }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{cur.status || "Думаю…"}</span>
          </div>
        )}
        {cur.warning && <div style={{ ...small, color: "#e8b14d" }}>{cur.warning}</div>}
        {text && <Answer text={text} dim={busy} />}
        {cur.done && cur.error && <div style={{ fontSize: "12px", color: "#ff8080", marginTop: "4px", whiteSpace: "pre-wrap" }}>{cur.error}</div>}
      </div>
    </PanelSectionRow>
  );
}

export function Panel() {
  const [state, setState] = useState<State | null>(null);
  const [cli, setCli] = useState<CliInfo | null>(null);
  const [question, setQuestion] = useState("");
  const [withShot, setWithShot] = useState(true);
  const [notes, setNotesText] = useState("");
  const [showGuide, setShowGuide] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [preview, setPreview] = useState<string>("");
  const cur = useCurrent();
  const game = runningGame();
  const busy = !!cur && !cur.done;

  const refresh = async () => {
    const s = await api.getState(game.appid);
    setState(s);
    setCachedSettings(s.settings);
    setNotesText(s.game.notes);
    if (s.current && (!cur || cur.id === s.current.id)) setCurrent(s.current);
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
  const update = async (patch: Partial<api.Settings>) => {
    const next = await api.setSettings(patch);
    setState((st) => (st ? { ...st, settings: next } : st));
    setCachedSettings(next);
    if ("auth_mode" in patch || "api_key" in patch) recheck();
  };
  const ask = (q: string, shot: boolean) => startAsk(q, shot, "quick", "panel");

  return (
    <>
      {cli && !ready && <Setup cli={cli} onRetry={recheck} />}
      <PanelSection title={game.name || "Игра не запущена"}>
        <CurrentAnswer />
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
          <TextField label="Свой вопрос" value={question} onChange={(e) => setQuestion(e.target.value)} disabled={busy} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField label="Приложить скриншот" checked={withShot} disabled={!game.appid} onChange={setWithShot} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy || !ready || !question.trim()}
            onClick={() => { ask(question.trim(), withShot && !!game.appid); setQuestion(""); }}>
            Спросить
          </ButtonItem>
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
                <ButtonItem layout="below" onClick={() => setShowGuide(!showGuide)}>
                  📖 {showGuide ? "Скрыть гайд" : "Открыть гайд"}
                </ButtonItem>
              </PanelSectionRow>
              {showGuide && (
                <PanelSectionRow>
                  <Focusable style={box} onActivate={() => {}}>
                    <Answer text={state.game.guide} />
                  </Focusable>
                </PanelSectionRow>
              )}
              {showGuide && (
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={async () => { await api.deleteGuide(game.appid); setShowGuide(false); refresh(); }}>
                    Удалить гайд
                  </ButtonItem>
                </PanelSectionRow>
              )}
            </>
          )}
          <PanelSectionRow>
            <TextField label="Где я сейчас (заметка для Claude)" value={notes} onChange={(e) => setNotesText(e.target.value)}
              onBlur={() => api.setNotes(game.appid, notes)} />
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
              <Focusable style={box} onActivate={() => {}}>
                <div style={{ ...small, marginBottom: "4px" }}>{h.shot ? "📷 " : ""}{h.q}</div>
                <Answer text={h.a} dim={!h.ok} />
              </Focusable>
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
            <>
              <PanelSectionRow>
                <DropdownItem label="Спойлеры" selectedOption={s.spoiler} onChange={(o) => update({ spoiler: o.data })}
                  rgOptions={[
                    { data: "hint", label: "Только намёки" },
                    { data: "balanced", label: "Помощь без лишнего" },
                    { data: "full", label: "Полное решение" },
                  ]} />
              </PanelSectionRow>
              <PanelSectionRow>
                <DropdownItem label="Модель" selectedOption={s.model} onChange={(o) => update({ model: o.data })}
                  rgOptions={[
                    { data: "opus", label: "Opus (умнее всех)" },
                    { data: "sonnet", label: "Sonnet (быстрее)" },
                    { data: "haiku", label: "Haiku (самый быстрый)" },
                  ]} />
              </PanelSectionRow>
              <PanelSectionRow>
                <DropdownItem label="Глубина размышлений" selectedOption={s.effort} onChange={(o) => update({ effort: o.data })}
                  rgOptions={[
                    { data: "low", label: "Быстро" },
                    { data: "medium", label: "Обычно" },
                    { data: "high", label: "Тщательно" },
                  ]} />
              </PanelSectionRow>
              <PanelSectionRow>
                <ToggleField label="Искать в интернете" description="Вики и гайды для точных ответов"
                  checked={s.web_search} onChange={(v) => update({ web_search: v })} />
              </PanelSectionRow>
              <PanelSectionRow>
                <ToggleField label="Горячая клавиша" description={`Зажмите кнопки — Claude посмотрит на экран и ответит уведомлением. Статус: ${state.hotkey_status}`}
                  checked={s.hotkey_enabled} onChange={(v) => update({ hotkey_enabled: v })} />
              </PanelSectionRow>
              {s.hotkey_enabled && (
                <PanelSectionRow>
                  <DropdownItem label="Кнопки" selectedOption={s.hotkey_combo} onChange={(o) => update({ hotkey_combo: o.data })}
                    rgOptions={state.combos.map((c) => ({ data: c, label: c }))} />
                </PanelSectionRow>
              )}
              {s.hotkey_enabled && (
                <PanelSectionRow>
                  <TextField label="Вопрос для горячей клавиши" value={s.hotkey_question}
                    onChange={(e) => setState({ ...state, settings: { ...s, hotkey_question: e.target.value } })}
                    onBlur={() => update({ hotkey_question: s.hotkey_question })} />
                </PanelSectionRow>
              )}
              <PanelSectionRow>
                <DropdownItem label="Вход" selectedOption={s.auth_mode} onChange={(o) => update({ auth_mode: o.data })}
                  rgOptions={[
                    { data: "subscription", label: "Подписка Claude" },
                    { data: "api_key", label: "API-ключ" },
                  ]} />
              </PanelSectionRow>
              {s.auth_mode === "api_key" && (
                <PanelSectionRow>
                  <TextField label="API-ключ Anthropic" bIsPassword value={s.api_key}
                    onChange={(e) => setState({ ...state, settings: { ...s, api_key: e.target.value } })}
                    onBlur={() => update({ api_key: s.api_key })} />
                </PanelSectionRow>
              )}
              <PanelSectionRow>
                <div style={small}>
                  {cli?.found ? `Claude Code ${cli.version || ""} · ${cli.loggedIn ? "вход выполнен" : "нет входа"}` : "Claude Code не найден"}
                </div>
              </PanelSectionRow>
              <PanelSectionRow>
                <DropdownItem label="Хранить переписку" selectedOption={s.keep_days} onChange={(o) => update({ keep_days: o.data })}
                  rgOptions={[{ data: 0, label: "Всегда" }, ...[7, 30, 90].map((d) => ({ data: d, label: `${d} дн.` }))]} />
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={busy} description={`Плагин занимает ${mb(state.storage_bytes)} вместе с перепиской. Удалит копии сейвов, черновики и резервные копии; переписка, гайды и заметки останутся.`}
                  onClick={async () => { await api.cleanupNow(false); refresh(); }}>
                  Очистить временные файлы
                </ButtonItem>
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={async () => {
                  const r = await api.testCapture();
                  setPreview(r.ok ? r.data_url! : `error:${r.error}`);
                }}>
                  Проверить скриншот
                </ButtonItem>
              </PanelSectionRow>
              {preview && (
                <PanelSectionRow>
                  {preview.startsWith("error:")
                    ? <div style={{ fontSize: "12px", color: "#ff8080" }}>{preview.slice(6)}</div>
                    : <img src={preview} style={{ width: "100%", borderRadius: "4px" }} />}
                </PanelSectionRow>
              )}
            </>
          )}
        </PanelSection>
      )}
    </>
  );
}
