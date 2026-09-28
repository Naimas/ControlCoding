'use strict';
const fs = require('node:fs');
const path = require('node:path');

// Fixed, nonrecursive hint subscriptions. No project content or Git process.
const DIRECTORIES = ['', '.git', '.git/refs', '.git/refs/heads', '.git/logs',
  '.controlcoding', '.controlcoding/features', '.controlcoding/project-map',
  '.controlcoding/verification_receipts', '.controlcoding/invariant_receipts'];
function identity(root) {
  try {
    if (!path.isAbsolute(root)) return null;
    const parsed = path.parse(root);
    let current = parsed.root;
    for (const part of path.relative(current, root).split(path.sep).filter(Boolean)) {
      current = path.join(current, part);
      const info = fs.lstatSync(current, {bigint:true});
      if (!info.isDirectory() || info.isSymbolicLink()) return null;
    }
    const info = fs.lstatSync(root, {bigint:true});
    return info.isDirectory() && !info.isSymbolicLink() ? `${info.dev}:${info.ino}:${info.birthtimeNs}` : null;
  } catch { return null; }
}
function watchProject(root, hint, expected = identity(root)) {
  const handles = [];
  let closed = false, unavailable = false;
  if (!expected) return {close(){}, count:0, partial:true};
  let directories = DIRECTORIES;
  try {
    const marker=path.join(root,'cc','layout.json');
    if(identity(path.join(root,'cc'))) {
      const info=fs.lstatSync(marker);
      if(info.isFile()&&!info.isSymbolicLink()&&info.nlink===1&&info.size<=1024) {
        const value=JSON.parse(fs.readFileSync(marker,'utf8'));
        if(value.schema===1&&value.layout==='contained'&&Object.keys(value).length===2)
          directories=[...DIRECTORIES.filter(p=>!p.startsWith('.controlcoding')),'cc',...DIRECTORIES.filter(p=>p.startsWith('.controlcoding')).map(p=>'cc/'+p)];
      }
    }
  } catch { /* Backend validation remains authoritative; watchers emit hints. */ }
  for (const relative of directories) {
    const target = path.join(root, relative);
    if (identity(root) !== expected || !identity(target)) continue;
    try {
      const handle = fs.watch(target, {persistent:false, recursive:false}, () => {
        if (!closed) hint(relative.startsWith('.git') ? 'git_metadata' : 'filesystem');
      });
      handle.on('error', () => {
        if (!closed && !unavailable) { unavailable = true; hint('watcher_unavailable'); }
      });
      handles.push(handle);
    } catch { unavailable = true; }
  }
  return {count:handles.length, partial:true, close(){closed=true;for(const handle of handles)handle.close();}};
}
module.exports = {identity, watchProject, DIRECTORIES};
