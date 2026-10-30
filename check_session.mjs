import {readdir,readFile,stat} from 'node:fs/promises';
import {join} from 'node:path';
import {zstdDecompressSync} from 'node:zlib';
const paths=[];async function walk(p){for(const f of await readdir(p,{withFileTypes:true})){const n=join(p,f.name);if(f.isDirectory())await walk(n);else if(f.name.endsWith('.zstd'))paths.push([n,(await stat(n)).mtimeMs]);}}
await walk('data/sessions');
for(const [p] of paths.sort((a,b)=>b[1]-a[1]).slice(0,2)){
const rows=zstdDecompressSync(await readFile(p)).toString().split('\n').filter(Boolean).map(l=>JSON.parse(l));
console.log(p); console.log(JSON.stringify(rows.slice(-3)).slice(-3500));
}
