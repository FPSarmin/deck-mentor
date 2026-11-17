import { addEventListener, callable } from "@decky/api";

export const EVENT = "deck_mentor_event";
export const HOTKEY_EVENT = "deck_mentor_hotkey";

export type Mode = "quick" | "deep";
export type Origin = "panel" | "hotkey";

export interface Settings {
  auth_mode: "subscription" | "api_key";
  api_key: string;
  claude_path: string;
  model: "opus" | "sonnet" | "haiku";
  effort: "low" | "medium" | "high";
  spoiler: "hint" | "balanced" | "full";
  web_search: boolean;
  hotkey_enabled: boolean;
  hotkey_combo: string;
  hotkey_question: string;
  keep_days: number;
  max_storage_mb: number;
  deep_max_turns: number;
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
  game: { notes: string; history: HistoryEntry[]; history_total: number; guide: string; turns: number };
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
  | { id: string; type: "status"; text: string }
  | { id: string; type: "text"; delta: string }
  | { id: string; type: "warning"; text: string }
  | { id: string; type: "captured"; ok: boolean }
  | { id: string; type: "done"; ok: boolean; answer: string; error: string; origin: Origin; mode: Mode; game: string };

export const getState = callable<[appid: number], State>("get_state");
export const checkCli = callable<[], CliInfo>("check_cli");
export const setSettings = callable<[patch: Partial<Settings>], Settings>("set_settings");
export const setNotes = callable<[appid: number, notes: string], boolean>("set_notes");
export const deleteGuide = callable<[appid: number], boolean>("delete_guide");
export const newChat = callable<[appid: number], boolean>("new_chat");
export const cleanupNow = callable<[everything: boolean], { freed_bytes: number; storage_bytes: number }>("cleanup_now");
export const testCapture = callable<[], { ok: boolean; error?: string; width?: number; height?: number; bytes?: number; data_url?: string }>("test_capture");
export const cancel = callable<[], boolean>("cancel");
export const ask = callable<
  [appid: number, game: string, question: string, withShot: boolean, mode: Mode, origin: Origin],
  { ok: boolean; request_id?: string; error?: string }
>("ask");

export function onEvent(fn: (event: BackendEvent) => void) {
  return addEventListener<[BackendEvent]>(EVENT, fn);
}
