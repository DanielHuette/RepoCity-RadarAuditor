// Writes src/generated/industries.json from config/branchen.yaml, so the app offers exactly the
// industry groups the server knows, with examples, typical services and older stored values.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "..", "config", "branchen.yaml");
const target = join(root, "src", "generated", "industries.json");

function text(record, key, where) {
  const value = record[key];
  if (typeof value !== "string" || value.trim() === "") throw new Error(`${where}: "${key}" is missing`);
  return value;
}

function texts(record, key, where) {
  const value = record[key];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`${where}: "${key}" is not a list of texts`);
  }
  return value;
}

const parsed = yaml.load(await readFile(source, "utf8"));
if (!Array.isArray(parsed?.groups) || parsed.groups.length === 0) throw new Error(`${source}: "groups" is not a list`);

const groups = parsed.groups.map((group) => {
  const where = `group ${String(group.id)}`;
  return {
    id: text(group, "id", where),
    label: text(group, "label", where),
    examples: texts(group, "examples", where),
    services: texts(group, "services", where),
  };
});

const ids = new Set(groups.map((group) => group.id));
const legacy = Object.fromEntries(
  Object.entries(parsed.legacy ?? {}).map(([value, id]) => {
    if (!ids.has(id)) throw new Error(`${source}: legacy value "${value}" points to unknown group "${id}"`);
    return [value, id];
  }),
);

await mkdir(dirname(target), { recursive: true });
await writeFile(target, `${JSON.stringify({ groups, legacy }, null, 2)}\n`);
