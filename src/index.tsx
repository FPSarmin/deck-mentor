import { definePlugin, addEventListener, removeEventListener } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { FaRegLightbulb } from "react-icons/fa";
import * as api from "./backend";
import { Panel } from "./Panel";
import { handleEvent, onHotkey, setCachedSettings } from "./store";

export default definePlugin(() => {
  const eventListener = api.onEvent(handleEvent);
  const hotkeyListener = addEventListener(api.HOTKEY_EVENT, () => {
    onHotkey();
  });
  api.getState(0).then((s) => setCachedSettings(s.settings)).catch(() => {});

  return {
    name: "Deck Mentor",
    titleView: <div className={staticClasses.Title}>Deck Mentor</div>,
    content: <Panel />,
    icon: <FaRegLightbulb />,
    onDismount() {
      removeEventListener(api.EVENT, eventListener);
      removeEventListener(api.HOTKEY_EVENT, hotkeyListener);
    },
  };
});
