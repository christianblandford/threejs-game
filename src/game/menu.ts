import { getSettings, QUALITY_LEVELS, updateSettings } from "../engine/settings";

/**
 * The game's menus: the title screen, pause, and settings. Plain HTML over
 * the canvas (three.js has no interface toolkit of its own, and the page
 * does this well).
 *
 * The menu only decides what state the game is in and reports what the
 * player asked for; shell.ts acts on it.
 *
 * - `menu`: the title screen is up, the world idling behind it.
 * - `playing`: the player has control.
 * - `paused`: the world is frozen with the pause menu over it.
 */

export type ShellState = "menu" | "playing" | "paused";

export interface MenuOptions {
  title: string;
  /** True when there is a saved game: the title screen then offers "Continue" and "New game" in place of "Play". */
  canContinue(): boolean;
  initialState: ShellState;
}

export interface Menu {
  readonly state: ShellState;
  onStateChanged: ((state: ShellState) => void) | null;
  /** "Play": start from the beginning of the level, keeping what the world remembers. */
  onPlay: (() => void) | null;
  /** "Continue": pick the saved game up where it was. */
  onContinue: (() => void) | null;
  /** "New game": forget the saved game and start from the beginning. */
  onNewGame: (() => void) | null;
  /** "Restart level". */
  onRestart: (() => void) | null;
  /** Puts the game in a state without anybody having clicked: for the shell, once it has done what was asked. */
  set(state: ShellState): void;
  pause(): void;
  resume(): void;
  togglePause(): void;
  /** Takes the menu off the page. */
  dispose(): void;
  /**
   * Works the menu without a mouse (a gamepad's direction pad and buttons):
   * `move` steps the highlight up (-1) or down (1), `adjust` moves a slider
   * or steps a choice left (-1) or right (1), `accept` presses what is
   * highlighted, `back` goes back a screen, or out of the pause menu.
   */
  steer(did: { move: number; adjust: number; accept: boolean; back: boolean }): void;
}

const STYLE = `
#menu { position: fixed; inset: 0; z-index: 6; display: none; align-items: center; justify-content: center; background: rgba(6, 8, 12, 0.62); font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif; }
#menu .card { width: min(380px, calc(100vw - 32px)); box-sizing: border-box; padding: 28px 32px; border-radius: 14px; background: rgba(18, 21, 28, 0.94); border: 1px solid rgba(255, 255, 255, 0.08); color: #f4f6f9; }
#menu h1 { margin: 0 0 16px; font-size: 22px; font-weight: 600; }
#menu button.item { display: block; width: 100%; height: 44px; margin: 8px 0; border: 0; border-radius: 9px; background: rgba(255, 255, 255, 0.07); color: #f4f6f9; font: inherit; font-size: 15px; cursor: pointer; }
#menu button.item:hover { background: rgba(255, 255, 255, 0.14); }
#menu button.item.primary { background: #3b82f6; }
#menu button.item.primary:hover { background: #5b9bff; }
#menu .row { display: flex; align-items: center; justify-content: space-between; height: 46px; font-size: 14px; color: #c5ccd6; }
#menu .row input[type=range] { width: 170px; accent-color: #3b82f6; }
#menu .row input[type=checkbox] { width: 20px; height: 20px; accent-color: #3b82f6; }
#menu .row button { width: 170px; height: 32px; border: 0; border-radius: 7px; background: rgba(255, 255, 255, 0.1); color: #f4f6f9; font: inherit; font-size: 14px; cursor: pointer; text-transform: capitalize; }
#menu .gap { height: 10px; }
#menu :focus-visible, #menu .steered { outline: 2px solid #8fb8ff; outline-offset: 2px; }
`;

