import {MediaWorkbench} from './data/profiles/desktop/node_modules/dsh-remotion/lib/media-workbench.js';
import {writeFile,copyFile} from 'node:fs/promises';
process.env.DSH_HOME='D:\\DeepSeek Harness\\data';
const w=new MediaWorkbench('remotion');
const p=await w.store.create({template:'title',title:'创作环境已就绪',body:'B站 · 微信 · 小红书',format:'landscape',duration:3});
const task=await w.start('render',p.id,p.revision);
while(true){const j=w.job(task.id);if(j.state!=='running'){await writeFile('video-render-test.json',JSON.stringify(j,null,2));console.log(JSON.stringify(j));if(j.state==='done')await copyFile(`${await w.store.path(p.id)}/runs/${j.id}/final.mp4`,'创作环境测试.mp4');else process.exitCode=1;break;}await new Promise(r=>setTimeout(r,1000));}
w.dispose();
