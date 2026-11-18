import { Navigation, Router } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";
import * as api from "./backend";
import type { BackendEvent, Facts, HotkeyEvent, Limits, Request, Settings } from "./backend";

/** Shared state that outlives the Quick Access panel (it unmounts when closed). */
let current: Request | null = null;
let settings: Settings | null = null;
let limits: Limits | null = null;
let download: { kind: string; progress: number } | null = null;
let panelOpen = false;
let reopenAfterCapture = false;
const listeners = new Set<() => void>();

let notifyTimer: ReturnType<typeof setTimeout> | null = null;

function notify() {
  if (notifyTimer) {
    clearTimeout(notifyTimer);
    notifyTimer = null;
  }
  listeners.forEach((fn) => fn());
}

/** Streaming text arrives in dozens of small pieces a second; redraw the panel at most ~4 times a second. */
function notifySoon(ms = 250) {
  if (!notifyTimer) notifyTimer = setTimeout(notify, ms);
}

function useStore<T>(get: () => T): T {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, []);
  return get();
}

export const useCurrent = () => useStore(() => current);
export const useLimits = () => useStore(() => limits);
export const useDownload = () => useStore(() => download);

export function clearDownload() {
  download = null;
  notify();
}

export function setCurrent(req: Request | null) {
  current = req;
  notify();
}

export function setLimits(l: Limits | null) {
  limits = l;
  notify();
}

export function setCachedSettings(s: Settings) {
  settings = s;
}

export function setPanelOpen(open: boolean) {
  panelOpen = open;
}

/**
 * What the panel shows that must survive it closing: Steam unmounts the panel whenever the
 * Quick Access menu closes (B on the keyboard, a screenshot, the reader), and a half-typed
 * question used to vanish with it.
 */
export const panelUi = { question: "", withShot: true, showHistory: false, showSettings: false, showGame: false };

const SHORTCUT_APP_TYPE = 1073741824; // k_EAppType_Shortcut: a non-Steam game added to the library

export function runningGame(): { appid: number; name: string; nonSteam: boolean } {
  const app = Router.MainRunningApp as any;
  if (!app) return { appid: 0, name: "", nonSteam: false };
  return { appid: Number(app.appid) || 0, name: app.display_name || "", nonSteam: app.app_type === SHORTCUT_APP_TYPE };
}

/** Steam's achievement summary for the running game, or null (non-Steam, none, not loaded). */
export async function achievementSummary(): Promise<{ total: number; unlocked: number } | null> {
  const game = runningGame();
  if (!game.appid || game.nonSteam) return null;
  try {
    const store = (window as any).appDetailsStore;
    if (store?.RequestAchievements) {
      await Promise.race([store.RequestAchievements(game.appid), new Promise((r) => setTimeout(r, 1500))]);
    }
    const a = store?.GetAchievements?.(game.appid);
    return a && a.nTotal > 0 ? { total: a.nTotal, unlocked: a.nAchieved } : null;
  } catch {
    return null;
  }
}