export function createMenu(options: MenuOptions): Menu {
  const style = document.head.appendChild(Object.assign(document.createElement("style"), { textContent: STYLE }));
  const root = Object.assign(document.createElement("div"), { id: "menu" });
  const card = Object.assign(document.createElement("div"), { className: "card" });
  root.appendChild(card);
  document.body.appendChild(root);
  let state: ShellState = options.initialState;

  // --- Building blocks ---------------------------------------------------------
  const heading = (text: string): void => void card.appendChild(Object.assign(document.createElement("h1"), { textContent: text }));
  function button(label: string, onClick: () => void, primary = false): void {
    const control = Object.assign(document.createElement("button"), { className: primary ? "item primary" : "item", textContent: label });
    control.addEventListener("click", onClick);
    card.appendChild(control);
  }
  function row(label: string, control: HTMLElement): void {
    const line = Object.assign(document.createElement("label"), { className: "row" });
    line.append(Object.assign(document.createElement("span"), { textContent: label }), control);
    card.appendChild(line);
  }
  function slider(label: string, min: number, max: number, value: number, onChange: (value: number) => void): void {
    const control = Object.assign(document.createElement("input"), { type: "range", min: String(min), max: String(max), step: "0.01", value: String(value) });
    control.addEventListener("input", () => onChange(Number(control.value)));
    row(label, control);
  }
  function checkbox(label: string, value: boolean, onChange: (value: boolean) => void): void {
    const control = Object.assign(document.createElement("input"), { type: "checkbox", checked: value });
    control.addEventListener("change", () => onChange(control.checked));
    row(label, control);
  }
  /** A button that steps through a short list of options. */
  function choice<T extends string>(label: string, values: readonly T[], value: T, onChange: (value: T) => void): void {
    let index = Math.max(0, values.indexOf(value));
    const control = Object.assign(document.createElement("button"), { type: "button", textContent: values[index]! });
    control.addEventListener("click", (event) => {
      event.preventDefault();
      index = (index + 1) % values.length;
      control.textContent = values[index]!;
      onChange(values[index]!);
    });
    row(label, control);
  }
  const gap = (): void => void card.appendChild(Object.assign(document.createElement("div"), { className: "gap" }));

  // --- Screens ----------------------------------------------------------------
  type Screen = "main" | "pause" | "settings";
  function show(screen: Screen | null): void {
    card.replaceChildren();
    root.style.display = screen === null ? "none" : "flex";
    if (screen === null) return;
    /** Where "Back" goes from a sub-screen. */
    const home: Screen = state === "paused" ? "pause" : "main";
    switch (screen) {
      case "main":
        heading(options.title);
        if (options.canContinue()) {
          button("Continue", () => api.onContinue?.(), true);
          button("New game", () => api.onNewGame?.());
        } else {
          button("Play", () => api.onPlay?.(), true);
        }
        button("Settings", () => show("settings"));
        break;
      case "pause":
        heading("Paused");
        button("Resume", () => set("playing"), true);
        button("Settings", () => show("settings"));
        button("Restart level", () => api.onRestart?.());
        button("Main menu", () => set("menu"));
        break;
      case "settings": {
        const settings = getSettings();
        heading("Settings");
        choice("Graphics quality", QUALITY_LEVELS, settings.quality, (quality) => updateSettings({ quality }));
        slider("Volume", 0, 1, settings.masterVolume, (masterVolume) => updateSettings({ masterVolume }));
        slider("Look sensitivity", 0.2, 3, settings.lookSensitivity, (lookSensitivity) => updateSettings({ lookSensitivity }));
        checkbox("Invert vertical look", settings.invertY, (invertY) => updateSettings({ invertY }));
        checkbox("Lower resolution to hold 60 fps", settings.autoResolution, (autoResolution) => updateSettings({ autoResolution }));
        gap();
        button("Back", () => show(home));
        break;
      }
    }
  }
  function set(next: ShellState): void {
    if (next === state) return;
    state = next;
    show(state === "menu" ? "main" : state === "paused" ? "pause" : null);
    api.onStateChanged?.(state);
  }

  const api: Menu = {
    get state() {
      return state;
    },
    onStateChanged: null,
    onPlay: null,
    onContinue: null,
    onNewGame: null,
    onRestart: null,
    set,
    steer(did) {
      if (state === "playing") return;
      const controls = [...card.querySelectorAll<HTMLElement>("button, input")];
      let at = controls.findIndex((control) => control.classList.contains("steered"));
      if (did.move !== 0 && controls.length > 0) {
        controls[at]?.classList.remove("steered");
        at = at < 0 ? (did.move > 0 ? 0 : controls.length - 1) : (at + did.move + controls.length) % controls.length;
        controls[at]!.classList.add("steered");
      }
      const control = controls[at];
      if (did.adjust !== 0 && control instanceof HTMLInputElement && control.type === "range") {
        const span = Number(control.max) - Number(control.min);
        control.value = String(Math.min(Number(control.max), Math.max(Number(control.min), Number(control.value) + did.adjust * span * 0.05)));
        control.dispatchEvent(new Event("input"));
      } else if ((did.accept || did.adjust !== 0) && control && !control.classList.contains("item")) {
        control.click(); // a choice steps on, a checkbox flips
        if (!control.isConnected) return;
      } else if (did.accept) (control ?? controls[0])?.click();
      if (did.back) {
        const back = controls.find((each) => each.textContent === "Back");
        if (back) back.click();
        else if (state === "paused") set("playing");
      }
    },
    pause() {
      if (state === "playing") set("paused");
    },
    resume() {
      if (state === "paused") set("playing");
    },
    togglePause() {
      if (state === "playing") set("paused");
      else if (state === "paused") set("playing");
    },
    dispose() {
      root.remove();
      style.remove();
    },
  };
  show(state === "menu" ? "main" : state === "paused" ? "pause" : null);
  return api;
}
