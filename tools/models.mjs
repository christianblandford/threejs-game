#!/usr/bin/env node
// Fetches 3D models from Poly Haven (polyhaven.com; all CC0: free for any use).
//
//   npm run model:get -- shrub_03 [more ids...] [--res 1k|2k]
//       -> maps/models/<id>/<id>.gltf, its .bin and its textures, plus info.json
//   npm run model:get -- --search fern
//       -> lists matching ids with their triangle counts
//
// Look at the triangle count before fetching. Poly Haven's models are scans
// meant for film: a shrub is a few thousand triangles and fine to place by
// the hundred, but its trees and grass patches run from one to seventeen
// MILLION triangles, which no game can draw in numbers. Build those instead
// (tools/blender/plants.py).
//
// A model is brought into a map by importing its .gltf in Blender (File >
// Import > glTF) or with `import_model` from tools/blender/plants.py.
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const API = "https://api.polyhaven.com";
const headers = { "User-Agent": "game-scaffold-tools" }; // the API asks callers to name themselves

async function getJson(url) {
  const response = await fetch(url, { headers });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

async function download(file, target) {
  const response = await fetch(file.url, { headers });
  if (!response.ok) throw new Error(`${file.url}: HTTP ${response.status}`);
  const data = Buffer.from(await response.arrayBuffer());
  if (file.md5 && createHash("md5").update(data).digest("hex") !== file.md5) throw new Error(`${file.url} arrived damaged; try again`);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, data);
  return data.length;
}

async function search(term) {
  const assets = await getJson(`${API}/assets?t=models`);
  const matches = Object.entries(assets)
    .filter(([id, asset]) => `${id} ${asset.name} ${asset.tags?.join(" ")} ${asset.categories?.join(" ")}`.toLowerCase().includes(term.toLowerCase()))
    .sort((a, b) => (a[1].polycount ?? 0) - (b[1].polycount ?? 0));
  for (const [id, asset] of matches.slice(0, 40)) console.log(`${id.padEnd(30)} ${String(asset.polycount ?? "?").padStart(9)} triangles  ${asset.name}`);
  if (matches.length === 0) console.log(`Nothing matches "${term}".`);
}

async function fetchModel(id, resolution) {
  const [files, asset] = await Promise.all([getJson(`${API}/files/${id}`), getJson(`${API}/info/${id}`)]);
  const entry = files.gltf?.[resolution]?.gltf;
  if (!entry) throw new Error(`${id} has no glTF at ${resolution}`);
  const folder = join(root, "maps", "models", id);
  let bytes = await download(entry, join(folder, `${id}.gltf`));
  for (const [path, file] of Object.entries(entry.include ?? {})) bytes += await download(file, join(folder, path));
  const info = { id, name: asset.name, triangles: asset.polycount, resolution, source: `https://polyhaven.com/a/${id}`, license: "CC0", authors: Object.keys(asset.authors ?? {}) };
  writeFileSync(join(folder, "info.json"), `${JSON.stringify(info, null, 2)}\n`);
  console.log(`${id}: ${info.name}, ${info.triangles?.toLocaleString()} triangles, ${(bytes / 1e6).toFixed(1)} MB -> maps/models/${id}/`);
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
  else if (args.length > 0) for (const id of args) await fetchModel(id, resolution);
  else console.error("usage:\n  node tools/models.mjs <id> [more ids...] [--res 1k|2k]\n  node tools/models.mjs --search <word>");
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
