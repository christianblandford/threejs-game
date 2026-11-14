/**
 * Action-based input: game code asks "is `jump` down?" rather than "is Space
 * down?", so keys can be rebound and keyboard and gamepad feed the same
 * actions. Ported from the Babylon project's src/engine/input.ts. (The mouse
 * turns the view through three's PointerLockControls, in main.ts; the gamepad
 * is the browser's own Gamepad API, which three.js does not wrap.)
 *
 * Frame protocol (main.ts):
 *   input.update()   at the start of the frame  (polls the gamepad)
 *   ...game logic reads input...
 *   input.endFrame() after drawing              (clears per-frame state)
 */

export type Action = "forward" | "back" | "left" | "right" | "jump" | "sprint" | "interact" | "toggleView" | "pause";

/** Keyboard bindings, as `KeyboardEvent.code` values (layout-independent). */
export const DEFAULT_KEY_BINDINGS: Record<Action, string[]> = {
  forward: ["KeyW", "ArrowUp"],
  back: ["KeyS", "ArrowDown"],
  left: ["KeyA", "ArrowLeft"],
  right: ["KeyD", "ArrowRight"],
  jump: ["Space"],
  sprint: ["ShiftLeft", "ShiftRight"],
  interact: ["KeyE"],
  toggleView: ["KeyV"],
  pause: ["Escape", "KeyP"],
};

/** Gamepad bindings, as button indices of the W3C "standard" mapping. */
export const DEFAULT_PAD_BINDINGS: Partial<Record<Action, number[]>> = {
  jump: [0], // A / Cross
  interact: [2], // X / Square
  sprint: [10, 6], // left stick click, left trigger
  toggleView: [11], // right stick click
  pause: [9], // Start / Options
};
/** Buttons of the standard mapping that menus and conversations use by number. */
export const PAD = { accept: 0, back: 1, up: 12, down: 13, left: 14, right: 15 } as const;

const STICK_DEADZONE = 0.15;

export interface Vec2 {
  x: number;
  y: number;
}

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || /^(TEXTAREA|SELECT)$/.test(target.tagName) || (target instanceof HTMLInputElement && target.type === "text"));
}
function deadzone(v: number): number {
  const a = Math.abs(v);
  return a < STICK_DEADZONE ? 0 : (Math.sign(v) * (a - STICK_DEADZONE)) / (1 - STICK_DEADZONE);
}

export class Input {
  keyBindings: Record<Action, string[]> = structuredClone(DEFAULT_KEY_BINDINGS);
  padBindings: Partial<Record<Action, number[]>> = structuredClone(DEFAULT_PAD_BINDINGS);
  /**
   * Movement intent this frame. `x` is right (+) / left (-), `y` is forward (+)
   * / back (-). Keyboard and left stick combined; length never exceeds 1.
   */
  readonly move: Vec2 = { x: 0, y: 0 };
  /** Right gamepad stick, -1..1 per axis (`x` right, `y` down). A rate, not a distance. */
  readonly lookStick: Vec2 = { x: 0, y: 0 };
  /** True while a gamepad with the standard layout is connected. */
  padConnected = false;

  private readonly keysDown = new Set<string>();
  private readonly keysPressed = new Set<string>();
  private padDown = new Set<number>();
  private readonly padPressed = new Set<number>();

  constructor() {
    window.addEventListener("keydown", this.onKeyDown);
    window.addEventListener("keyup", (e) => this.keysDown.delete(e.code));
    window.addEventListener("blur", this.releaseAll);
    document.addEventListener("visibilitychange", this.releaseAll);
  }

  /** True while any key/button bound to the action is held. */
  isDown(action: Action): boolean {
    return this.keyBindings[action].some((code) => this.keysDown.has(code)) || (this.padBindings[action]?.some((button) => this.padDown.has(button)) ?? false);
  }
  /** True only on the frame the action went from up to down. */
  wasPressed(action: Action): boolean {
    return this.keyBindings[action].some((code) => this.keysPressed.has(code)) || (this.padBindings[action]?.some((button) => this.padPressed.has(button)) ?? false);
  }
  /** What to call an action's key on screen: "E" for `interact` by default. */
  labelFor(action: Action): string {
    const code = this.keyBindings[action][0] ?? "";
    return code.replace(/^(Key|Digit)/, "").replace(/(Left|Right)$/, "");
  }
  /** Raw key state, for one-off debug keys. Prefer actions in game code. */
  isKeyDown(code: string): boolean {
    return this.keysDown.has(code);
  }
  wasKeyPressed(code: string): boolean {
    return this.keysPressed.has(code);
  }
  /** A gamepad button by its number in the standard mapping (see `PAD`), on the frame it went down. */
  wasPadPressed(button: number): boolean {
    return this.padPressed.has(button);
  }
  /** The number key (1 to 9) that went down this frame, or 0. */
  digitPressed(): number {
    for (let digit = 1; digit <= 9; digit++) if (this.keysPressed.has(`Digit${digit}`) || this.keysPressed.has(`Numpad${digit}`)) return digit;
    return 0;
  }

  /** Call once at the start of every frame. */
  update(): void {
    const pad = navigator.getGamepads?.().find((p) => p?.connected && p.mapping === "standard") ?? null;
    this.padConnected = pad !== null;
    const nowDown = new Set<number>();
    let stickX = 0;
    let stickY = 0;
    this.lookStick.x = 0;
    this.lookStick.y = 0;
    if (pad) {
      pad.buttons.forEach((button, index) => {
        if (!button.pressed) return;
        nowDown.add(index);
        if (!this.padDown.has(index)) this.padPressed.add(index);
      });
      stickX = deadzone(pad.axes[0] ?? 0);
      stickY = -deadzone(pad.axes[1] ?? 0);
      this.lookStick.x = deadzone(pad.axes[2] ?? 0);
      this.lookStick.y = deadzone(pad.axes[3] ?? 0);
    }
    this.padDown = nowDown;

    let x = stickX + (this.isDown("right") ? 1 : 0) - (this.isDown("left") ? 1 : 0);
    let y = stickY + (this.isDown("forward") ? 1 : 0) - (this.isDown("back") ? 1 : 0);
    const length = Math.hypot(x, y);
    if (length > 1) {
      x /= length;
      y /= length;
    }
    this.move.x = x;
    this.move.y = y;
  }

  /** Call once after every frame has been drawn. */
  endFrame(): void {
    this.keysPressed.clear();
    this.padPressed.clear();
  }

  /** Lets go of everything: for when the window loses the keyboard, or the game changes hands (a menu opens). */
  readonly releaseAll = (): void => {
    this.keysDown.clear();
  };

  private readonly onKeyDown = (e: KeyboardEvent): void => {
    if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    const bound = Object.values(this.keyBindings).some((codes) => codes.includes(e.code));
    if (bound) e.preventDefault(); // stop Space / arrows from scrolling the page
    if (e.repeat) return;
    this.keysDown.add(e.code);
    this.keysPressed.add(e.code);
  };
}
