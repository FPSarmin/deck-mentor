import { Navigation, Router } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";
import * as api from "./backend";
import type { BackendEvent, Request, Settings } from "./backend";

/** Shared state that outlives the Quick Access panel (it unmounts when closed). */
let current: Request | null = null;
let settings: Settings | null = null;
let panelOpen = false;
let reopenAfterCapture = false;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((fn) => fn());
}

export function useCurrent(): Request | null {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, []);
  return current;
}

export function setCurrent(req: Request | null) {
  current = req;
  notify();
}

export function setCachedSettings(s: Settings) {
  settings = s;
}

export function setPanelOpen(open: boolean) {
  panelOpen = open;
}

export function runningGame(): { appid: number; name: string } {
  const app = Router.MainRunningApp;
  return app ? { appid: Number(app.appid) || 0, name: app.display_name || "" } : { appid: 0, name: "" };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function shorten(text: string, max = 230) {
  const flat = text.replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
  return flat.length > max ? flat.slice(0, max - 1) + "…" : flat;
}

/** Start a request. Screenshots are taken with the menu closed, then the menu reopens. */
export async function startAsk(question: string, withShot: boolean, mode: api.Mode, origin: api.Origin) {
  const game = runningGame();
  if (withShot && origin === "panel") {
    reopenAfterCapture = true;
    Navigation.CloseSideMenus();
    await sleep(550);
  }
  const res = await api.ask(game.appid, game.name, question, withShot, mode, origin);
  if (!res.ok) {
    reopenAfterCapture = false;
    toaster.toast({ title: "Deck Mentor", body: res.error || "Не удалось начать запрос" });
    if (withShot && origin === "panel") Navigation.OpenQuickAccessMenu(999 as any);
    return;
  }
  setCurrent({
    id: res.request_id!, appid: game.appid, game: game.name, question, mode, origin, shot: withShot,
    text: "", tools: [], status: "Запускаю…", done: false, ok: false, answer: "", error: "",
  });
}

export function handleEvent(ev: BackendEvent) {
  if (!current || current.id !== ev.id) {
    // The panel may have been closed when the request started (hotkey): adopt it.
    if (ev.type === "done" && ev.origin === "hotkey") showAnswerToast(ev.answer, ev.error, ev.game);
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
        Navigation.OpenQuickAccessMenu(999 as any); // Decky tab
      }
      break;
    case "done":
      Object.assign(next, { done: true, ok: ev.ok, answer: ev.answer, error: ev.error, status: "" });
      if (reopenAfterCapture) {
        reopenAfterCapture = false; // failed before the screenshot: bring the menu back anyway
        Navigation.OpenQuickAccessMenu(999 as any);
      }
      if (ev.origin === "hotkey" || !panelOpen) showAnswerToast(ev.answer, ev.error, ev.game, ev.mode);
      break;
  }
  setCurrent(next);
}

function showAnswerToast(answer: string, error: string, game: string, mode?: api.Mode) {
  const title = mode === "deep" ? "Гайд готов" : game ? `Подсказка: ${game}` : "Подсказка";
  toaster.toast({
    title,
    body: answer ? shorten(answer) : error || "Нет ответа",
    duration: answer ? 15000 : 6000,
    onClick: () => Navigation.OpenQuickAccessMenu(999 as any),
  });
}

export async function onHotkey() {
  if (current && !current.done) {
    toaster.toast({ title: "Deck Mentor", body: "Ещё думаю над прошлым вопросом…", duration: 3000 });
    return;
  }
  const game = runningGame();
  if (!game.appid) {
    toaster.toast({ title: "Deck Mentor", body: "Сначала запустите игру.", duration: 3000 });
    return;
  }
  const question = settings?.hotkey_question || "Что мне сейчас делать? Подскажи по скриншоту.";
  toaster.toast({ title: "Deck Mentor", body: "Смотрю на экран…", duration: 2500 });
  await startAsk(question, true, "quick", "hotkey");
}
