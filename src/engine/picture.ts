import * as THREE from "three/webgpu";
import { add, diffuseColor, mrt, normalView, output, packNormalToRGB, pass, rtt, sample, screenUV, smoothstep, unpackRGBToNormal, vec3, vec4, velocity } from "three/tsl";
import { bloom } from "three/addons/tsl/display/BloomNode.js";
import { denoise } from "three/addons/tsl/display/DenoiseNode.js";
import { ao } from "three/addons/tsl/display/GTAONode.js";
import { sharpen } from "three/addons/tsl/display/SharpenNode.js";
import { ssgi } from "three/addons/tsl/display/SSGINode.js";
import { taau } from "three/addons/tsl/display/TAAUNode.js";
import { traa } from "three/addons/tsl/display/TRAANode.js";
import { params } from "./params";
import { QUALITY, type QualityLevel } from "./settings";

/**
 * The picture: one pass over the scene, and the chain of effects built on
 * it (bounced light and shade, temporal anti-aliasing, sharpening, bloom).
 *
 * `?post=0` leaves the effects out. Each can be forced on or off by name
 * whatever the quality (`?gi=0`, `?ao=1`, `?traa=0`, `?bloom=0`), to see
 * what it costs or adds; the README's "Measured again" has what each costs.
 */
export interface Picture {
  /**
   * The pass that draws the world. It exists before the effects are built,
   * because things drawn over the picture (particles) need the world's
   * depth from it to hide behind walls.
   */
  readonly scenePass: ReturnType<typeof pass>;
  /** How far away the world is at each pixel. */
  readonly sceneDepth: unknown;
  /**
   * A scene drawn by itself and laid over the picture: fire and smoke
   * (effects.ts). After the anti-aliasing, which would smear them, and
   * before the glow, which a flame should have. Set before `compose`.
   */
  overlay: THREE.Scene;
  /** Builds the effects for a level of quality (settings.ts says what each means). Called at the start and again whenever the setting changes: three compiles the new chain on the next frame. */
  compose(level: QualityLevel): void;
  /** Draws a frame. */
  render(): void;
}

