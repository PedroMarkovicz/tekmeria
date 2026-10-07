// Generate src/generated/audit-report.ts from the schema the Python models export.
// A model change that the view does not follow breaks `tsc`, and so the build.
import { mkdir, writeFile } from "node:fs/promises";
import { compileFromFile } from "json-schema-to-typescript";

const SCHEMA = new URL("../../schemas/audit_report.schema.json", import.meta.url);
const OUT = new URL("../src/generated/audit-report.ts", import.meta.url);

const ts = await compileFromFile(SCHEMA.pathname.replace(/^\/([A-Za-z]:)/, "$1"), {
  bannerComment:
    "/* Generated from schemas/audit_report.schema.json by scripts/gen-types.mjs. Do not edit. */",
  additionalProperties: false,
  style: { singleQuote: false },
});
await mkdir(new URL(".", OUT), { recursive: true });
await writeFile(OUT, ts);
console.log("wrote src/generated/audit-report.ts");
