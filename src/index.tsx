import { definePlugin, addEventListener, removeEventListener, routerHook } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { FaRegLightbulb } from "react-icons/fa";
import * as api from "./backend";
import { Panel } from "./Panel";
import { Reader } from "./Reader";
import { handleEvent, onHotkey, READER_ROUTE, setCachedSettings } from "./store";

export default definePlugin(() => {
  const eventListener = api.onEvent(handleEvent);
  const hotkeyListener = addEventListener<[api.HotkeyEvent]>(api.HOTKEY_EVENT, (ev) => {
    onHotkey(ev);
  });
  routerHook.addRoute(READER_ROUTE, Reader, { exact: true });
  api.getState(0).then((s) => setCachedSettings(s.settings)).catch(() => {});

  return {
    name: "Deck Mentor",
    titleView: <div className={staticClasses.Title}>Deck Mentor</div>,
    content: <Panel />,
    icon: <FaRegLightbulb />,
    onDismount() {
      removeEventListener(api.EVENT, eventListener);
      removeEventListener(api.HOTKEY_EVENT, hotkeyListener);
      routerHook.removeRoute(READER_ROUTE);
    },
  };
});
