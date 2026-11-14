import * as THREE from "three/webgpu";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { createDialogue, type Conversation, type Dialogue } from "./dialogue";
import type { Hud } from "./hud";
import type { Interact } from "./interact";
import type { LoadedMap } from "./map";
import type { Player } from "./player";
import { markerPosition, markerYaw } from "./spaces";
import type { WorldState } from "./worldState";

/**
 * People to talk to. Blender places them (an Empty with `talk = "steward"`,
 * and `npc = "fox"` if a stand-in character should be put there); Ink says
 * what is said (every `.ink` file in stories/ is a story, named after its
 * file). This file joins them up: it shows the conversation in the HUD and
 * gives the keys to it while it lasts (interact or jump for the next line,
 * the number keys to answer). The player stands still but can look around.
 */

export interface Conversations {
  readonly dialogue: Dialogue;
  readonly talking: boolean;
  /** Stands a character at each of a map's `talk` markers that asks for one. */
  followMap(map: LoadedMap): void;
  /**
   * Once a frame, with what the player did in it: asked for the next line
   * (also takes the highlighted answer), picked an answer by number (1 up,
   * 0 for none), or moved the highlight (-1 up, 1 down: a gamepad has no
   * number keys).
   */
  update(dt: number, did: { next: boolean; digit: number; step: number }): void;
}

const STORIES = import.meta.glob<string>("./stories/*.ink", { eager: true, import: "default" });
/** The stand-in for anyone: the sample fox, sitting. Metres from paws to ear tips. */
const STAND_IN = { url: "/assets/models/Fox.glb", height: 0.75, idle: "Survey" };

export function createConversations(systems: { state: WorldState; interact: Interact; hud: Hud; player: Player }): Conversations {
  const { interact, hud, player } = systems;
  const dialogue = createDialogue(systems.state);
  for (const [path, compiled] of Object.entries(STORIES)) dialogue.add(path.slice(path.lastIndexOf("/") + 1, -".ink".length), compiled);

  let talking: Conversation | null = null;
  /** The answer highlighted with the gamepad, or -1 before it has been used. */
  let selected = -1;
  /** The frame a conversation begins on still carries the key press that began it. */
  let justBegan = false;
  const mixers: THREE.AnimationMixer[] = [];

  const show = (conversation: Conversation): void => {
    if (!conversation.line) return;
    const answering = conversation.choices.length > 0;
    hud.say({
      speaker: conversation.line.speaker,
      text: conversation.line.text,
      choices: conversation.choices,
      selected,
      hint: answering ? `Press 1 to ${conversation.choices.length} to answer` : "[ E ]  continue",
    });
  };
  dialogue.onChanged = (conversation) => {
    selected = -1;
    if (conversation?.line) {
      show(conversation);
      return;
    }
    hud.say(null);
    if (conversation) return;
    talking = null;
    player.frozen = false;
    interact.suspended = false;
  };

  interact.on("talk", (person) => {
    if (talking) return;
    if (!dialogue.has(person.name.split("#")[0]!)) {
      console.warn(`"${person.marker.name}" wants to tell the story "${person.name}", but there is no such file in src/stories/.`);
      return;
    }
    player.frozen = true;
    interact.suspended = true;
    justBegan = true;
    talking = dialogue.start(person.name);
  });

  async function standIn(map: LoadedMap, marker: LoadedMap["manifest"]["markers"][number]): Promise<void> {
    const gltf = await new GLTFLoader().loadAsync(STAND_IN.url);
    const model = gltf.scene;
    const bounds = new THREE.Box3().setFromObject(model);
    const scale = STAND_IN.height / (bounds.max.y - bounds.min.y);
    model.scale.setScalar(scale);
    model.position.y = -bounds.min.y * scale;
    model.traverse((object) => {
      object.castShadow = true;
      object.receiveShadow = true;
      object.frustumCulled = false;
    });
    // In the map's own group, so it is hidden with the map. (The fox faces +z in its file.)
    const root = new THREE.Group();
    root.name = `talker-${marker.name}`;
    root.position.copy(markerPosition(marker));
    root.rotation.y = markerYaw(marker) + Math.PI;
    root.add(model);
    map.root.add(root);
    const idle = THREE.AnimationClip.findByName(gltf.animations, STAND_IN.idle);
    if (idle) {
      const mixer = new THREE.AnimationMixer(model);
      mixer.clipAction(idle).play();
      mixers.push(mixer);
    }
  }

  return {
    dialogue,
    get talking() {
      return talking !== null;
    },
    followMap(map) {
      for (const marker of map.manifest.markers) {
        if (typeof marker.props.talk === "string" && marker.props.npc) void standIn(map, marker).catch((error) => console.warn(`Could not stand anyone at "${marker.name}":`, error));
      }
    },
    update(dt, did) {
      for (const mixer of mixers) mixer.update(dt);
      if (!talking) return;
      if (justBegan) {
        justBegan = false;
        return;
      }
      const answers = talking.choices.length;
      if (answers === 0) {
        if (did.next) talking.continue();
      } else if (did.digit >= 1 && did.digit <= answers) talking.choose(did.digit - 1);
      else if (did.step !== 0) {
        selected = selected < 0 ? (did.step > 0 ? 0 : answers - 1) : (selected + did.step + answers) % answers;
        show(talking);
      } else if (did.next && selected >= 0) talking.choose(selected);
    },
  };
}
