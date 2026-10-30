// Lazy shared-profile bridge: login UI and agent attach to the same Chrome.
import {spawn} from 'node:child_process';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=dirname(fileURLToPath(import.meta.url));
const platform=process.argv[2]==='douyin'?'douyin':'bilibili';
const port=platform==='douyin'?9333:9334;
const child=spawn(process.execPath,[join(root,'runtime/node_modules/@playwright/mcp/cli.js'),'--cdp-endpoint',`http://127.0.0.1:${port}`,'--output-dir',join(root,'browser-output')],{cwd:root,stdio:['pipe','pipe','inherit'],windowsHide:true});
child.stdout.pipe(process.stdout);
let buffer='',queue=Promise.resolve();
function ensure(){return new Promise((resolve,reject)=>{const p=spawn(join(root,'.venv/Scripts/python.exe'),[join(root,'browser_hub.py'),platform],{cwd:root,env:{...process.env,PYTHONUTF8:'1'},stdio:['ignore','ignore','pipe'],windowsHide:true});let error='';p.stderr.on('data',chunk=>{error+=chunk;});p.once('error',reject);p.once('close',code=>code===0?resolve():reject(new Error(error.slice(-2000)||'共享浏览器未就绪')));});}
process.stdin.setEncoding('utf8');process.stdin.on('data',chunk=>{buffer+=chunk;let i;while((i=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,i);buffer=buffer.slice(i+1);queue=queue.then(async()=>{let message;try{message=JSON.parse(line);}catch{child.stdin.write(line+'\n');return;}if(message.method==='tools/call'&&message.params?.name!=='browser_close'){try{await ensure();}catch(error){process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:message.id,result:{isError:true,content:[{type:'text',text:error.message}]}})+'\n');return;}}child.stdin.write(line+'\n');}).catch(error=>{console.error(error.message);});}});
process.stdin.on('end',()=>child.stdin.end());
process.on('SIGINT',()=>child.kill());process.on('SIGTERM',()=>child.kill());
child.once('exit',code=>{process.exitCode=code??0;process.stdin.pause();});
