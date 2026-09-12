// Converts every Simplified-Chinese string/regex literal in the installed
// @deepseek-ai/dsh* packages' bundled client.js files to Traditional Chinese
// (Taiwan wording), in place, using OpenCC's cn->twp profile.
//
// Strategy: parse each file, walk every Literal (string) and RegExpLiteral
// node in the whole file, and replace it with its OpenCC conversion iff the
// conversion actually changes it. This is idempotent (already-Traditional
// or non-Chinese text is left byte-identical) and doesn't rely on guessing
// which variable-naming convention ("zh", "zh$1", "accessZh", "FOO_ZH", ...)
// a given dictionary happens to use -- it catches all of them, plus stray
// regexes elsewhere in the file that match against Chinese literals (e.g.
// stripping a "(推荐)" suffix), which a name-based approach would miss.
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import * as acorn from "acorn";
import * as OpenCC from "opencc-js";

const root = process.argv[2];
if (!root) {
  console.error("Usage: node patch-zh-locale.mjs <node_modules_root>");
  process.exit(1);
}

const rawConverter = OpenCC.Converter({ from: "cn", to: "twp" });
const cjk = /[㐀-鿿]/;

// OpenCC's phrase-refinement layer (simplified word -> Taiwan-preferred
// word, e.g. 发送 -> 傳送 rather than the merely-character-converted 發送)
// doesn't always fully resolve in one pass -- segmentation of a compound
// depends on surrounding characters, and re-feeding the already-converted
// text can surface a further, more idiomatic substitution. Iterate to a
// fixed point so we get the fully Taiwan-refined form regardless.
function converter(text) {
  let prev = text;
  for (let i = 0; i < 5; i++) {
    const next = rawConverter(prev);
    if (next === prev) break;
    prev = next;
  }
  return prev;
}

// `npm install -g` doesn't hoist the way a local install does: dsh's ~240
// dependencies end up nested inside dsh's own node_modules (and some of
// those, in turn, nest a second time under their own node_modules where
// npm couldn't dedupe a version), so "@deepseek-ai/<pkg>/lib/*.js" only
// exists at a fixed depth by luck. Walk the whole tree recursively instead
// and match any "@deepseek-ai/<pkg>/lib/*.js" path regardless of how deep
// it's nested.
const pkgLibJs = /@deepseek-ai[/\\][^/\\]+[/\\]lib[/\\][^/\\]+\.js$/;
const files = [];
(function walkDir(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      walkDir(path);
    } else if (entry.isFile() && path.endsWith(".js") && pkgLibJs.test(path)) {
      if (cjk.test(readFileSync(path, "utf8"))) files.push(path);
    }
  }
})(root);

console.log(`Found ${files.length} candidate files (any CJK content).`);

let totalStrings = 0;
let totalFiles = 0;

for (const file of files) {
  const src = readFileSync(file, "utf8");
  const ast = acorn.parse(src, {
    ecmaVersion: "latest",
    sourceType: "module",
    allowReturnOutsideFunction: true,
    ranges: true,
  });

  const targets = []; // {start, end, newText}

  function walk(node) {
    if (!node || typeof node.type !== "string") return;
    if (node.type === "Literal") {
      if (typeof node.value === "string" && cjk.test(node.value)) {
        const converted = converter(node.value);
        if (converted !== node.value) {
          targets.push({ start: node.start, end: node.end, newText: JSON.stringify(converted) });
        }
      } else if (node.regex && cjk.test(node.regex.pattern)) {
        const converted = converter(node.regex.pattern);
        if (converted !== node.regex.pattern) {
          targets.push({ start: node.start, end: node.end, newText: `/${converted}/${node.regex.flags}` });
        }
      }
      return;
    }
    if (node.type === "TemplateElement") {
      const raw = node.value.raw;
      if (cjk.test(raw)) {
        const converted = converter(raw);
        if (converted !== raw) {
          // TemplateElement's range covers exactly the raw text between
          // delimiters (verified against acorn directly), so splice it in
          // place -- but escape backtick/backslash/${ ourselves, since
          // template quasis aren't JSON and JSON.stringify would produce
          // the wrong quoting style.
          const escaped = converted.replace(/\\/g, "\\\\").replace(/`/g, "\\`").replace(/\$\{/g, "\\${");
          targets.push({ start: node.start, end: node.end, newText: escaped });
        }
      }
      return;
    }
    for (const key in node) {
      if (key === "start" || key === "end" || key === "loc" || key === "range") continue;
      const val = node[key];
      if (Array.isArray(val)) {
        for (const item of val) {
          if (item && typeof item.type === "string") walk(item);
        }
      } else if (val && typeof val.type === "string") {
        walk(val);
      }
    }
  }
  walk(ast);

  if (targets.length === 0) continue;

  targets.sort((a, b) => b.start - a.start);
  let out = src;
  for (const t of targets) {
    out = out.slice(0, t.start) + t.newText + out.slice(t.end);
  }

  // Syntax-check before committing the rewrite.
  acorn.parse(out, { ecmaVersion: "latest", sourceType: "module" });

  writeFileSync(file, out, "utf8");
  totalStrings += targets.length;
  totalFiles += 1;
  console.log(`patched ${targets.length} literals in ${file}`);
}

console.log(`Done: ${totalStrings} literals across ${totalFiles} files.`);
