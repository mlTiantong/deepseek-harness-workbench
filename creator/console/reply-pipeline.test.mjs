import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {replyPrompt} from './reply-prompt.js';

// Compile the actual registered execute body with read-only mocks: no platform traffic.
function executeOf(file, name, deps) {
  const source=readFileSync(new URL(file, import.meta.url),'utf8');
  const start=source.indexOf("name: '"+name+"'");
  assert.ok(start>=0);
  const body=source.slice(source.indexOf('async execute() {',start)+17,source.indexOf('\n    },',start));
  return new Function(...Object.keys(deps),'return async function(){'+body+'}')( ...Object.values(deps));
}
const bili='../../data/profiles/desktop/node_modules/dsh-bili-scroll/index.js';
const dy='../../data/profiles/desktop/node_modules/dsh-douyin-life/index.js';
test('shared session prompt requires memory, safe IDs and one ledger reservation',()=>{
  const prompt=replyPrompt(['bilibili','douyin']);
  for(const token of ['creator_context','creator_recall','creator_inbox_record','creator_reply_begin','creator_action_finish','bili_dm_inbox','dy_dm_inbox','uncertain']) assert.ok(prompt.includes(token));
  assert.ok(readFileSync(new URL('./index.js',import.meta.url),'utf8').includes('const text=replyPrompt(selected)'));
});
function biliTool(overrides={}) {
  return executeOf(bili,'bili_dm_inbox',{
    stateDir:'mock',dmCred:()=>({dedeuserid:'10'}),
    call:async name=>{assert.equal(name,'bili_check_credential');return {logged_in:true,uid:'10'};},
    dmList:async()=>[{uid:20,unread:2},{uid:10,unread:1},{uid:30,unread:0}],
    dmFetch:async(_cred,uid,size)=>{assert.equal(uid,20);assert.equal(size,8);return [{key:'1',stableKey:true,fromMe:false,text:'test'},{key:'999',stableKey:false,fromMe:false}];},
    ...overrides});
}
test('Bilibili read-only inbox excludes self/read threads and namespaces message IDs',async()=>{
  const result=await biliTool()();
  assert.equal(result.readOnly,true);assert.equal(result.account_id,'10');assert.equal(result.items.length,1);
  assert.equal(result.items[0].messages[0].message_id,'20:1');assert.equal(result.items[0].messages[1].message_id,null);
});
test('expired login or account mismatch cannot read inbox',async()=>{
  for(const verified of [{logged_in:false},{logged_in:true,uid:'11'},{}]) {
    const result=await biliTool({call:async()=>verified,dmList:async()=>{throw Error('must not read');}})();
    assert.equal(result.needLogin,true);
  }
});
test('missing plugin account cannot read inbox',async()=>assert.equal((await biliTool({dmCred:()=>null})()).needLogin,true));
test('Douyin returns preview only, never invokes send',async()=>{
  const execute=executeOf(dy,'dy_dm_inbox',{call:async(name,args)=>{assert.equal(name,'douyin_inbox');assert.equal(args.limit,12);return {ok:true,items:[{text:'preview'}]};}});
  const result=await execute();assert.equal(result.readOnly,true);assert.equal(result.requiresThreadObservation,true);
});
test('Bilibili fast reply watcher uses five-second polling without a reply cap',()=>{
  const config=JSON.parse(readFileSync(new URL('../../data/bili-duty/config.json',import.meta.url),'utf8'));
  assert.equal(config.dmWatch,true);assert.equal(config.dmWatchSeconds,5);assert.equal(config.dmReplyPerDay,0);assert.ok(config._set.includes('dmWatch'));
});

test('fast watcher dispatches non-owner contacts but not self or read threads', async()=>{
  const source=readFileSync(new URL(bili,import.meta.url),'utf8');
  const a=source.indexOf('  async function dmTick() {');
  const b=source.indexOf('  function startDmWatch()',a);
  const sent=[];
  const deps={refreshCfg:()=>{},cfg:{dmWatch:true},stateDir:'mock',creatorDir:'creator',join:(...s)=>s.join('/'),
    readFileSync:()=>JSON.stringify({pid:process.pid,ts:Date.now()}),writeFileSync:()=>{},existsSync:()=>false,
    readJsonFile:(_f,fallback)=>fallback,writeJsonFile:()=>{},dmCred:()=>({dedeuserid:10}),
    dmList:async()=>[{uid:20,unread:1},{uid:30,unread:2},{uid:10,unread:1},{uid:40,unread:0}],
    reserveConsoleDmQuota:async()=>({allowed:true,finish:async()=>{}}),dmFetch:async()=>[{key:'new',fromMe:false,text:'hello'}],runDmReply:(uid)=>{sent.push(uid);return {ok:true};},log:()=>{},dmSeenFile:'seen'};
  const tick=new Function(...Object.keys(deps),'let dmBusy=false;'+source.slice(a,b)+';return dmTick;')(...Object.values(deps));
  await tick();assert.deepEqual(sent,[20,30]);
});
