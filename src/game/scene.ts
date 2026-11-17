import * as THREE from "three/webgpu";
import type { LevelInfo } from "../config";
import type { Engine } from "../engine/engine";
import { PAD } from "../engine/input";
import { loadMap, type LoadedMap } from "../engine/map";
import { navBox } from "../engine/navigation";
import { activeQuality, numberOf, params } from "../engine/params";
import { getSettings, QUALITY } from "../engine/settings";
import { createSimulation, TICK, type Frame, type Simulation } from "../engine/simulation";
import { createSpaces, markerYaw, type Spaces, type Way } from "../engine/spaces";
import type { StateValue } from "../engine/worldState";
import { addControls } from "./controls";
import { createConversations, type Conversations, type TalkSnapshot } from "./conversations";
import { addFeedback } from "./feedback";
import { createFlyCamera, type FlyCamera } from "./flyCamera";
import { createGate, type Gate, type GateSnapshot } from "./gate";
import { createHud, type Hud } from "./hud";
import { addInteractions } from "./interactions";
import type { Menu } from "./menu";
import { createNpcs, type NpcSnapshot, type Npcs } from "./npcs";
import { createPlayer, type Player, type PlayerSnapshot } from "./player";
import { createSaving } from "./saving";
import { createShell } from "./shell";

/**
 * The game: a level loaded into the engine's scene, a player in it, the
 * people and things of the place, and what a tick of play is.
 *
 * This is the file to read to see how a game is put together, and the one
 * to change to make a different one. Saved, it is put together again in
 * the running page (main.ts): the engine and the loaded map stay.
 *
 * The world moves in ticks (engine/simulation.ts): `tick` below is the
 * whole of what happens in one, from one frame of input. A frame of the
 * display runs the ticks that are due and draws between the last two.
 */
export interface Game {
  readonly level: LevelInfo;
  readonly map: LoadedMap;
  readonly player: Player;
  readonly spaces: Spaces;
  readonly conversations: Conversations;
  readonly npcs: Npcs;
  readonly gate: Gate | null;
  readonly hud: Hud;
  readonly menu: Menu;
  readonly fly: FlyCamera;
  readonly simulation: Simulation;
  /** A frame of the display, given the time since the last (seconds): runs the ticks that are due and draws. Behind a menu the world stands still. */
  update(seconds: number): void;
  /** What only needs doing once a frame, given the point the world is loaded around: streaming, building where characters can walk, particles. */
  advance(seconds: number, eye: THREE.Vector3): void;
  /** The point the world is loaded around: the player (flying, the camera). */
  focus(): THREE.Vector3;
  /** Resolves when the world around the player is loaded and still: nothing arriving, no door being gone through, the walkable area built. */
  ready(): Promise<void>;
  /** The state of play as plain data, for reading and comparing: rounded, and named for people. */
  state(): GameState;
  /** Everything the next tick depends on, exactly; and putting it back. */
  snapshot(): GameSnapshot;
  restore(snapshot: GameSnapshot): Promise<void>;
  /** Takes the game out of the engine. With `keepMap`, the level's map stays loaded for the next game on it. */
  dispose(keepMap?: boolean): void;
}

export interface GameState {
  tick: number;
  seed: number;
  level: string;
  space: string;
  player: { position: number[]; yaw: number; pitch: number; grounded: boolean; swimming: boolean; view: string };
  npcs: { position: number[] }[];
  gate: GateSnapshot | null;
  talking: boolean;
  /** What pressing "interact" would do now, as the prompt on screen says it; or null. */
  offered: string | null;
  world: Record<string, StateValue>;
}

export interface GameSnapshot {
  tick: number;
  /** Where the game's dice have got to, and the navigation's own. */
  random: number;
  walkRandom: number;
  space: string;
  /** The player, with the position relative to the place's own zero. */
  player: PlayerSnapshot;
  npcs: NpcSnapshot;
  gate: GateSnapshot | null;
  world: Record<string, StateValue>;
  /** The conversation going on, at the line it has reached. */
  talk: TalkSnapshot | null;
  /** The way back out of each place that was walked into. */
  ways: Record<string, Way>;
  /** Where the level is solid: its chunks loaded in detail, and the point it is held around while the player is indoors. */
  detail: string[];
  held: [number, number, number] | null;
}

/** How fast the gamepad's right stick turns the view (radians a second at full tilt). */
const STICK_LOOK_SPEED = 2.6;
/** Where the player is, is written to the saved world this often (ticks: every two seconds of play). By the tick and not by the clock, so that it is part of the run like everything else. */
const SAVE_EVERY = 120;
/** What a tick may be told was pressed. */
const PRESSES = ["jump", "interact", "toggleView"] as const;

