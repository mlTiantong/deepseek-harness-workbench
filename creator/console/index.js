import {readFileSync, writeFileSync, mkdirSync, renameSync, unlinkSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join, resolve} from 'node:path';
import {spawn, execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {replyPrompt} from './reply-prompt.js';

export const name = 'creator-console';
export const inject = ['webServer', 'connection', 'typertGateway'];
const DIR=dirname(fileURLToPath(import.meta.url));
const ROOT=resolve(DIR,'..');
const PYTHON=join(ROOT,'.venv','Scripts','python.exe');
const API='/creator-console/api/';

export function rpc(method,payload={}) {
  return new Promise((resolve,reject)=>{
    const child=spawn(PYTHON,[join(ROOT,'console_rpc.py')],{cwd:ROOT,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
    let output='',error='';
    const timer=setTimeout(()=>{child.kill();reject(new Error('本地控制服务超时'));},25000);
    child.stdout.on('data',data=>{output+=data.toString('utf8');if(output.length>2000000) child.kill();});
    child.stderr.on('data',data=>{error+=data.toString('utf8');});
    child.once('error',err=>{clearTimeout(timer);reject(err);});
    child.once('close',()=>{clearTimeout(timer);try {const data=JSON.parse(output);data.ok?resolve(data.value):reject(new Error(data.error));}catch {reject(new Error('本地控制服务没有返回有效结果'));}});
    child.stdin.end(JSON.stringify({method,payload}));
  });
}

function json(res,status,value){res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'});res.end(JSON.stringify(value));}
async function body(req){let text='';for await(const chunk of req){text+=chunk;if(Buffer.byteLength(text)>65536) throw new Error('请求过大');}return text?JSON.parse(text):{};}

export function apply(ctx) {
  const registration=ctx.webServer.register({kind:'prefix',path:'/creator-console',handler:async(req,res)=>{
    const admission=ctx.connection.admit(req);
    if('rejection' in admission){res.writeHead(admission.rejection,{'content-type':'text/plain; charset=utf-8'});res.end('请先从已登录的 DSH 打开知游工作台，或使用根目录的统一入口。');return;}
    if(admission.peer.principal.kind!=='owner'){json(res,403,{ok:false,error:'仅本机账号所有者可以使用创作者工作台'});return;}
    const url=new URL(req.url,'http://127.0.0.1');
    try {
      if(!url.pathname.startsWith(API)){
        if(req.method!=='GET'){json(res,405,{error:'method_not_allowed'});return;}
        const files={'/creator-console':'index.html','/creator-console/':'index.html','/creator-console/app.js':'app.js','/creator-console/style.css':'style.css'};
        const file=files[url.pathname];if(!file){json(res,404,{error:'not_found'});return;}
        res.writeHead(200,{'content-type':file.endsWith('.js')?'text/javascript; charset=utf-8':file.endsWith('.css')?'text/css; charset=utf-8':'text/html; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff','content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"});res.end(readFileSync(join(DIR,file)));return;
      }
      if(req.method!=='POST'||req.headers['x-creator-console']!=='1'){json(res,403,{error:'invalid_request'});return;}
      const data=await body(req);
      const endpoint=url.pathname.slice(API.length);
      if(['snapshot','settings','login','check_login','pause'].includes(endpoint)){
        json(res,200,{ok:true,value:await rpc(endpoint,data)});return;
      }
      if(endpoint==='sessions'){
        const result=await ctx.typertGateway.invoke({namespace:'session',method:'list',args:{_request:{}},peer:admission.peer});
        const items=result.items.filter(row=>row.cwd&&resolve(row.cwd).toLowerCase()===ROOT.toLowerCase());
        json(res,200,{ok:true,value:items.map(row=>({sessionId:row.sessionId,running:row.running,updatedAt:row.updatedAt}))});return;
      }
      if(endpoint==='task'){
        const platforms=['xiaohongshu','bilibili','douyin','qq','wechat'];
        const selected=data.platform?platforms.filter(p=>p===data.platform):platforms;
        if(!selected.length||data.kind!=='reply'||typeof data.sessionId!=='string') throw new Error('无效任务参数');
        const sessions=await ctx.typertGateway.invoke({namespace:'session',method:'list',args:{_request:{}},peer:admission.peer});
        if(!sessions.items.some(row=>row.sessionId===data.sessionId&&row.cwd&&resolve(row.cwd).toLowerCase()===ROOT.toLowerCase())) throw new Error('请选择 creator 工作区里的原有会话');
        const text=replyPrompt(selected);
        const receipt=await ctx.typertGateway.invoke({namespace:'session',method:'prompt',args:{request:{requestId:randomUUID(),sessionId:data.sessionId,mode:'queue',content:[{type:'text',text}],clientTimeZone:'Asia/Shanghai'}},peer:admission.peer});
        json(res,200,{ok:true,value:receipt});return;
      }
      json(res,404,{error:'not_found'});
    } catch(error){json(res,400,{ok:false,error:String(error.message||error)});}
  }});
  ctx.effect(()=>registration,'creator-console: authenticated window');
  ctx.on('webserver/index-inject',table=>{table.push({kind:'script',placement:'body',text:`if(location.hash==='#creator-console'){location.replace('/creator-console/')}else if(window===window.top){const mount=()=>{const b=document.createElement('a');b.textContent='知游工作台';b.href='/creator-console/';b.target='_blank';b.style.cssText='position:fixed;right:18px;bottom:18px;z-index:9999;background:#1e6155;color:white;padding:10px 16px;border-radius:12px;text-decoration:none;font:14px sans-serif';document.body.append(b)};if(document.readyState==='loading')addEventListener('DOMContentLoaded',mount);else mount()}`});});
  const state=join(ROOT,'state');mkdirSync(state,{recursive:true});
  const discovery=join(state,'console-host.json');
  const tmp=discovery+'.tmp';
  // A launch token is sensitive: restrict the file to the current Windows identity.
  writeFileSync(tmp,JSON.stringify({pid:process.pid,url:ctx.connection.authenticatedUrl(`${ctx.webServer.protocol}//127.0.0.1:${ctx.webServer.port}/`)+'#creator-console',origin:`${ctx.webServer.protocol}//127.0.0.1:${ctx.webServer.port}`,startedAt:new Date().toISOString()}),{mode:0o600});
  if(process.platform==='win32'){
    try {const who=execFileSync('whoami',[],{encoding:'utf8',windowsHide:true}).trim();execFileSync('icacls',[tmp,'/inheritance:r','/grant:r',`${who}:(F)`],{windowsHide:true,stdio:'ignore'});}catch{unlinkSync(tmp);throw new Error('无法保护工作台启动凭据文件，已停止发布入口');}
  }
  renameSync(tmp,discovery);
  ctx.effect(()=>()=>{try{const current=JSON.parse(readFileSync(discovery,'utf8'));if(current.pid===process.pid)unlinkSync(discovery);}catch{}},'creator-console: remove stale discovery');
}
