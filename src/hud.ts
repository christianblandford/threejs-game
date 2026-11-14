/**
 * What is written over the picture: the prompt for something that can be
 * used, a line of news, and a conversation. Plain HTML over the canvas.
 */

export interface Hud {
  /** "[E] Enter the hall", low on the screen. Null hides it. */
  prompt(text: string | null): void;
  /** A line near the top for a few seconds. */
  toast(text: string): void;
  /** What someone is saying, with the answers on offer. Null puts it away. */
  say(speech: { speaker: string | null; text: string; choices: string[]; hint: string; selected?: number } | null): void;
}

const STYLE = `
.hud { position: fixed; left: 0; right: 0; display: flex; justify-content: center; pointer-events: none; z-index: 4; font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif; color: #fff; }
.hud > div { background: rgba(10, 12, 16, 0.72); border-radius: 12px; }
#hud-prompt { bottom: 110px; }
#hud-prompt > div { padding: 8px 16px; font-size: 15px; }
#hud-toast { top: 84px; transition: opacity 0.4s; }
#hud-toast > div { padding: 8px 18px; font-size: 20px; font-weight: 600; background: none; text-shadow: 0 1px 8px rgba(0, 0, 0, 0.8); }
#hud-speech { bottom: 36px; }
#hud-speech > div { width: min(760px, calc(100vw - 32px)); padding: 16px 22px; box-sizing: border-box; }
#hud-speech .speaker { color: #8fb8ff; font-weight: 700; font-size: 14px; margin-bottom: 6px; }
#hud-speech .line { font-size: 18px; line-height: 1.4; }
#hud-speech .choice { font-size: 16px; color: #e8ecf1; margin-top: 6px; }
#hud-speech .choice.selected { color: #8fb8ff; font-weight: 600; }
#hud-speech .hint { font-size: 12px; color: rgba(232, 236, 241, 0.6); margin-top: 10px; }
`;

export function createHud(): Hud {
  document.head.appendChild(Object.assign(document.createElement("style"), { textContent: STYLE }));
  const layer = (id: string): HTMLElement => {
    const outer = Object.assign(document.createElement("div"), { id, className: "hud" });
    outer.style.display = "none";
    outer.appendChild(document.createElement("div"));
    document.body.appendChild(outer);
    return outer;
  };
  const promptBox = layer("hud-prompt");
  const toastBox = layer("hud-toast");
  const speechBox = layer("hud-speech");
  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  const add = (parent: Element, className: string, text: string): void => {
    parent.appendChild(Object.assign(document.createElement("div"), { className, textContent: text }));
  };

  return {
    prompt(text) {
      promptBox.style.display = text === null ? "none" : "flex";
      if (text !== null) promptBox.firstElementChild!.textContent = `[ E ]   ${text}`;
    },
    toast(text) {
      toastBox.firstElementChild!.textContent = text;
      toastBox.style.display = "flex";
      toastBox.style.opacity = "1";
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => (toastBox.style.opacity = "0"), 2600);
    },
    say(speech) {
      speechBox.style.display = speech === null ? "none" : "flex";
      if (!speech) return;
      const box = speechBox.firstElementChild!;
      box.replaceChildren();
      if (speech.speaker) add(box, "speaker", speech.speaker);
      add(box, "line", speech.text);
      speech.choices.forEach((choice, i) => add(box, i === speech.selected ? "choice selected" : "choice", `${i + 1}.  ${choice}`));
      add(box, "hint", speech.hint);
    },
  };
}
