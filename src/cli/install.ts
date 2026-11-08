import { cp, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { DatabaseSync, backup } from "node:sqlite";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { SCHEMA_VERSION } from "../store/index.js";
import { CliError } from "./errors.js";
import { exists } from "./paths.js";
import { defaults, type CliDependencies } from "./context.js";
import { withSetupLock } from "./setup-lock.js";

export interface InstallPaths {
  home: string;
  directory: string;
  packageRoot: string;
  exec?: CliDependencies["exec"];
}
interface VersionInfo {
  version: string;
  schema: number;
}
function versionName(value: string): string {
  if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(value))
    throw new CliError(
      "E_USAGE",
      "Invalid package version",
      "thc init --help",
      2,
    );
  return value;
}
async function readVersion(path: string): Promise<VersionInfo> {
  const data: unknown = JSON.parse(await readFile(path, "utf8"));
  if (
    typeof data !== "object" ||
    !data ||
    !("version" in data) ||
    !("schema" in data) ||
    typeof data.version !== "string" ||
    typeof data.schema !== "number"
  )
    throw new CliError(
      "E_DEPENDENCY",
      "Invalid installed version metadata",
      "thc init --update",
      4,
    );
  return { version: versionName(data.version), schema: data.schema };
}
async function installedSchema(directory: string): Promise<number | undefined> {
  const path = join(directory, ".thc", "store.db");
  if (!(await exists(path))) return undefined;
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    return (db.prepare("PRAGMA user_version").get() as { user_version: number })
      .user_version;
  } finally {
    db.close();
  }
}
async function snapshot(directory: string): Promise<void> {
  const path = join(directory, ".thc", "store.db");
  if (!(await exists(path))) return;
  const target = join(
    directory,
    ".thc",
    `store-backup-${String(Date.now())}-${randomUUID()}.db`,
  );
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    await backup(db, target);
  } finally {
    db.close();
  }
}
function shim(): string {
  const node = `'${process.execPath.replaceAll("'", "'\\''")}'`;
  return `#!/bin/sh\n':' //; exec ${node} "$0" "$@"\n// thc-managed-shim v1\nimport { existsSync, readFileSync, readdirSync } from 'node:fs';\nimport { dirname, join, resolve } from 'node:path';\nimport { homedir } from 'node:os';\nimport { spawnSync } from 'node:child_process';\nconst args=process.argv.slice(2); const i=args.indexOf('--dir'); const inline=args.find(x=>x.startsWith('--dir='));
const override=i>=0 ? args[i+1] : inline?.slice(6);
let p=resolve(override || process.env.THC_DIR || process.cwd());\nif (p.endsWith('/.thc')) p=dirname(p);\nlet v; for (;;) { const f=join(p,'.thc','version'); if (existsSync(f)) { v=readFileSync(f,'utf8').trim(); break; } const n=dirname(p); if (n===p) break; p=n; }\nconst root=join(homedir(),'.local','share','thc');\nif (!v) v=readdirSync(root).filter(x=>/^\\d+\\.\\d+\\.\\d+(?:-[0-9A-Za-z.-]+)?$/.test(x)).sort((a,b)=>{const [aa,ap]=a.split('-',2),[bb,bp]=b.split('-',2);const n=aa.localeCompare(bb,undefined,{numeric:true});return n || (ap===undefined ? 1 : bp===undefined ? -1 : ap.localeCompare(bp,undefined,{numeric:true}));}).at(-1);\nif (!v || !/^\\d+\\.\\d+\\.\\d+(?:-[0-9A-Za-z.-]+)?$/.test(v)) { console.error('thc: E_DEPENDENCY: No valid installed version\\nfix: thc init --update'); process.exit(4); }\nconst r=spawnSync(process.execPath,[join(root,v,'dist','cli','main.js'),...process.argv.slice(2)],{stdio:'inherit',env:process.env}); if(r.error) { console.error('thc: E_DEPENDENCY: '+r.error.message+'\\nfix: thc init --update'); process.exit(4); } process.exit(r.status ?? 4);\n`;
}
async function writeShim(home: string): Promise<void> {
  const directory = join(home, ".local", "bin");
  await mkdir(directory, { recursive: true });
  const path = join(directory, "thc");
  if (await exists(path)) {
    const current = await readFile(path, "utf8");
    if (!current.includes("// thc-managed-shim v1"))
      throw new CliError(
        "E_DEPENDENCY",
        "An existing thc executable is not managed by this installation",
        "Move the existing executable, then run thc init",
        4,
      );
  }
  const temp = `${path}.${randomUUID()}`;
  await writeFile(temp, shim(), { mode: 0o755, flag: "wx" });
  await rename(temp, path);
}
async function verifyPackage(root: string): Promise<void> {
  for (const path of ["dist/cli/main.js", "package.json"]) {
    if (!(await exists(join(root, path))))
      throw new CliError(
        "E_DEPENDENCY",
        "Installed package is incomplete",
        "Rebuild or reinstall telegram-harness-cli",
        4,
      );
  }
}
async function installCurrent(
  paths: InstallPaths,
  version: string,
): Promise<void> {
  const root = join(paths.home, ".local", "share", "thc");
  const destination = join(root, version);
  if (await exists(destination)) {
    const current = await readVersion(join(destination, "version.json"));
    if (current.version !== version || current.schema !== SCHEMA_VERSION)
      throw new CliError(
        "E_DEPENDENCY",
        "Installed version has incompatible metadata",
        "thc init --update",
        4,
      );
    await verifyPackage(destination);
    return;
  }
  await verifyPackage(paths.packageRoot);
  await mkdir(root, { recursive: true });
  const temp = join(root, `.install-${randomUUID()}`);
  await mkdir(temp, { mode: 0o700 });
  try {
    for (const name of ["dist", "skills"])
      await cp(join(paths.packageRoot, name), join(temp, name), {
        recursive: true,
        errorOnExist: true,
        force: false,
      });
    await cp(
      join(paths.packageRoot, "package.json"),
      join(temp, "package.json"),
    );
    const shrinkwrap = join(paths.packageRoot, "npm-shrinkwrap.json");
    const lock = (await exists(shrinkwrap))
      ? shrinkwrap
      : join(paths.packageRoot, "package-lock.json");
    await cp(lock, join(temp, "package-lock.json"));
    await (paths.exec ?? defaults().exec)("npm", ["ci", "--omit=dev"], {
      cwd: temp,
    });
    await writeFile(
      join(temp, "version.json"),
      JSON.stringify({ version, schema: SCHEMA_VERSION }),
      { mode: 0o600 },
    );
    await rename(temp, destination);
  } catch (error) {
    await rm(temp, { recursive: true, force: true });
    throw error;
  }
}
export async function install(
  paths: InstallPaths,
  packageVersion: string,
  target?: string,
): Promise<string> {
  const version = versionName(target ?? packageVersion);
  const root = join(paths.home, ".local", "share", "thc");
  const existing = await installedSchema(paths.directory);
  if (existing !== undefined && existing > SCHEMA_VERSION)
    throw new CliError(
      "E_STORE_SCHEMA_NEWER",
      "Database schema is newer than this executable",
      "thc init --update",
      4,
    );
  if (target && target !== packageVersion) {
    const info = await readVersion(join(root, version, "version.json"));
    const schema = await installedSchema(paths.directory);
    if (schema !== undefined && schema !== info.schema)
      throw new CliError(
        "E_STORE_SCHEMA_NEWER",
        "Cannot roll back across database schema versions",
        "Install a version compatible with the current database",
        4,
      );
    if (info.version !== version)
      throw new CliError(
        "E_DEPENDENCY",
        "Installed version metadata does not match its directory",
        "thc init --update",
        4,
      );
    await verifyPackage(join(root, version));
  } else
    await withSetupLock(paths.home, "installation", () =>
      installCurrent(paths, version),
    );
  await snapshot(paths.directory);
  await withSetupLock(paths.home, "installation", () => writeShim(paths.home));
  await mkdir(join(paths.directory, ".thc"), { recursive: true, mode: 0o700 });
  const temp = join(paths.directory, ".thc", `.version-${randomUUID()}`);
  await writeFile(temp, `${version}\n`, { mode: 0o600, flag: "wx" });
  await rename(temp, join(paths.directory, ".thc", "version"));
  return version;
}
export async function registerFolder(
  home: string,
  directory: string,
  version: string,
): Promise<void> {
  await withSetupLock(home, "folders", () =>
    updateFolders(home, directory, version),
  );
}
async function updateFolders(
  home: string,
  directory: string,
  version: string,
): Promise<void> {
  const root = join(home, ".local", "share", "thc");
  await mkdir(root, { recursive: true });
  const target = join(root, "folders");
  let rows: Record<string, string> = {};
  if (await exists(target)) {
    const parsed: unknown = JSON.parse(await readFile(target, "utf8"));
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed))
      rows = Object.fromEntries(
        Object.entries(parsed).filter(
          (entry): entry is [string, string] => typeof entry[1] === "string",
        ),
      );
  }
  rows[directory] = version;
  const temp = `${target}.${randomUUID()}`;
  await writeFile(temp, JSON.stringify(rows), { mode: 0o600, flag: "wx" });
  await rename(temp, target);
}
