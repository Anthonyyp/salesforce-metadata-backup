// Keep what the org listed but didn't hand over.
//
// Usage: node keep_undelivered.js <retrieve folder>   (run from the repo, after the pull, before `git add`)
//
// A pull reports each item it was asked for and couldn't deliver ("Entity of type 'Flow' named 'X' cannot be
// found"). Most are built-in items that were never in the backup. But one that is in the backup was listed by the
// org tonight, so it hasn't been deleted: leave its files as they were instead of committing them as deleted.
// Uses the Salesforce CLI's own library to tell which files belong to which item. Prints the number kept.
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// SF_CLI_MODULES: the folder the workflow installed the CLI into. Found the way the CLI itself would find it.
const cli = path.join(process.env.SF_CLI_MODULES, '@salesforce/cli');
const { ComponentSet } = require(require.resolve('@salesforce/source-deploy-retrieve', { paths: [cli] }));

const folder = process.argv[2];
const notDelivered = new Set();
for (const name of fs.existsSync(folder) ? fs.readdirSync(folder) : []) {
  let messages;
  try {
    messages = JSON.parse(fs.readFileSync(path.join(folder, name), 'utf8')).result?.messages ?? [];
  } catch {
    continue;
  }
  for (const m of Array.isArray(messages) ? messages : [messages]) {
    const hit = /Entity of type '([^']+)' named '([^']+)' cannot be found/.exec(m?.problem ?? '');
    if (hit) notDelivered.add(`${hit[1]}:${hit[2]}`);
  }
}

const git = (...args) => execFileSync('git', args, { encoding: 'utf8', maxBuffer: 1 << 28 });
const deleted = git('ls-files', '--deleted', '-z', '--', 'force-app').split('\0').filter(Boolean);
if (!notDelivered.size || !deleted.length) {
  console.log(0);
  process.exit(0);
}

// Put the deleted files back so the library can read them, keep those of undelivered items, and remove the rest
// again, whatever happens in between. A file the library can't place stays deleted, as it would have been anyway.
for (let i = 0; i < deleted.length; i += 500) git('checkout', 'HEAD', '--', ...deleted.slice(i, i + 500));
const keep = new Set();
const keptItems = new Set();
try {
  for (const f of deleted) {
    if (keep.has(path.resolve(f))) continue;
    let components;
    try {
      components = ComponentSet.fromSource({ fsPaths: [path.normalize(f)] }).getSourceComponents().toArray();
    } catch {
      continue;
    }
    for (const c of components) {
      const key = `${c.type.name}:${c.fullName}`;
      if (!notDelivered.has(key)) continue;
      keptItems.add(key);
      for (const file of [c.xml, ...(c.content ? c.walkContent() : [])].filter(Boolean)) keep.add(path.resolve(file));
    }
  }
} finally {
  for (const f of deleted) if (!keep.has(path.resolve(f))) fs.rmSync(f, { force: true });
}
console.log(keptItems.size);