/** Playtime and unlocked achievements, when the launcher has them. Never blocks for long. */
async function gameFacts(withMissing = false): Promise<Facts | null> {
  const app = Router.MainRunningApp as any;
  if (!app) return null;
  const game = runningGame();
  const facts: Facts = { source: game.nonSteam ? "non-steam" : "steam" };
  if (typeof app.minutes_playtime_forever === "number") facts.playtime_minutes = app.minutes_playtime_forever;
  if (!game.nonSteam) {
    try {
      const store = (window as any).appDetailsStore;
      if (store?.RequestAchievements) {
        await Promise.race([store.RequestAchievements(game.appid), new Promise((r) => setTimeout(r, 1500))]);
      }
      const a = store?.GetAchievements?.(game.appid);
      if (a && a.nTotal > 0) {
        facts.achievements_total = a.nTotal;
        facts.achievements_unlocked = a.nAchieved;
        const all = [...(a.vecHighlight || []), ...(a.vecAchievedHidden || []), ...(a.vecUnachieved || [])];
        const seen = new Set<string>();
        facts.recent_achievements = all
          .filter((x: any) => x && x.bAchieved && !seen.has(x.strID) && seen.add(x.strID))
          .sort((x: any, y: any) => (y.rtUnlocked || 0) - (x.rtUnlocked || 0))
          .slice(0, 15)
          .map((x: any) => ({ name: x.strName || x.strID, description: x.strDescription || "" }));
        if (withMissing) {
          const seenMissing = new Set<string>();
          facts.missing_achievements = all
            .filter((x: any) => x && !x.bAchieved && !seenMissing.has(x.strID) && seenMissing.add(x.strID))
            .slice(0, 80)
            .map((x: any) => ({
              name: x.strName || x.strID,
              description: x.strDescription || "",
              hidden: !!x.bHidden,
            }));
        }
      }
    } catch {
      // achievements are optional
    }
  }
  return facts;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function shorten(text: string, max = 230) {
  const flat = api.splitSpoiler(text)[0].replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
  return flat.length > max ? flat.slice(0, max - 1) + "…" : flat;
}

const DECKY_TAB = 999 as any;

/** Start a request. Screenshots are taken with the menu closed, then the menu reopens. */
export async function startAsk(question: string, withShot: boolean, mode: api.Mode, origin: api.Origin) {
  const game = runningGame();
  const factsPromise = gameFacts(mode === "achievements" || mode === "progress");
  if (withShot && origin === "panel") {
    reopenAfterCapture = true;
    Navigation.CloseSideMenus();
    await sleep(550);
  }
  const facts = await factsPromise;
  const res = await api.ask(game.appid, game.name, question, withShot, mode, origin, facts);
  if (!res.ok) {
    reopenAfterCapture = false;
    toaster.toast({ title: "Deck Mentor", body: res.error || "Не удалось начать запрос" });
    if (withShot && origin === "panel") Navigation.OpenQuickAccessMenu(DECKY_TAB);
    return;
  }
  setCurrent({
    id: res.request_id!, appid: game.appid, game: game.name, question, mode, origin, shot: withShot,
    text: "", tools: [], status: "Запускаю…", done: false, ok: false, answer: "", error: "",
  });
}

export function handleEvent(ev: BackendEvent) {
  if (ev.type === "limits") {
    setLimits(ev.limits);
    return;
  }
  if (ev.type === "download") {
    download = ev.progress >= 1 ? null : { kind: ev.kind, progress: ev.progress };
    notify();
    return;
  }
  if (ev.type === "started") {
    // A question typed on the phone page: show it in the panel too.
    if (ev.request.origin === "phone" && (!current || current.done)) setCurrent(ev.request);
    return;
  }
  if (!current || current.id !== ev.id) {
    if (ev.type === "done" && ev.origin !== "panel") showAnswerToast(ev.answer, ev.error, ev.game);
    return;
  }
  const next: Request = { ...current };
  switch (ev.type) {
    case "status":
      next.status = ev.text;
      if (ev.text && !ev.text.endsWith("…")) next.tools = [...next.tools, ev.text].slice(-6);
      break;
    case "text":
      next.text += ev.delta;
      break;
    case "warning":
      next.warning = ev.text;
      break;
    case "captured":
      if (reopenAfterCapture) {
        reopenAfterCapture = false;
        Navigation.OpenQuickAccessMenu(DECKY_TAB);
      }
      break;
    case "done":
      Object.assign(next, { done: true, ok: ev.ok, answer: ev.answer, error: ev.error, status: "", followups: ev.followups || [] });
      if (reopenAfterCapture) {
        reopenAfterCapture = false; // failed before the screenshot: bring the menu back anyway
        Navigation.OpenQuickAccessMenu(DECKY_TAB);
      }
      if (ev.origin !== "panel" || !panelOpen) showAnswerToast(ev.answer, ev.error, ev.game, ev.mode);
      break;
  }
  current = next;
  if (ev.type === "text" || ev.type === "status") notifySoon();
  else notify();
}

function showAnswerToast(answer: string, error: string, game: string, mode?: api.Mode) {
  const title = mode === "deep" ? "Гайд готов" : mode === "progress" ? "Прогресс проверен"
    : game ? `Подсказка: ${game}` : "Подсказка";
  toaster.toast({
    title,
    body: answer ? shorten(answer) : error || "Нет ответа",
    duration: answer ? 15000 : 6000,
    // One tap opens the whole answer on the full screen; B goes straight back to the game.
    onClick: () => (answer ? openReader(title, answer, false) : Navigation.OpenQuickAccessMenu(DECKY_TAB)),
  });
}

function toast(body: string, duration = 3000) {
  toaster.toast({ title: "Deck Mentor", body, duration });
}

export async function onHotkey(ev: HotkeyEvent) {
  const kind = ev?.kind || "hint";
  if (kind === "voice_listening") return toast("🎤 Слушаю… отпустите кнопки, когда договорите", 3000);
  if (kind === "voice_transcribing") return toast("Распознаю…", 2500);
  if (kind === "voice_error") return toast((ev as any).text || "Не получилось", 5000);

  if (current && !current.done) return toast("Ещё думаю над прошлым вопросом…");
  const game = runningGame();
  if (!game.appid) return toast("Сначала запустите игру.");

  if (kind === "voice") {
    const text = (ev as any).text as string;
    toast(`🎤 «${text}»`, 4000);
    return startAsk(text, true, "quick", "voice");
  }
  const question = settings?.hotkey_question || "Что мне сейчас делать? Подскажи по скриншоту.";
  toast("Смотрю на экран…", 2500);
  return startAsk(question, true, "quick", "hotkey");
}

/** Text shown on the full-screen reader page. */
export let readerContent: { title: string; text: string } = { title: "", text: "" };

export const READER_ROUTE = "/deck-mentor-reader";

let readerFromPanel = false;

export function openReader(title: string, text: string, fromPanel = true) {
  readerContent = { title, text };
  readerFromPanel = fromPanel;
  Navigation.CloseSideMenus();
  Navigation.Navigate(READER_ROUTE);
}

/** The reader page went away (its own button or Steam's B): bring the plugin panel back. */
export function readerClosed() {
  if (!readerFromPanel) return;
  readerFromPanel = false;
  setTimeout(() => Navigation.OpenQuickAccessMenu(DECKY_TAB), 150);
}
