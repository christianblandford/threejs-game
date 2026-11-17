import type { LevelInfo } from "../config";
import { params } from "../engine/params";
import type { WorldState } from "../engine/worldState";
import { createMenu, type Menu } from "./menu";
import type { Saving } from "./saving";

/**
 * How a game starts and starts again: the title screen, "Continue", "New
 * game", "Restart level".
 *
 * The title screen comes first, unless something says how to start: a URL
 * switch (`?continue`, `?menu`), or the menu itself across a reload. "New
 * game" and "Restart level" reload the page: what the world remembers
 * decides how it is built. In development the game starts playing, as the
 * Babylon project does; `?menu` shows the title screen.
 */
const START_KEY = "three-start";

export async function createShell(systems: { level: LevelInfo; state: WorldState; saving: Saving; start?: "play" | "continue" | null }): Promise<{ menu: Menu; dispose(): void }> {
  const { level, state, saving } = systems;
  const asked = sessionStorage.getItem(START_KEY);
  sessionStorage.removeItem(START_KEY);
  // (`start` is given when a game is put together again in a page that is already running: it carries on as it was.)
  const starting = systems.start !== undefined ? systems.start : params.has("continue") ? "continue" : asked ?? (import.meta.env.DEV && !params.has("menu") ? "play" : null);
  if (starting === "continue") await saving.resume();
  const menu = createMenu({ title: level.title, canContinue: saving.exists, initialState: starting ? "playing" : "menu" });

  const reloadTo = (start: "play" | "continue"): void => {
    state.flush();
    sessionStorage.setItem(START_KEY, start);
    // Without the switches that would start it some other way.
    const url = new URL(location.href);
    for (const name of ["newgame", "continue", "menu"]) url.searchParams.delete(name);
    location.replace(url);
  };
  menu.onPlay = () => menu.set("playing");
  menu.onContinue = () => void saving.resume().then(() => menu.set("playing"));
  menu.onNewGame = () => {
    state.reset();
    reloadTo("play");
  };
  menu.onRestart = () => {
    // The level as it was at its start: what was done in it is forgotten (the
    // gate, the fires) along with where the player was. Without that, lowering
    // the gate and restarting leaves the player on the road outside a town
    // whose only lever is inside. What people remember of conversations stays.
    state.forget(`${level.id}/`);
    state.forget("player/");
    reloadTo("play");
  };
  return { menu, dispose: () => menu.dispose() };
}
