// Writes src/generated/catalogue.json from config/pruefkatalog.yaml, so the app lists exactly the
// checks of the catalogue with their names and customer wording; runs before dev, build, check and test.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "..", "config", "pruefkatalog.yaml");
const target = join(root, "src", "generated", "catalogue.json");

function flag(record, key, where) {
  const value = record[key];
  if (typeof value !== "boolean") throw new Error(`${where}: "${key}" must be true or false`);
  return value;
}

function required(record, key, where) {
  const value = record[key];
  if (value === undefined || value === null || value === "") throw new Error(`${where}: "${key}" is missing`);
  return value;
}

const parsed = yaml.load(await readFile(source, "utf8"));
if (!Array.isArray(parsed?.areas)) throw new Error(`${source}: "areas" is not a list`);

const areas = parsed.areas.map((area) => {
  const where = `area ${String(area.id)}`;
  if (!Array.isArray(area.checks) || area.checks.length === 0) throw new Error(`${where}: "checks" is not a list`);
  const weight = Number(required(area, "weight", where));
  // Same rule as scoring/evaluator.py: the area weight is shared equally by its checks.
  const checkWeight = Math.round((weight / area.checks.length) * 100) / 100;
  return {
    id: String(required(area, "id", where)),
    name: String(required(area, "name", where)),
    weight,
    checks: area.checks.map((check) => {
      const at = `check ${String(check.id)} in ${where}`;
      return {
        id: String(required(check, "id", at)),
        name: String(required(check, "name", at)),
        why: String(required(check, "why", at)),
        area: String(area.id),
        source: String(required(check, "source", at)),
        weight: checkWeight,
        requiresPaid: flag(check, "requires_paid", at),
        requiresAccess: flag(check, "requires_access", at),
        customerQuestion: String(required(check, "customer_question", at)),
        customerImpact: String(required(check, "customer_impact", at)),
      };
    }),
  };
});

await mkdir(dirname(target), { recursive: true });
await writeFile(target, `${JSON.stringify({ areas }, null, 2)}\n`);
