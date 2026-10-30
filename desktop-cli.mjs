// Explicitly invoke the installed desktop CLI: Electron's ASAR entry does not
// set import.meta.main on this Windows build.
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const runtime = join(root, 'app', 'resources', 'app.asar', 'dsh');
const { runDesktopCli } = await import(pathToFileURL(join(runtime, 'node_modules', '@deepseek-ai', 'dsh-desktop-host', 'lib', 'cli.js')).href);
await runDesktopCli(runtime, join(root, 'app', 'resources', 'runtime'));
