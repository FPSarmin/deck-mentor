import { addEventListener, callable } from "@decky/api";

export const EVENT = "deck_mentor_event";
export const HOTKEY_EVENT = "deck_mentor_hotkey";

export type Mode = "quick" | "deep" | "recap" | "achievements" | "progress";
export type Origin = "panel" | "hotkey" | "voice" | "phone";
export const SPOILER_MARK = "<<<SPOILER>>>";

export interface Settings {
  auth_mode: "subscription" | "api_key";
  api_key: string;
  claude_path: string;
  model: "opus" | "sonnet" | "haiku";
  quick_model: "same" | "opus" | "sonnet" | "haiku";
  effort: "low" | "medium" | "high";
  spoiler: "hint" | "balanced" | "full";
  web_search: boolean;
  hotkey_enabled: boolean;
  hotkey_combo: string;
  hotkey_question: string;
  keep_days: number;
  max_storage_mb: number;
  deep_max_turns: number;
  voice_enabled: boolean;
  voice_combo: string;
  voice_model: string;
  voice_language: string;
  tts_enabled: boolean;
  tts_voice: string;
  tts_auto: boolean;
  update_check: boolean;
}

export interface Facts {
  playtime_minutes?: number;
  achievements_total?: number;
  achievements_unlocked?: number;
  recent_achievements?: { name: string; description: string }[];
  missing_achievements?: { name: string; description: string; hidden: boolean; global_percent?: number }[];
  source?: "steam" | "non-steam";
}

export interface LimitWindow {
  utilization?: number;
  resets_at?: number;
  status?: string;
}

export interface Limits {
  status?: string;
  type?: string;
  resets_at?: number;
  windows?: Record<string, LimitWindow>;
  updated?: number;
}

export interface VoiceStatus {
  stt_binary: boolean;
  stt_model: boolean;
  stt_model_mb: number;
  recorder: boolean;
  tts_engine: boolean;
  tts_voice: boolean;
  player: boolean;
  recording: boolean;
  downloading: boolean;
}

export interface UpdateInfo {
  current: string;
  latest: string;
  url: string;
  newer: boolean;
  notes: string;
}

export interface Request {
  id: string;
  appid: number;
  game: string;
  question: string;
  mode: Mode;
  origin: Origin;
  shot: boolean;
  text: string;
  tools: string[];
  status: string;
  done: boolean;
  ok: boolean;
  answer: string;
  error: string;
  warning?: string;
  /** Follow-up questions Claude suggested, shown as buttons under the answer. */
  followups?: string[];
}

export interface HistoryEntry {
  q: string;
  a: string;
  shot: boolean;
  mode: Mode;
  ok: boolean;
  ts: number;
}

export interface State {
  settings: Settings;
  combos: string[];
  busy: boolean;
  current: Request | null;
  hotkey_status: string;
  storage_bytes: number;
  voice_bytes: number;
  voice: VoiceStatus;
  voice_options: { models: Record<string, number>; voices: string[] };
  limits: Limits | null;
  version: string;
  update: UpdateInfo | null;
  phone: PhoneStatus;
  game: {
    spoiler: "" | "hint" | "balanced" | "full";
    wiki: string;
    notes: string;
    history: HistoryEntry[];
    history_total: number;
    guide: string;
    turns: number;
  };
}

export interface PhoneStatus {
  running: boolean;
  url: string;
  expires: number;
}

export interface CliInfo {
  found: boolean;
  path?: string;
  version?: string;
  loggedIn?: boolean;
  authMethod?: string;
  error?: string;
}

export type BackendEvent =
  | { id: string; type: "started"; request: Request }
  | { id: string; type: "status"; text: string }
  | { id: string; type: "text"; delta: string }
  | { id: string; type: "warning"; text: string }
  | { id: string; type: "captured"; ok: boolean }
  | {
      id: string; type: "done"; ok: boolean; answer: string; error: string; followups?: string[];
      origin: Origin; mode: Mode; game: string;
    }
  | { id: string; type: "limits"; limits: Limits }
  | { id: string; type: "download"; kind: "stt" | "tts"; progress: number };

export type HotkeyEvent =
  | { kind: "hint" }
  | { kind: "voice_listening" }
  | { kind: "voice_transcribing" }
  | { kind: "voice"; text: string }
  | { kind: "voice_error"; text: string };

export const getState = callable<[appid: number], State>("get_state");
export const checkCli = callable<[], CliInfo>("check_cli");
export const setSettings = callable<[patch: Partial<Settings>], Settings>("set_settings");
export const setGame = callable<[appid: number, patch: { spoiler?: string; wiki?: string }], { spoiler: string; wiki: string }>("set_game");
export const phoneStart = callable<[appid: number, game: string], PhoneStatus & { ok: boolean; error?: string }>("phone_start");
export const phoneStop = callable<[], boolean>("phone_stop");
export const setNotes = callable<[appid: number, notes: string], boolean>("set_notes");
export const deleteGuide = callable<[appid: number], boolean>("delete_guide");
export const newChat = callable<[appid: number], boolean>("new_chat");
export const cleanupNow = callable<[everything: boolean], { freed_bytes: number; storage_bytes: number }>("cleanup_now");
export const testCapture = callable<[], { ok: boolean; error?: string; width?: number; height?: number; bytes?: number; data_url?: string }>("test_capture");
export const cancel = callable<[], boolean>("cancel");
export const ask = callable<
  [appid: number, game: string, question: string, withShot: boolean, mode: Mode, origin: Origin, facts: Facts | null],
  { ok: boolean; request_id?: string; error?: string }
>("ask");

export function onEvent(fn: (event: BackendEvent) => void) {
  return addEventListener<[BackendEvent]>(EVENT, fn);
}

type Result = { ok: boolean; error?: string };
export const voiceSetup = callable<[kind: "stt" | "tts"], Result>("voice_setup");
export const voiceRemove = callable<[], { freed_bytes: number }>("voice_remove");
export const voiceRecordStart = callable<[], Result>("voice_record_start");
export const voiceRecordStop = callable<[], Result & { text?: string }>("voice_record_stop");
export const speak = callable<[text: string], Result>("speak");
export const stopSpeaking = callable<[], boolean>("stop_speaking");
export const checkUpdate = callable<[force: boolean], UpdateInfo | null>("check_update");

/** Ask Decky Loader itself to install a plugin zip (it shows its own confirmation). */
export function installUpdate(url: string, version: string) {
  const backend = (window as any).DeckyBackend;
  return backend.call("utilities/install_plugin", url, "Deck Mentor", version, false, 2 /* UPDATE */);
}

/** While an answer streams in, hide the follow-up line Claude writes at its end. */
export function stripNext(text: string): string {
  const i = text.indexOf("<<<NEXT>>>");
  return i < 0 ? text : text.slice(0, i).trimEnd();
}

export function splitSpoiler(text: string): [string, string] {
  const i = text.indexOf(SPOILER_MARK);
  return i < 0 ? [text, ""] : [text.slice(0, i).trim(), text.slice(i + SPOILER_MARK.length).trim()];
}
