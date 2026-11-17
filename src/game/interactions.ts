import type { LevelInfo } from "../config";
import type { Engine } from "../engine/engine";
import type { Hud } from "./hud";

/**
 * What the named things in a level do when they are used or walked into.
 *
 * The map says where a thing is and what it is called (a marker with
 * `interact = "campfire"`, a zone called `market_square`); this says what
 * happens. Anything that should stay as it was left is written to the
 * saved world under the level's name.
 */
export function addInteractions(engine: Engine, level: LevelInfo, hud: Hud): { /** Brings what is on screen into line with the saved world again, after it was put back to an earlier state. */ sync(): void } {
  const { interact, effects, audio, state } = engine;

  // Walking into a named part of the level says so.
  for (const [zone, name] of Object.entries(level.places ?? {})) interact.onEnter(zone, () => hud.toast(name));

  // Fires can be put out and lit again, and stay as they were left.
  const fires = state.scope(level.id).scope("fire");
  const sync = (): void => {
    for (const fire of interact.all("interact", "campfire")) {
      const out = fires.get(`${fire.marker.name}.out`, false);
      effects.setPlaced(fire.marker.name, !out);
      // (A fire that was never touched keeps the prompt its map gave it.)
      if (out) fire.prompt = "Light the fire";
      else if (fire.prompt === "Light the fire") fire.prompt = "Put out the fire";
    }
  };
  sync();
  interact.on("interact", (used) => {
    if (used.name === "well") {
      hud.toast("The water is cold and clear.");
      audio.play("land", { volume: 0.4, playbackRate: 1.6 });
      effects.burst("dust", used.position);
    }
    if (used.name !== "campfire") return;
    const relight = fires.get(`${used.marker.name}.out`, false);
    fires.set(`${used.marker.name}.out`, relight ? undefined : true);
    effects.setPlaced(used.marker.name, relight);
    if (relight) effects.burst("sparks", used.position);
    used.prompt = relight ? "Put out the fire" : "Light the fire";
  });
  return { sync };
}
