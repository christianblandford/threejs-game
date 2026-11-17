import { Story } from "inkjs";
import type { WorldState } from "./worldState";

/**
 * Conversations, written in Ink.
 *
 * Ink (https://www.inklestudios.com/ink) is a small language for branching
 * dialogue: lines, choices, conditions and variables, written as text. Its
 * runtime (`inkjs`) plays a story a line at a time; this file is what stands
 * between that and the game:
 *
 * (Ported unchanged from the Babylon project's src/engine/dialogue.ts.)
 *
 * - **Stories** are `.ink` files, compiled by the build (vite.config.ts) and
 *   added here under a name. `start("steward")` begins the story of that
 *   name at its knot `start`; `start("steward#farewell")` at another knot.
 * - **A conversation** gives one line at a time, with who says it and, when
 *   the story stops to ask, the choices. Nothing here draws anything: the
 *   game shows `line` and `choices` however it likes, and calls `continue()`
 *   or `choose(i)`.
 * - **Memory.** What a story remembers (its variables, which lines have been
 *   said, how many times a knot was visited) is kept in the saved world,
 *   under `dialogue/<story>`, so a character still knows you tomorrow.
 * - **The world.** A story can ask about the saved world and change it:
 *
 *       EXTERNAL fact(name)
 *       EXTERNAL set_fact(name, value)
 *
 *       { fact("town/gate.lowered"): The gate is down, I see. }
 *       ~ set_fact("town/steward.thanked", true)
 *
 *   `fact` of something never stored is false. This is how a conversation
 *   notices what the player has done, and how it starts or advances
 *   whatever the game keeps track of there.
 * - **Who is speaking** is a tag on the line: `Mind the fire. # speaker: Steward`.
 *   It stays until another line changes it. Other tags are passed through
 *   on the line for the game to act on.
 */

export interface DialogueLine {
  /** The name from the last `# speaker:` tag, or null. */
  speaker: string | null;
  text: string;
  /** The line's other tags, as written. */
  tags: string[];
}

export interface Conversation {
  /** The story's name. */
  readonly story: string;
  /** What is being said now. */
  readonly line: DialogueLine | null;
  /** What the player may answer. Empty while there is more to hear: `continue()` then moves on. */
  readonly choices: string[];
  readonly ended: boolean;
  /** On to the next line; ends the conversation after the last. Does nothing while a choice is waited for. */
  continue(): void;
  choose(index: number): void;
  /** Walks away mid-conversation. What was said so far is remembered. */
  end(): void;
}

export interface Dialogue {
  /** Adds a compiled story (the default export of an imported `.ink` file) under a name. */
  add(name: string, compiled: string): void;
  has(name: string): boolean;
  /** Begins "story" or "story#knot". Ends whatever conversation was going on. */
  start(target: string): Conversation;
  readonly current: Conversation | null;
  /** Called when the line or the choices change, and with null when the conversation ends. */
  onChanged: ((conversation: Conversation | null) => void) | null;
}

export function createDialogue(state: WorldState): Dialogue {
  const compiled = new Map<string, string>();
  const memory = state.scope("dialogue");
  let current: Conversation | null = null;

  function start(target: string): Conversation {
    current?.end();
    const [name, knot = "start"] = target.split("#") as [string, string | undefined];
    const source = compiled.get(name);
    if (!source) throw new Error(`No story called "${name}". (Stories are the .ink files the game adds with dialogue.add.)`);

    const story = new Story(source);
    story.onError = (message) => console.warn(`Story "${name}": ${message}`);
    // The saved world, for the story to read and write. Reading is marked
    // safe to run ahead of the text, which Ink does to glue lines together.
    story.BindExternalFunction("fact", (key: string) => state.peek(String(key)) ?? false, true);
    story.BindExternalFunction("set_fact", (key: string, value: string | number | boolean) => state.set(String(key), value), false);

    // Pick up what the story remembers from earlier conversations. A story
    // rewritten since may no longer fit its memory: then it starts afresh.
    const remembered = memory.get(name, "");
    if (remembered) {
      try {
        story.state.LoadJson(remembered);
      } catch {
        console.warn(`Story "${name}" has changed since it was last saved; starting it from the beginning.`);
        story.ResetState();
      }
    }
    story.ChoosePathString(knot);

    let line: DialogueLine | null = null;
    let choices: string[] = [];
    let speaker: string | null = null;
    let ended = false;
    const remember = (): void => memory.set(name, story.state.ToJson());

    function end(): void {
      if (ended) return;
      ended = true;
      remember();
      if (current === conversation) current = null;
      api.onChanged?.(null);
    }
    /** Plays the story on to its next line of text, or to its end. */
    function advance(): void {
      while (story.canContinue) {
        const text = (story.Continue() ?? "").trim();
        const tags: string[] = [];
        for (const tag of story.currentTags ?? []) {
          const named = /^speaker\s*:\s*(.*)$/i.exec(tag);
          if (named) speaker = named[1]!.trim() || null;
          else tags.push(tag);
        }
        if (!text) continue; // a line of nothing but logic
        line = { speaker, text, tags };
        choices = story.canContinue ? [] : story.currentChoices.map((choice) => choice.text);
        api.onChanged?.(conversation);
        return;
      }
      // Nothing more to say. Choices offered with no line before them are shown with the last one.
      const offered = story.currentChoices.map((choice) => choice.text);
      if (offered.length > 0 && choices.length === 0 && !ended) {
        choices = offered;
        api.onChanged?.(conversation);
        return;
      }
      end();
    }

    const conversation: Conversation = {
      story: name,
      get line() {
        return line;
      },
      get choices() {
        return choices;
      },
      get ended() {
        return ended;
      },
      continue() {
        if (ended || choices.length > 0) return;
        advance();
      },
      choose(index) {
        if (ended || index < 0 || index >= choices.length) return;
        story.ChooseChoiceIndex(index);
        choices = [];
        remember();
        advance();
      },
      end,
    };
    current = conversation;
    advance();
    return conversation;
  }

  const api: Dialogue = {
    add: (name, story) => void compiled.set(name, story),
    has: (name) => compiled.has(name),
    start,
    get current() {
      return current;
    },
    onChanged: null,
  };
  return api;
}
