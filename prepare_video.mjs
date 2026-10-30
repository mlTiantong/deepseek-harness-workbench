import {MediaWorkbench} from './data/profiles/desktop/node_modules/dsh-remotion/lib/media-workbench.js';
import {writeFile} from 'node:fs/promises';
process.env.DSH_HOME='D:\\DeepSeek Harness\\data';
const w=new MediaWorkbench('remotion');
const task=await w.start('prepare');
let previous='';
while(true){
  const j=w.job(task.id);
  if(j.stage!==previous){console.log(j.stage);previous=j.stage;}
  if(j.state!=='running'){
    await writeFile('video-environment-test.json',JSON.stringify(j,null,2));
    console.log(JSON.stringify(j));
    if(j.state!=='done')process.exitCode=1;
    break;
  }
  await new Promise(r=>setTimeout(r,1000));
}
w.dispose();
