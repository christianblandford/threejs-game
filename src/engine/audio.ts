import * as THREE from "three/webgpu";

/**
 * Sound, on three's own audio classes: one `AudioListener` on the camera
 * (the ears, and the master volume), and a few `Audio` voices per sound.
 * A sound that should come from somewhere in the world wants
 * `THREE.PositionalAudio` on the same listener.
 *
 * There are no sound files yet. `synth` makes a small effect from a
 * description (a swept tone, some noise, a quick fade), enough for a jump, a
 * thud and a splash; `load` takes a file's URL when there are real ones.
 */

export interface SynthOptions {
  /** Seconds. */
  duration: number;
  /** Start pitch in Hz. */
  frequency: number;
  /** End pitch in Hz (sweeps from `frequency`). Defaults to no sweep. */
  frequencyEnd?: number;
  /** 0 = pure tone, 1 = pure noise. */
  noise?: number;
  /** Peak amplitude, 0..1. */
  gain?: number;
}

export interface GameAudio {
  readonly listener: THREE.AudioListener;
  play(name: string, options?: { volume?: number; playbackRate?: number }): void;
  load(name: string, url: string): Promise<void>;
  synth(name: string, options: SynthOptions): void;
  /** 0..1 */
  masterVolume: number;
}

const SAMPLE_RATE = 44100;
/** How many of one sound may play over each other. */
const VOICES = 4;

function renderSynth({ duration, frequency, frequencyEnd = frequency, noise = 0, gain = 0.5 }: SynthOptions): AudioBuffer {
  const length = Math.max(1, Math.floor(duration * SAMPLE_RATE));
  const buffer = new AudioBuffer({ length, sampleRate: SAMPLE_RATE, numberOfChannels: 1 });
  const data = buffer.getChannelData(0);
  let phase = 0;
  let smoothed = 0;
  for (let i = 0; i < length; i++) {
    const t = i / length;
    phase += (2 * Math.PI * (frequency + (frequencyEnd - frequency) * t)) / SAMPLE_RATE;
    smoothed += (Math.random() * 2 - 1 - smoothed) * 0.12; // low-passed noise: a thud, not a hiss
    const envelope = Math.min(1, i / 120) * (1 - t) ** 2; // short attack, smooth decay
    data[i] = gain * envelope * ((1 - noise) * Math.sin(phase) + noise * smoothed * 3);
  }
  return buffer;
}

export function createAudio(camera: THREE.Camera): GameAudio {
  const listener = new THREE.AudioListener();
  camera.add(listener);
  // Browsers keep audio silent until the player has touched the page.
  const wake = (): void => {
    if (listener.context.state === "suspended") void listener.context.resume();
  };
  window.addEventListener("pointerdown", wake);
  window.addEventListener("keydown", wake);

  const sounds = new Map<string, { voices: THREE.Audio[]; next: number }>();
  function register(name: string, buffer: AudioBuffer): void {
    // A sound given again under the same name takes the old one's place, and the old one's voices are let go.
    for (const voice of sounds.get(name)?.voices ?? []) {
      if (voice.isPlaying) voice.stop();
      voice.gain.disconnect();
    }
    const voices = Array.from({ length: VOICES }, () => new THREE.Audio(listener).setBuffer(buffer));
    sounds.set(name, { voices, next: 0 });
  }

  return {
    listener,
    play(name, options) {
      const sound = sounds.get(name);
      if (!sound || listener.context.state !== "running") return;
      const voice = sound.voices[sound.next++ % VOICES]!;
      if (voice.isPlaying) voice.stop();
      voice.setVolume(options?.volume ?? 1);
      voice.setPlaybackRate(options?.playbackRate ?? 1);
      voice.play();
    },
    async load(name, url) {
      register(name, await new THREE.AudioLoader().loadAsync(url));
    },
    synth: (name, options) => register(name, renderSynth(options)),
    get masterVolume() {
      return listener.getMasterVolume();
    },
    set masterVolume(value: number) {
      listener.setMasterVolume(Math.min(1, Math.max(0, value)));
    },
  };
}
