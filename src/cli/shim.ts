import { dirname } from "node:path";

/**
 * The managed command runs on the Node it was installed with. Codex launches
 * through `env node`, so a private Node from the bootstrap goes first on PATH,
 * as it did during the bootstrap; a system Node never reorders the caller's PATH.
 */
export function shim(execPath: string): string {
  const node = `'${execPath.replaceAll("'", "'\\''")}'`;
  return `#!/bin/sh\n':' //; exec ${node} "$0" "$@"\n// thc-managed-shim v1\nimport { existsSync, readFileSync, readdirSync } from 'node:fs';\nimport { dirname, join, resolve } from 'node:path';\nimport { homedir } from 'node:os';\nimport { spawnSync } from 'node:child_process';\nconst args=process.argv.slice(2); const i=args.indexOf('--dir'); const inline=args.find(x=>x.startsWith('--dir='));
const override=i>=0 ? args[i+1] : inline?.slice(6);
let p=resolve(override || process.env.THC_DIR || process.cwd());\nif (p.endsWith('/.thc')) p=dirname(p);\nlet v; for (;;) { const f=join(p,'.thc','version'); if (existsSync(f)) { v=readFileSync(f,'utf8').trim(); break; } const n=dirname(p); if (n===p) break; p=n; }\nconst root=join(homedir(),'.local','share','thc');\nif (!v) v=readdirSync(root).filter(x=>/^\\d+\\.\\d+\\.\\d+(?:-[0-9A-Za-z.-]+)?$/.test(x)).sort((a,b)=>{const [aa,ap]=a.split('-',2),[bb,bp]=b.split('-',2);const n=aa.localeCompare(bb,undefined,{numeric:true});return n || (ap===undefined ? 1 : bp===undefined ? -1 : ap.localeCompare(bp,undefined,{numeric:true}));}).at(-1);\nif (!v || !/^\\d+\\.\\d+\\.\\d+(?:-[0-9A-Za-z.-]+)?$/.test(v)) { console.error('thc: E_DEPENDENCY: No valid installed version\\nfix: thc init --update'); process.exit(4); }\nconst tools=join(process.env.XDG_DATA_HOME||join(homedir(),'.local','share'),'thc','toolchains'); let codex=[]; try { codex=readdirSync(tools).filter(x=>/^codex-\\d+\\.\\d+\\.\\d+/.test(x)).sort((a,b)=>b.localeCompare(a,undefined,{numeric:true})).map(x=>join(tools,x,'bin')); } catch {}\nconst pinned=${JSON.stringify(dirname(execPath))}; const own=pinned.startsWith(tools+'/'); const current=process.env.PATH?process.env.PATH.split(':'):[]; const base=own?current.filter(x=>x!==pinned):current; const extra=[...(own?[]:[pinned]),...codex].filter(x=>!base.includes(x)); const env={...process.env,PATH:[...(own?[pinned]:[]),...base,...extra].join(':')};\nconst r=spawnSync(process.execPath,[join(root,v,'dist','cli','main.js'),...process.argv.slice(2)],{stdio:'inherit',env}); if(r.error) { console.error('thc: E_DEPENDENCY: '+r.error.message+'\\nfix: thc init --update'); process.exit(4); } process.exit(r.status ?? 4);\n`;
}
