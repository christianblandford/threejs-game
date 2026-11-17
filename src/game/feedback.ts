import * as THREE from "three/webgpu";
import type { Engine } from "../engine/engine";
import type { Player } from "./player";

/**
 * What the player's own movement sounds and looks like: a jump, a landing
 * that raises dust in proportion to how hard it was, water thrown about on
 * going in or climbing out.
 *
 * No sound files yet: the few sounds are made up from a description
 * (engine/audio.ts), and one of them at different speeds stands in for
 * several.
 */
export function addFeedback(engine: Engine, player: Player): void {
  const { audio, effects } = engine;
  audio.synth("jump", { duration: 0.16, frequency: 260, frequencyEnd: 520, gain: 0.25 });
  audio.synth("land", { duration: 0.14, frequency: 90, frequencyEnd: 50, noise: 0.6, gain: 0.6 });

  player.onJump = () => audio.play("jump", { playbackRate: 0.95 + Math.random() * 0.1 });
  player.onLand = (impactSpeed) => {
    audio.play("land", { volume: Math.min(1, impactSpeed / 8) });
    effects.burst("dust", player.position, { scale: Math.min(1.6, impactSpeed / 4) });
  };
  player.onSwimChanged = ({ swimming, speed }) => {
    audio.play("land", { volume: Math.min(1, 0.4 + speed / 8), playbackRate: swimming ? 0.5 : 0.8 });
    effects.burst("splash", player.position.clone().add(new THREE.Vector3(0, 0.42, 0)), { scale: swimming ? Math.min(1.8, 0.7 + speed / 5) : 0.6 });
  };
}