export async function createGame(engine: Engine, level: LevelInfo, reuse: { map?: LoadedMap; start?: "play" | "continue" | null } = {}): Promise<Game> {
  const { scene, camera, physics, renderer, interact, effects, navigation, walkable, input, mouse, audio, state, random } = engine;

  // --- The level -----------------------------------------------------------------
  const around = new THREE.Vector3(...level.around);
  const map =
    reuse.map ??
    (await loadMap(renderer, level.map, {
      around,
      aroundMarker: "spawn",
      // `?detail=` and `?view=` set how far full detail and anything at all reach (m): small values show the streaming at work.
      detailDistance: numberOf("detail", 80),
      viewDistance: numberOf("view", 400),
      // How much grass goes with the quality setting, as it stands when the level loads (`?grass=` overrides it).
      grass: numberOf("grass", QUALITY[activeQuality()].grass),
      materials: level.materials,
      physics,
    }));
  if (!reuse.map) scene.add(map.root);
  // Start where the map says: its `spawn` marker, facing the way it faces.
  const spawnMarker = map.manifest.markers.find((marker) => marker.name === "spawn");
  const spawn = map.marker("spawn") ?? around.clone();
  map.shown = true;
  map.hold(null);
  map.update(spawn);
  // The ground must be there before anything stands on it; and only what is
  // near: a map kept from the game before this one still holds what that
  // game's player walked past, and a run must not start from another's leavings.
  await map.reset();
  // The walkable area is built nearest this point first.
  navigation.update(0, spawn);

  // --- The player ----------------------------------------------------------------
  /** The water's surface where the player is: asked of the place they are in, once places exist (below). */
  let waterHere = (position: THREE.Vector3): number | null => map.waterAt(position.x, position.z);
  const player = await createPlayer(scene, camera, physics, spawn.clone().add(new THREE.Vector3(0, 0.3, 0)), spawnMarker ? markerYaw(spawnMarker) : 0, { water: (position) => waterHere(position) });
  addFeedback(engine, player);

  // --- People, places and things ---------------------------------------------------
  const hud = createHud();
  const conversations = createConversations({ state, interact, hud, player, random });
  const spaces = createSpaces({
    home: { id: level.id, map },
    interact,
    find: (id) => level.interiors.find((interior) => interior.id === id),
    async load(space, origin) {
      // An interior is small: all of it in detail, no grass.
      const interior = await loadMap(renderer, space.map, { around: origin, detailDistance: 200, viewDistance: 400, grass: 0, physics, origin });
      scene.add(interior.root);
      return interior;
    },
    // Whatever is attached to "the map" has to be told about every map: the level's, and each interior as it loads.
    attach(_id, attached) {
      conversations.followMap(attached);
      effects.followMap(attached);
      walkable.follow(attached);
    },
    detach(_id, detached) {
      effects.forget(detached);
      walkable.forget(detached);
    },
    light: (environment) => engine.lighting.apply(environment),
    place: (position, toYaw) => player.teleport(position, toYaw),
    whereabouts: () => ({ position: player.position, yaw: player.yaw }),
  });
  waterHere = (position) => spaces.map(spaces.current)?.waterAt(position.x, position.z) ?? null;
  const gate = createGate({
    map,
    physics,
    interact,
    hud,
    state: state.scope(level.id),
    feet: player.position,
    // A gate that is down is a wall to those who walk by themselves.
    block: (box) => walkable.block(box ? [navBox(box.centre, box.half, box.rotation)] : []),
    moved: (what) => audio.play("land", what === "lever" ? { volume: 0.5, playbackRate: 2 } : { volume: 0.9, playbackRate: 0.45 }),
  });
  // A few foxes with business of their own, in the square.
  const npcs = await createNpcs({ parent: map.root, navigation, physics, around: map.marker("market_square") ?? around, player, random });
  const interactions = addInteractions(engine, level, hud);

  // --- Starting, saving, menus ---------------------------------------------------
  const saving = createSaving({ state, level, map, spaces, player });
  /** True while a saved moment is being put back: going where it was is not an arrival to write down. */
  let restoring = false;
  spaces.onTravelled = () => {
    if (restoring) return;
    saving.save();
    audio.play("land", { volume: 0.5, playbackRate: 0.7 });
  };
  const shell = await createShell({ level, state, saving, start: reuse.start });
  const { menu } = shell;
  const fly = createFlyCamera(camera, params.has("fly"));
  /** The prompt on screen, to change it only when it changes. */
  let offered: string | null = null;
  /** Steps of the wheel since the last tick. */
  let wheel = 0;
  const removeControls = addControls({ engine, menu, player, hud, fly, onZoom: (steps) => (wheel += steps), onLeftPlay: () => hud.prompt((offered = null)) });

  /**
   * What the world is loaded around: the player, not the camera. The camera
   * eases to its place by the screen's clock; what is loaded (so what is
   * solid, and the order the walkable area is built in) must not go by
   * that, or the same run comes out differently. Flying, it is the camera:
   * there is nothing else to go by.
   */
  const focus = (): THREE.Vector3 => (fly.active ? camera.position : player.position);

  // --- A tick ----------------------------------------------------------------------
  /** The whole of what happens in a sixtieth of a second, from one frame of input. Nothing in here may look at the clock or at `Math.random()`. */
  function tick(frame: Frame, index: number): void {
    const pressed = (action: string): boolean => frame.actions.includes(action);
    const digit = Number(frame.actions.find((action) => action.startsWith("answer"))?.slice("answer".length) ?? 0);
    // Whatever arrived or was put somewhere since the last tick is solid from this one on.
    physics.refresh();
    // The view is where this tick's input says it is, and the camera exactly there: what can be used is judged from it.
    player.setLook(frame.look[0], frame.look[1]);
    if (frame.zoom) player.zoom(frame.zoom);
    if (!fly.active) player.aim();
    // Space answers in a conversation; it is a jump only when nobody is talking.
    const busy = conversations.talking || spaces.travelling;
    const jumping = pressed("jump") && !busy;
    if (pressed("toggleView")) player.setViewMode(player.viewMode === "first" ? "third" : "first");
    interact.update(player.position, camera, spaces.current, pressed("interact"));
    conversations.update(TICK, { next: pressed("interact") || pressed("jump"), digit, step: Number(pressed("down")) - Number(pressed("up")) });
    gate?.update(TICK);
    player.frozen = busy;
    player.simulate(TICK, { move: { x: frame.move[0], y: frame.move[1] }, run: frame.run, jump: jumping });
    physics.step(TICK);
    navigation.update(TICK, focus());
    npcs.update(TICK);
    if ((index + 1) % SAVE_EVERY === 0) saving.save();
  }

  /** What was pressed since the last tick. A press is not lost when a frame runs no tick, nor counted twice when it runs two. */
  const latched = new Set<string>();
  const idle = (): Frame => ({ move: [0, 0], run: false, actions: [], look: [player.yaw, player.pitch], zoom: 0 });
  /** This tick's input, from the keyboard, mouse and gamepad. */
  function devices(): Frame {
    const frame: Frame = { move: fly.active ? [0, 0] : [input.move.x, input.move.y], run: !fly.active && input.isDown("sprint"), actions: fly.active ? [] : [...latched], look: [player.yaw, player.pitch], zoom: fly.active ? 0 : wheel };
    latched.clear();
    wheel = 0;
    return frame;
  }

  async function ready(): Promise<void> {
    const pause = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
    for (;;) {
      while (spaces.travelling) await pause(30);
      spaces.update(focus());
      await spaces.map(spaces.current)?.settled();
      navigation.update(0, focus());
      await navigation.settled();
      if (!spaces.travelling) return;
    }
  }

  /** False until the world around the start has finished arriving: the frame loop's first tick, like a stepped one, finds the ground and the walkable area there. */
  let begun = false;
  const simulation = createSimulation({
    tick,
    idle,
    blocked: () => !begun || menu.state !== "playing" || spaces.travelling,
    settle: ready,
  });
  // (Not for ever: a world that will not settle should still be playable.)
  void Promise.race([ready(), new Promise((resolve) => setTimeout(resolve, 8000))]).then(() => (begun = true));

  // --- A frame ---------------------------------------------------------------------
  function advance(seconds: number, eye: THREE.Vector3): void {
    spaces.update(eye); // streams the place the player is in around the camera
    navigation.update(0, eye); // builds a little more of where characters can walk; they are moved by the ticks
    effects.update(seconds, eye);
  }

  function update(passed: number): void {
    // Behind a menu the world stands still: nothing is given any time.
    const live = menu.state === "playing";
    const seconds = live ? passed : 0;
    input.update();
    if (input.wasPressed("pause")) menu.togglePause();
    // The view is turned by the mouse, and by the gamepad's right stick (a rate, so by the time that has passed).
    const turned = mouse.turned();
    const stick = STICK_LOOK_SPEED * getSettings().lookSensitivity * seconds;
    turned.yaw -= input.lookStick.x * stick;
    turned.pitch -= input.lookStick.y * stick * (getSettings().invertY ? -1 : 1);
    if (!live) {
      latched.clear();
      wheel = 0;
      // The keys and the pad are the menu's.
      menu.steer({
        move: Number(input.wasPadPressed(PAD.down)) - Number(input.wasPadPressed(PAD.up)),
        adjust: Number(input.wasPadPressed(PAD.right)) - Number(input.wasPadPressed(PAD.left)),
        accept: input.wasPadPressed(PAD.accept),
        back: input.wasPadPressed(PAD.back),
      });
    } else if (fly.active) {
      fly.update(seconds, turned, input);
    } else {
      // Looking around is not left waiting for a tick: the view turns with the mouse, frame by frame.
      player.look(-turned.yaw, -turned.pitch);
      for (const action of PRESSES) if (input.wasPressed(action)) latched.add(action);
      const digit = input.digitPressed();
      if (digit > 0) latched.add(`answer${digit}`);
      if (input.wasPadPressed(PAD.up)) latched.add("up");
      if (input.wasPadPressed(PAD.down)) latched.add("down");
    }
    simulation.advance(seconds, devices);
    input.endFrame();

    // Drawn between the last two ticks.
    if (!fly.active) player.present(seconds, simulation.alpha);
    npcs.present(simulation.alpha);
    // Asked every frame, not on a change of focus: using a thing may change what it offers.
    const offer = live && !fly.active ? interact.focus?.prompt ?? null : null;
    if (offer !== offered) hud.prompt((offered = offer));
    advance(seconds, focus());
  }

  let disposed = false;
  const round = (value: number): number => Math.round(value * 1000) / 1000;
  const api: Game = {
    level,
    map,
    player,
    spaces,
    conversations,
    npcs,
    gate,
    hud,
    menu,
    fly,
    simulation,
    update,
    advance,
    focus,
    ready,
    state: () => ({
      tick: simulation.tick,
      seed: random.seed,
      level: level.id,
      space: spaces.current,
      player: { position: player.position.toArray().map(round), yaw: round(player.yaw), pitch: round(player.pitch), grounded: player.grounded, swimming: player.swimming, view: player.viewMode },
      npcs: npcs.snapshot().map((npc) => ({ position: npc.position.map(round) })),
      gate: gate?.snapshot() ?? null,
      talking: conversations.talking,
      offered: interact.focus?.prompt ?? null,
      world: state.snapshot(),
    }),
    snapshot() {
      const own = player.snapshot();
      const origin = spaces.origin(spaces.current);
      own.position = [own.position[0] - origin.x, own.position[1] - origin.y, own.position[2] - origin.z];
      return {
        tick: simulation.tick,
        random: random.state,
        walkRandom: navigation.randomState,
        space: spaces.current,
        player: own,
        npcs: npcs.snapshot(),
        gate: gate?.snapshot() ?? null,
        world: state.snapshot(),
        talk: conversations.snapshot(),
        ways: spaces.ways(),
        detail: map.detail,
        held: map.heldAt ? [map.heldAt.x, map.heldAt.y, map.heldAt.z] : null,
      };
    },
    async restore(saved) {
      // Nothing is changed before it is known that all of it can be.
      if (saved.space !== level.id && !level.interiors.some((interior) => interior.id === saved.space)) throw new Error(`The snapshot is of "${saved.space}", a place this level does not have.`);
      // A door being gone through is gone through first: the trip would otherwise finish on top of what is put back.
      await ready();
      conversations.end();
      state.restore(saved.world);
      const feet = new THREE.Vector3(...saved.player.position);
      restoring = true;
      try {
        if (saved.space !== spaces.current) await spaces.travel(saved.space, { position: feet, yaw: saved.player.yaw });
      } finally {
        restoring = false;
      }
      spaces.setWays(saved.ways);
      feet.add(spaces.origin(saved.space));
      // The level, solid exactly where it was then.
      if (saved.space === level.id) map.update(feet);
      else if (saved.held) map.hold(new THREE.Vector3(...saved.held));
      await map.reset(saved.detail);
      const here = spaces.map(saved.space);
      here?.update(feet);
      await here?.settled();
      player.restore({ ...saved.player, position: feet.toArray() });
      // Where characters can walk follows from what is solid: wait for it before standing them on it.
      await ready();
      npcs.restore(saved.npcs);
      if (saved.gate) gate?.restore(saved.gate);
      interactions.sync();
      conversations.restore(saved.talk);
      // Last: nothing above may have thrown the dice since.
      random.state = saved.random;
      navigation.randomState = saved.walkRandom;
      simulation.rewind(saved.tick);
    },
    dispose(keepMap = false) {
      if (disposed) return;
      disposed = true;
      removeControls();
      shell.dispose();
      hud.dispose();
      conversations.dispose();
      npcs.dispose();
      gate?.dispose();
      player.dispose();
      spaces.dispose();
      interact.clear();
      effects.forget(map);
      walkable.block([]);
      if (!keepMap) {
        walkable.forget(map);
        map.dispose();
      }
    },
  };
  return api;
}
