#!/usr/bin/env node
// Fetches surface textures from Poly Haven (polyhaven.com), whose assets are
// all CC0: free for any use, no credit required.
//
//   npm run texture:get -- cobblestone_floor_08 [more ids...] [--res 1k|2k|4k]
//       -> maps/textures/<id>/color.jpg    the surface's colour
//                              normal.jpg   its fine relief (OpenGL convention, as glTF wants)
//                              arm.jpg      occlusion, roughness and metalness in red, green and blue
//                              info.json    its name, real-world size and where it came from
//   npm run texture:get -- --search roof
//       -> lists matching ids, most downloaded first
//
// The id is the last part of the asset's address on the site:
// polyhaven.com/a/cobblestone_floor_08. Blender scripts pick the set up with
// `surface(...)` from tools/blender/surfaces.py; for a level built by hand,
// load the three files into a material as described in the README.
//
// 1k is plenty for surfaces that repeat every metre or two, and what the map
// export compresses best. Go higher only for something seen very close.
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const API = "https://api.polyhaven.com";
const headers = { "User-Agent": "game-scaffold-tools" }; // the API asks callers to name themselves

/** Our file name for each of Poly Haven's maps. */
const MAPS = { color: "Diffuse", normal: "nor_gl", arm: "arm" };

async function getJson(url) {
  const response = await fetch(url, { headers });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

async function search(term) {
  const assets = await getJson(`${API}/assets?t=textures`);
  const matches = Object.entries(assets)
    .filter(([id, asset]) => `${id} ${asset.name} ${asset.tags?.join(" ")} ${asset.categories?.join(" ")}`.toLowerCase().includes(term.toLowerCase()))
    .sort((a, b) => (b[1].download_count ?? 0) - (a[1].download_count ?? 0));
  for (const [id, asset] of matches.slice(0, 30)) {
    const metres = asset.dimensions?.[0] ? `${(asset.dimensions[0] / 1000).toFixed(1)} m` : "?";
    console.log(`${id.padEnd(34)} ${asset.name}  (repeats every ${metres})`);
  }
  if (matches.length === 0) console.log(`Nothing matches "${term}".`);
}

async function fetchSet(id, resolution) {
  const [files, asset] = await Promise.all([getJson(`${API}/files/${id}`), getJson(`${API}/info/${id}`)]);
  const folder = join(root, "maps", "textures", id);
  mkdirSync(folder, { recursive: true });
  let bytes = 0;
  for (const [name, map] of Object.entries(MAPS)) {
    const file = files[map]?.[resolution]?.jpg;
    if (!file) throw new Error(`${id} has no ${map} map at ${resolution}`);
    const response = await fetch(file.url, { headers });
    if (!response.ok) throw new Error(`${file.url}: HTTP ${response.status}`);
    const data = Buffer.from(await response.arrayBuffer());
    if (file.md5 && createHash("md5").update(data).digest("hex") !== file.md5) throw new Error(`${file.url} arrived damaged; try again`);
    writeFileSync(join(folder, `${name}.jpg`), data);
    bytes += data.length;
  }
  const info = {
    id,
    name: asset.name,
    // How much ground one repeat of the texture covers, so it can be laid at its real size.
    metres: asset.dimensions?.[0] ? Math.round(asset.dimensions[0] / 10) / 100 : 1,
    resolution,
    source: `https://polyhaven.com/a/${id}`,
    license: "CC0",
    authors: Object.keys(asset.authors ?? {}),
  };
  writeFileSync(join(folder, "info.json"), `${JSON.stringify(info, null, 2)}\n`);
  console.log(`${id}: ${info.name}, ${resolution}, repeats every ${info.metres} m, ${(bytes / 1e6).toFixed(1)} MB -> maps/textures/${id}/`);
}

const args = process.argv.slice(2);
const flag = (name) => {
  const index = args.indexOf(name);
  return index < 0 ? undefined : args.splice(index, 2)[1];
};
const term = flag("--search");
const resolution = flag("--res") ?? "1k";
try {
  if (term) await search(term);
  else if (args.length > 0) for (const id of args) await fetchSet(id, resolution);
  else console.error("usage:\n  node tools/textures.mjs <id> [more ids...] [--res 1k|2k|4k]\n  node tools/textures.mjs --search <word>");
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