export function createPicture(renderer: THREE.WebGPURenderer, scene: THREE.Scene, camera: THREE.PerspectiveCamera): Picture {
  const scenePass = pass(scene, camera);
  let overlay = new THREE.Scene();
  const usePost = params.get("post") !== "0";
  const pipeline = new THREE.RenderPipeline(renderer);
  /** Effects made for the picture as it is now, to be freed when it is built again. */
  let effectNodes: { dispose?(): void }[] = [];

  function compose(level: QualityLevel): void {
    const preset = QUALITY[level];
    for (const node of effectNodes) node.dispose?.();
    effectNodes = [];
    const made = <T>(node: T): T => {
      effectNodes.push(node as { dispose?(): void });
      return node;
    };
    if (!usePost) {
      const particles = pass(overlay, camera);
      pipeline.outputNode = vec4(scenePass.rgb.mul(particles.a.oneMinus()).add(particles.rgb), 1);
      pipeline.needsUpdate = true;
      return;
    }
    // Each effect can be forced on or off by name whatever the quality
    // (`?gi=0`, `?ao=1`, `?traa=0`, `?bloom=0`), to see what it costs or adds.
    const on = (name: string, otherwise = true): boolean => (params.has(name) ? params.get(name) !== "0" : otherwise);
    const useGi = on("gi", preset.shading === "bounced");
    const useAo = on("ao", preset.shading !== "none");
    // Every pixel is drawn, and steadied by temporal anti-aliasing, which is
    // also what averages away the noise of the bounced light below.
    // `?scale=0.75` draws fewer pixels and has three's temporal upscaler bring
    // them up to size instead. It was the default for an hour: it saves only a
    // few milliseconds here, softens the picture, and does not average that
    // noise well, which shows as grain crawling over everything in shade.
    const scale = Math.min(1, Number(params.get("scale") ?? 1));
    // One pass over the scene writes colour, and with it whatever the effects in use need.
    if (scale < 1 && on("traa")) scenePass.setResolutionScale(scale);
    // Always the same set, whatever the quality uses of it. A pass keeps the
    // buffers it was first given: asked later to write fewer, three.js looks
    // for the missing ones while compiling every material and throws.
    scenePass.setMRT(mrt({ output, diffuseColor, normal: packNormalToRGB(normalView), velocity } as never));
    const depth = scenePass.getTextureNode("depth");
    const colour = scenePass.getTextureNode("output");
    let picture: any = colour; // eslint-disable-line @typescript-eslint/no-explicit-any
    const normals = (): any => {
      scenePass.getTexture("normal").type = THREE.UnsignedByteType;
      return sample((uv) => unpackRGBToNormal(scenePass.getTextureNode("normal").sample(uv)));
    };

    if (useGi) {
      // Light bounced off what is on screen, and the shade in creases, from
      // one effect: a sunlit wall lights the wall opposite, a red awning
      // tints the plaster under it. This is what the Babylon version of the
      // town lacks most.
      scenePass.getTexture("diffuseColor").type = THREE.UnsignedByteType;
      // What it gathers light from is the picture with its brightest points
      // held down to about sunlit white. A lamp is a few pixels many times
      // brighter than that: the handful of random samples that happen to land
      // on it light their pixels like sparks, and the wall beside every lamp
      // is peppered with orange. The lamp still glows (bloom) and still lights
      // the wall, as much as a sunlit surface that size would.
      const bounce = made(ssgi(vec4(colour.rgb.min(Number(params.get("giSourceLimit") ?? 2)), 1), depth, normals(), camera));
      // The effect samples at random; what settles the result is the smoothing
      // further down and temporal anti-aliasing. With both, one slice of eight
      // steps is enough (settings.ts and the README have the measurements).
      bounce.sliceCount.value = Number(params.get("giSlices") ?? (preset.slices || 2));
      bounce.stepCount.value = Number(params.get("giSteps") ?? (preset.steps || 6));
      bounce.radius.value = 8;
      bounce.giIntensity.value = 14;
      bounce.aoIntensity.value = Number(params.get("broadStrength") ?? 1);
      bounce.thickness.value = 0.6;
      bounce.useTemporalFiltering = on("traa") && on("giTemporal");
      // At well under half the canvas each way: it is the dearest effect in the frame, and what it adds is soft.
      bounce.resolutionScale = Number(params.get("giScale") ?? (preset.bounceScale || 0.5));
      // What comes out is grainy: a few random samples per pixel. Temporal
      // anti-aliasing averages most of it away on textured surfaces, but on
      // anything of one flat colour (an awning, plaster) what is left shows as
      // dark mottling that crawls. So both results are first smoothed across
      // neighbouring pixels that lie on the same surface (same depth, same
      // facing). `?denoise=0` shows it without.
      // The smoothing is done at half the canvas each way and stretched back
      // up, not at full size. Written into the picture's own shader it ran for
      // every pixel of the canvas, twice (shade, and bounced light), and was
      // the dearest thing in the frame: 8.5 ms of 26 at 2.25 million pixels,
      // more than the bounced light it was cleaning. What it smooths was only
      // ever worked out at half size. `?smoothScale=1` is the old way.
      const smoothScale = Number(params.get("smoothScale") ?? 0.5);
      const smooth = (node: any, type: THREE.TextureDataType = THREE.HalfFloatType): any => { // eslint-disable-line @typescript-eslint/no-explicit-any
        if (!on("denoise")) return node;
        const cleaned = made(denoise(node, depth, normals(), camera));
        cleaned.radius.value = Number(params.get("denoiseRadius") ?? 8);
        return made(rtt(cleaned, null, null, { resolutionScale: smoothScale, type } as never));
      };
      // That effect reaches eight metres, to carry light across a street, and
      // what it gives as shade is as broad: a general dimming, with nothing at
      // the foot of a wall or under an eave. A second, short-reach occlusion
      // puts the dark where two surfaces meet.
      // The two are multiplied before they are smoothed, not after: smoothing
      // is the dear part (a full-size pass), the short-reach occlusion needs it
      // as much as the broad one (raised to a power, its grain flickers ten
      // times worse than the rest of the picture), and one pass serves both.
      const broad: any = bounce.getAONode(); // eslint-disable-line @typescript-eslint/no-explicit-any
      let occlusion: any = broad; // eslint-disable-line @typescript-eslint/no-explicit-any
      let debug: any = null; // eslint-disable-line @typescript-eslint/no-explicit-any
      if (on("contact")) {
        const near = made(ao(depth, normals(), camera));
        near.resolutionScale = 0.5;
        near.samples.value = 8;
        near.radius.value = Number(params.get("contactRadius") ?? 1.2);
        // As it comes it is faint (hardly anything is hidden from a point by what is within a metre of it): `scale` is the power it is raised to.
        near.scale.value = Number(params.get("contactStrength") ?? 2);
        near.useTemporalFiltering = on("traa");
        const contact: any = near.getTextureNode(); // eslint-disable-line @typescript-eslint/no-explicit-any
        if (params.get("show") === "contact") debug = contact.sample(screenUV).r;
        // (Drawn into a texture of its own first: the smoothing wants a texture to read, not a sum.)
        occlusion = made(rtt(broad.mul(contact.sample(screenUV).r), null, null, { resolutionScale: smoothScale, type: THREE.UnsignedByteType } as never));
      }
      const shade = smooth(occlusion, THREE.UnsignedByteType).r;
      if (params.get("show") === "broad") debug = smooth(broad, THREE.UnsignedByteType).r;
      // Capped: one sample that happens to land on a lamp or the sun's glint
      // would otherwise light its pixel like a spark.
      const bounced = smooth(bounce.getGINode()).rgb.min(2.5);
      picture = vec4(add(colour.rgb.mul(shade), scenePass.getTextureNode("diffuseColor").rgb.mul(bounced)), colour.a);
      // `?show=broad|contact|bounce` puts one of the effect's parts on screen by itself, to see what it is doing.
      if (params.get("show") === "bounce") debug = bounced;
      if (debug) picture = vec4(vec3(debug), 1);
    } else if (useAo) {
      const occlusion = made(ao(depth, normals(), camera));
      occlusion.resolutionScale = 0.5;
      occlusion.samples.value = 8;
      occlusion.radius.value = 1.4;
      occlusion.scale.value = 2; // as it comes it is faint
      occlusion.useTemporalFiltering = on("traa");
      picture = picture.mul(occlusion.getTextureNode().sample(screenUV).r);
    }
    if (on("traa")) {
      const steady = made((scale < 1 ? taau : traa)(picture, depth, scenePass.getTextureNode("velocity"), camera));
      if (!on("subpixel")) (steady as unknown as { useSubpixelCorrection: boolean }).useSubpixelCorrection = false;
      picture = steady;
      // Temporal anti-aliasing steadies the picture and softens it: measured
      // on a still, it takes away more than half of the fine detail (cobbles,
      // stonework, grass). Contrast-adaptive sharpening puts most of it back.
      // Its `denoise` option holds back where the picture is noisy, which is
      // what stops it magnifying the grain of the bounced light: plain
      // sharpening did exactly that here once and was taken out.
      // `?sharpen=0` shows the picture without; `?sharpness=` is 0 (most) to 2 (none).
      if (on("sharpen")) picture = made(sharpen(picture, Number(params.get("sharpness") ?? 0.2), true));
    }
    // Fire and smoke, drawn by themselves and laid over the picture (see
    // effects.ts): after the anti-aliasing, which would smear them, and before
    // the glow, which a flame should have.
    const particles = pass(overlay, camera);
    picture = vec4(picture.rgb.mul(particles.a.oneMinus()).add(particles.rgb), 1);
    if (on("bloom", preset.bloom)) {
      // Only what is far brighter than sunlit stone glows (lamps, glints on
      // water, the sun's own patch of sky), and not by much. With a low
      // threshold the whole sky and every sunlit wall bleed into a haze.
      // What glows is also capped. The sun's disc is thousands of times
      // brighter than a lamp: uncapped, even a faint glow from it veils the
      // whole picture in white whenever the sun is in view.
      const glow = made(bloom(vec4(picture.rgb.min(Number(params.get("bloomCap") ?? 4.5)), 1), Number(params.get("bloomStrength") ?? 0.07), 0.35, Number(params.get("bloomThreshold") ?? 3.2)));
      glow.setResolutionScale(0.25); // a soft glow needs few pixels
      picture = picture.add(glow);
    }
    // A little darker toward the corners, as a lens is.
    const fromCentre = screenUV.sub(0.5).length();
    picture = picture.mul(smoothstep(0.95, 0.35, fromCentre).mul(0.28).add(0.72));
    pipeline.outputNode = picture;
    pipeline.needsUpdate = true;
  }

  return {
    scenePass,
    sceneDepth: scenePass.getTextureNode("depth"),
    get overlay() {
      return overlay;
    },
    set overlay(value: THREE.Scene) {
      overlay = value;
    },
    compose,
    render: () => pipeline.render(),
  };
}
