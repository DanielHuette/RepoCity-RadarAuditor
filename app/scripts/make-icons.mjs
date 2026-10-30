// Renders favicon.svg and the PWA icons from the same geometry the Logo component uses.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const mark = JSON.parse(await readFile(join(root, "src/brand/mark.json"), "utf8"));
const MARK_SHARE = 0.62;
const ICON_SIZES = [192, 512];

function markShapes(stroke) {
  const { circle, horizon, bars } = mark;
  const rects = bars
    .map((bar) => `<rect x="${bar.x}" y="${bar.y}" width="${bar.width}" height="${bar.height}" rx="0.8" fill="${stroke}"/>`)
    .join("");
  return (
    `<circle cx="${circle.cx}" cy="${circle.cy}" r="${circle.r}" fill="none" stroke="${stroke}" stroke-width="${circle.stroke}"/>` +
    `<line x1="${horizon.x1}" x2="${horizon.x2}" y1="${horizon.y}" y2="${horizon.y}" stroke="${stroke}" stroke-width="${horizon.stroke}" stroke-linecap="round"/>` +
    rects
  );
}

function brassGradient(id) {
  const { light, base, deep } = mark.brass;
  return (
    `<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="${mark.viewBox}" y2="${mark.viewBox}">` +
    `<stop offset="0" stop-color="${light}"/><stop offset="0.45" stop-color="${base}"/><stop offset="1" stop-color="${deep}"/>` +
    `</linearGradient>`
  );
}

function faviconSvg() {
  const box = mark.viewBox;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${box} ${box}">` +
    `<defs>${brassGradient("brass")}</defs>${markShapes("url(#brass)")}</svg>\n`
  );
}

// Maskable icons are cropped to a circle of 80 % diameter, so the mark stays well inside it.
function iconSvg(size) {
  const box = mark.viewBox;
  const scale = (size * MARK_SHARE) / box;
  const offset = (size - box * scale) / 2;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">` +
    `<defs>${brassGradient("brass")}` +
    `<radialGradient id="glow" cx="0.3" cy="0" r="1.1"><stop offset="0" stop-color="${mark.glow}"/><stop offset="0.6" stop-color="${mark.night}"/></radialGradient>` +
    `</defs>` +
    `<rect width="${size}" height="${size}" fill="url(#glow)"/>` +
    `<g transform="translate(${offset} ${offset}) scale(${scale})">${markShapes("url(#brass)")}</g>` +
    `</svg>`
  );
}

await writeFile(join(root, "public/favicon.svg"), faviconSvg());
await mkdir(join(root, "public/icons"), { recursive: true });
for (const size of ICON_SIZES) {
  const target = join(root, `public/icons/icon-${size}.png`);
  await sharp(Buffer.from(iconSvg(size))).png({ compressionLevel: 9 }).toFile(target);
  process.stdout.write(`${target}\n`);
}
