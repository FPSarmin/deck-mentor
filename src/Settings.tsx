import { ButtonItem, DropdownItem, PanelSectionRow, ProgressBarWithInfo, ToggleField } from "@decky/ui";
import { LazyTextField } from "./Fields";
import { useState } from "react";
import * as api from "./backend";
import type { CliInfo, Settings, State } from "./backend";
import { clearDownload, useDownload } from "./store";

const small = { fontSize: "12px", opacity: 0.7 };
export const mb = (n: number) => `${(n / 1e6).toFixed(n < 1e7 ? 1 : 0)} МБ`;

const VOICE_LABELS: Record<string, string> = { ru_irina: "Ирина (рус.)", ru_dmitri: "Дмитрий (рус.)", en_amy: "Amy (англ.)" };

interface Props {
  state: State;
  cli: CliInfo | null;
  busy: boolean;
  update: (patch: Partial<Settings>) => Promise<void>;
  refresh: () => Promise<void>;
}

export function SettingsRows({ state, cli, busy, update, refresh }: Props) {
  const s = state.settings;
  const dl = useDownload();
  const [preview, setPreview] = useState("");
  const [voiceError, setVoiceError] = useState("");
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);

  const enableWithDownload = async (kind: "stt" | "tts", key: "voice_enabled" | "tts_enabled", on: boolean) => {
    setVoiceError("");
    if (!on) return update({ [key]: false } as Partial<Settings>);
    const ready = kind === "stt" ? state.voice.stt_model : state.voice.tts_engine && state.voice.tts_voice;
    if (!ready) {
      const r = await api.voiceSetup(kind).catch((e) => ({ ok: false, error: String(e) }));
      clearDownload();
      if (!r.ok) {
        setVoiceError(r.error || "Не удалось скачать");
        return;
      }
    }
    await update({ [key]: true } as Partial<Settings>);
    await refresh();
  };

  return (
    <>
      <PanelSectionRow>
        <DropdownItem label="Спойлеры" selectedOption={s.spoiler} onChange={(o) => update({ spoiler: o.data })}
          rgOptions={[
            { data: "hint", label: "Намёк, решение под шторкой" },
            { data: "balanced", label: "Помощь, спойлеры под шторкой" },
            { data: "full", label: "Полное решение сразу" },
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
        <DropdownItem label="Модель для горячих клавиш" selectedOption={s.quick_model} onChange={(o) => update({ quick_model: o.data })}
          rgOptions={[
            { data: "same", label: "Та же, что выше" },
            { data: "sonnet", label: "Sonnet (быстрее)" },
            { data: "haiku", label: "Haiku (за секунды)" },
            { data: "opus", label: "Opus" },
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
        <ToggleField label="Горячая клавиша: подсказка по экрану"
          description={`Коротко нажмите ${s.hotkey_combo}, и Claude ответит по скриншоту уведомлением. Статус: ${state.hotkey_status}`}
          checked={s.hotkey_enabled} onChange={(v) => update({ hotkey_enabled: v })} />
      </PanelSectionRow>
      {(s.hotkey_enabled || s.voice_enabled) && (
        <PanelSectionRow>
          <DropdownItem label="Кнопки" selectedOption={s.hotkey_combo} onChange={(o) => update({ hotkey_combo: o.data })}
            rgOptions={state.combos.map((c) => ({ data: c, label: c }))} />
        </PanelSectionRow>
      )}
      {s.hotkey_enabled && (
        <PanelSectionRow>
          <LazyTextField label="Вопрос для горячей клавиши" value={s.hotkey_question} delay={1500}
            onCommit={(v) => update({ hotkey_question: v })} />
        </PanelSectionRow>
      )}

      <PanelSectionRow>
        <ToggleField label="🎤 Голосовой вопрос"
          description={`Зажмите ${s.hotkey_combo} и говорите, отпустите, когда закончите. Короткое нажатие по-прежнему даёт подсказку по экрану. `
            + "Распознаётся на Деке, только пока кнопки зажаты. "
            + (state.voice.stt_model ? "" : `Скачает модель ~${state.voice_options.models[s.voice_model] || 60} МБ.`)}
          checked={s.voice_enabled} disabled={!!dl || !state.voice.stt_binary}
          onChange={(v) => enableWithDownload("stt", "voice_enabled", v)} />
      </PanelSectionRow>
      {!state.voice.stt_binary && <PanelSectionRow><div style={small}>В этой сборке нет распознавателя речи.</div></PanelSectionRow>}
      {s.voice_enabled && (
        <>
          <PanelSectionRow>
            <DropdownItem label="Точность распознавания" selectedOption={s.voice_model}
              onChange={async (o) => { await update({ voice_model: o.data }); await enableWithDownload("stt", "voice_enabled", true); }}
              rgOptions={Object.entries(state.voice_options.models).map(([k, size]) => ({
                data: k, label: k === "base" ? `Быстрая (${size} МБ)` : `Точнее (${size} МБ)`,
              }))} />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem label="Язык речи" selectedOption={s.voice_language} onChange={(o) => update({ voice_language: o.data })}
              rgOptions={[{ data: "ru", label: "Русский" }, { data: "en", label: "English" }, { data: "auto", label: "Определять" }]} />
          </PanelSectionRow>
        </>
      )}

      <PanelSectionRow>
        <ToggleField label="🔊 Озвучка ответов" description="Синтез речи на Деке. Скачает движок и голос, около 85 МБ."
          checked={s.tts_enabled} disabled={!!dl} onChange={(v) => enableWithDownload("tts", "tts_enabled", v)} />
      </PanelSectionRow>
      {s.tts_enabled && (
        <>
          <PanelSectionRow>
            <DropdownItem label="Голос" selectedOption={s.tts_voice}
              onChange={async (o) => { await update({ tts_voice: o.data }); await enableWithDownload("tts", "tts_enabled", true); }}
              rgOptions={state.voice_options.voices.map((v) => ({ data: v, label: VOICE_LABELS[v] || v }))} />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="Читать вслух ответы на кнопки" checked={s.tts_auto} onChange={(v) => update({ tts_auto: v })} />
          </PanelSectionRow>
        </>
      )}
      {dl && (
        <PanelSectionRow>
          <ProgressBarWithInfo label={dl.kind === "stt" ? "Скачиваю модель речи" : "Скачиваю озвучку"}
            nProgress={Math.round(dl.progress * 100)} sOperationText={`${Math.round(dl.progress * 100)}%`} />
        </PanelSectionRow>
      )}
      {voiceError && <PanelSectionRow><div style={{ fontSize: "12px", color: "#ff8080" }}>{voiceError}</div></PanelSectionRow>}
      {state.voice_bytes > 1e6 && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!!dl} description={`Модели речи занимают ${mb(state.voice_bytes)}`}
            onClick={async () => { await api.voiceRemove(); await refresh(); }}>
            Удалить голосовые модели
          </ButtonItem>
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
          <LazyTextField label="API-ключ Anthropic" bIsPassword value={s.api_key} delay={0}
            onCommit={(v) => update({ api_key: v })} />
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
        <ButtonItem layout="below" disabled={busy}
          description={`Плагин занимает ${mb(state.storage_bytes)} вместе с перепиской. Удалит копии сейвов, черновики и резервные копии; переписка, гайды и заметки останутся.`}
          onClick={async () => { await api.cleanupNow(false); await refresh(); }}>
          Очистить временные файлы
        </ButtonItem>
      </PanelSectionRow>

      <PanelSectionRow>
        {state.update?.newer ? (
          <ButtonItem layout="below" description={`Установлена ${state.version || "?"}. Decky спросит подтверждение.`}
            onClick={() => api.installUpdate(state.update!.url, state.update!.latest)}>
            ⬆️ Обновить до {state.update.latest}
          </ButtonItem>
        ) : (
          <ButtonItem layout="below" disabled={checking}
            description={checked && state.update ? `Версия ${state.version || "?"}, это последняя` : `Версия ${state.version || "?"}`}
            onClick={async () => {
              setChecking(true);
              await api.checkUpdate(true).catch(() => null);
              await refresh();
              setChecking(false);
              setChecked(true);
            }}>
            {checking ? "Проверяю…" : "Проверить обновления"}
          </ButtonItem>
        )}
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Сообщать о новых версиях" description="Раз в 12 часов смотрит релизы на GitHub"
          checked={s.update_check} onChange={(v) => update({ update_check: v })} />
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
  );
}
