// Lets DSH's Settings/Credentials pages work when served from our real
// domain instead of only 127.0.0.1/localhost.
//
// dsh's browser bundle gates "host" settings persistence behind an
// isLoopbackHostname(window.location.hostname) check (see
// dsh-client-connection's client.js) -- reasonable for its default
// single-user-on-their-own-machine use case, where a non-loopback origin
// usually means the instance got exposed to a network it shouldn't have
// been. That threat model doesn't fit a JupyterHub deployment: access is
// already gated by real per-user LDAP auth before anyone reaches dsh at
// all, and dsh-api-settings-controller (the *server* side of these RPCs)
// enforces nothing based on hostname or origin -- it only checks that the
// caller has a valid session. So this check is a client-side UX nicety, not
// an access-control boundary: any authenticated user could already call
// these RPCs from their browser's JS console today. Trusting our own
// domain here just lets the normal Settings UI do the same thing.
//
// Only the one hostname passed in gets trusted -- never a blanket bypass --
// so a typo'd or unset value fails closed (the file is left untouched, not
// patched to trust everything).
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import * as acorn from "acorn";

const root = process.argv[2];
const trustedHostname = process.argv[3];
if (!root) {
  console.error("Usage: node patch-trusted-hostname.mjs <node_modules_root> [trusted-hostname]");
  process.exit(1);
}
if (!trustedHostname) {
  console.log("No trusted hostname given -- skipping (dsh's Settings pages will only work from 127.0.0.1/localhost).");
  process.exit(0);
}

const files = [];
(function walkDir(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) walkDir(path);
    else if (entry.isFile() && path.endsWith(".js")) files.push(path);
  }
})(root);

let patchedCount = 0;
for (const file of files) {
  const src = readFileSync(file, "utf8");
  if (!src.includes("isLoopbackHostname")) continue;

  const ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "module", ranges: true });

  let target = null;
  function walk(node) {
    if (target || !node || typeof node.type !== "string") return;
    if (
      node.type === "FunctionDeclaration" &&
      node.id?.name === "isLoopbackHostname" &&
      node.body?.type === "BlockStatement"
    ) {
      target = node.body;
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
  if (!target) continue;

  const insertAt = target.start + 1; // right after the function body's opening "{"
  const injected = `if (hostname === ${JSON.stringify(trustedHostname)}) return true; `;
  const out = src.slice(0, insertAt) + injected + src.slice(insertAt);

  // Syntax-check before committing the rewrite.
  acorn.parse(out, { ecmaVersion: "latest", sourceType: "module" });

  writeFileSync(file, out, "utf8");
  patchedCount += 1;
  console.log(`patched isLoopbackHostname (trusting ${trustedHostname}) in ${file}`);
}

if (patchedCount === 0) {
  console.error(
    "WARNING: isLoopbackHostname was not found in any bundled file -- " +
      "trusted-hostname patch was NOT applied. dsh's own code shape may have " +
      "changed; Settings pages will only work from 127.0.0.1/localhost."
  );
} else {
  console.log(`Done: patched ${patchedCount} file(s) to also trust hostname ${trustedHostname}.`);
}
