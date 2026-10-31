"""Live, read-only checks: existing inbound media, visual model and public comments.
No IM connection, message sending, or changes to the user's database/config.
Only aggregate results are written under ignored var/feature-qa.
"""
from __future__ import annotations
import asyncio
import os
import base64
import io
import json
import re
import sqlite3
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from PIL import Image, ImageDraw
from dsh_companion.store import Store
from dsh_companion.dsh_runtime import DshRuntime
from dsh_companion.message_media import cache_image, image_block_from_path, normalize_message_content, visual_blocks_from_path
from dsh_companion.platforms.bilibili import BilibiliAdapter
from dsh_companion.platforms.douyin_web import DouyinWeb
from dsh_companion.platform_service import PlatformService

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'var/feature-qa/live-reading.json'


def source_data():
    with sqlite3.connect(f'file:{(ROOT/"var/companion.sqlite3").as_posix()}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        configs={row['key']:json.loads(row['value']) for row in db.execute('select key,value from config')}
        events=[]
        for row in db.execute('select * from events order by discovered_at desc limit 500'):
            event=dict(row);event['payload']=json.loads(event['payload']);events.append(event)
        return configs,events


def inspect_media(configs,events,folder):
    selected=defaultdict(list)
    for event in events:
        kind=(event['payload'].get('content') or {}).get('kind')
        tag=f"{event['platform']}/{event['event_type']}/{kind}"
        if kind in ('image','emoji') and len(selected[tag])<3:selected[tag].append(event)
    results={};visual=[];counter=0
    for tag,cases in selected.items():
        successes=[];errors=[]
        for i,event in enumerate(cases):
            content=normalize_message_content(event['payload']);counter+=1
            try:
                block=image_block_from_path(content['path']) if content.get('path') else cache_image(content,folder,index=counter)
                if not block:raise RuntimeError('No downloadable media URL')
                with Image.open(block['path']) as image:
                    size=image.size;nframes=getattr(image,'n_frames',1)
                successes.append({'mime':block['mimeType'],'size':size,'frames':nframes})
                if not any(t==tag for t,_ in visual):visual.append((tag,block))
                if nframes>1 and not any(t.endswith(':animated') for t,_ in visual):visual.append((tag+':animated',block))
            except Exception as exc:errors.append(type(exc).__name__)
        results[tag]={'passed':len(successes),'failed':len(errors),'media':successes,'errors':errors}
        print(tag,results[tag],flush=True)
    # Execute the real message->prompt->save pipeline, but intercept the outgoing model call.
    store=Store(folder/'test.sqlite3')
    store.set_config('chat_media',configs['chat_media'])
    runtime=DshRuntime(store,ROOT,data_dir=folder/'runtime')
    pipeline={}
    for tag,block in visual:
        platform,event_type,kind=tag.split('/')
        content={'kind':kind.split(':')[0],'path':block['path']}
        messages=[{'platform':platform,'id':tag.replace('/','-'),'sender_id':'test','payload':{'content':content,'text':'[media]'}}]
        job={'id':tag.replace('/','-').replace(':','-'),'event_id':None,'actor_id':'douyin_chat' if platform=='douyin' else 'weixin_chat',
             'kind':'private_message_batch','payload':{'platform':platform,'messages':messages}}
        with patch.object(runtime,'run_turn',return_value={'ok':True}) as turn:
            runtime.execute_job_sync(job)
            prompt=turn.call_args.args[1]
            images=[item for item in prompt if item.get('type')=='image']
            assert len(images)==(4 if tag.endswith(':animated') else 1), (tag, [x.get('text','')[-200:] for x in prompt if x.get('type')=='text'])
            pipeline[tag]={'inline_image':True,'image_count':len(images),'mime':images[0]['mimeType']}
    return results,pipeline,visual


def vision(configs,visual):
    config=configs['dsh'];key=(ROOT/'var/model-api-key.txt').read_text(encoding='utf-8').strip()
    marker=Image.new('RGB',(420,160),'white');draw=ImageDraw.Draw(marker)
    draw.rectangle((25,25,110,110),fill='red');draw.ellipse((165,25,250,110),fill='blue')
    draw.text((290,55),'FISH 726',fill='black',font_size=24)
    out=io.BytesIO();marker.save(out,format='PNG')
    blocks=[{'type':'text','text':'测试视觉：第1张是校验图，指出两种图形和颜色，并读出数字。之后每张图片各用一句话描述实际可见内容。不要调用工具、不要回复任何人。'}]
    blocks.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()}})
    # Private media was already destined for this configured model; don't use a new provider.
    tags=[]
    for tag,block in visual:
        tags.append(tag)
        blocks.append({'type':'text','text':tag})
        for item in visual_blocks_from_path(block['path']):
            if item['type']=='image':blocks.append({'type':'image_url','image_url':{'url':f"data:{item['mimeType']};base64,{item['data']}"}})
    with httpx.Client(timeout=180) as client:
        response=client.post(config['base_url'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},
          json={'model':config['model'],'messages':[{'role':'user','content':blocks}],'max_tokens':1000})
        response.raise_for_status();data=response.json()
    answer=data['choices'][0]['message'].get('content') or ''
    passed='726' in answer and ('红' in answer or 'red' in answer.lower()) and ('蓝' in answer or 'blue' in answer.lower())
    print('Actual vision model',{'passed':passed,'image_count':sum(b['type']=='image_url' for b in blocks),'response_length':len(answer)},flush=True)
    assert passed,'Visual control image was not recognized'
    return {'passed':passed,'image_count':sum(b['type']=='image_url' for b in blocks),'media_types':tags,'response_length':len(answer)}


async def comments(configs,events,folder):
    store=Store(folder/'comments.sqlite3')
    bili=BilibiliAdapter(store,ROOT,directory=ROOT/'var/platforms/bilibili')
    with httpx.Client(timeout=30) as client:
        popular=client.get('https://api.bilibili.com/x/web-interface/popular',params={'ps':2},headers={'User-Agent':'Mozilla/5.0'}).json()
    bvid=popular['data']['list'][0]['bvid']
    result=await bili.get_content_comments(content_id=bvid,limit=50)
    assert result['count']>=10
    report={'bilibili':{'count':result['count'],'requested':50,'ids_valid':all(c['id'] for c in result['items'])}}
    print('Live Bilibili comments',report['bilibili'],flush=True)
    ids=[]
    for event in events:
        content=event['payload'].get('content') or {}
        if content.get('kind')=='share':
            raw=json.dumps(content)
            matches=re.findall(r'(?:video|note)/([0-9]{15,})',raw)
            item_id=str((content.get('share') or {}).get('itemId') or '')
            if item_id.isdigit():matches.append(item_id)
            for ident in matches:
                if ident not in ids:ids.append(ident)
    if not ids:raise RuntimeError('No recent Douyin public video URL')
    web=DouyinWeb(store,ROOT,directory=folder/'douyin-web')
    try:
        await web.start()
        errors=[]
        for ident in ids[:3]:
            try:
                result=await web.call('companion_read_comments',content_id=ident,limit=30)
                if result['count']<1:continue
                report['douyin']={'count':result['count'],'requested':30,'ids_valid':all(c['id'] for c in result['items'])}
                print('Live Douyin comments',report['douyin'],flush=True);break
            except Exception as exc:errors.append(type(exc).__name__+': '+str(exc)[:160])
        if 'douyin' not in report:raise RuntimeError('Douyin comments failed: '+str(errors))
        note_ids=[]
        for event in events:
            content=event['payload'].get('content') or {}
            value=content.get('value') or {}
            if value.get('image_count') and str((content.get('share') or {}).get('itemId') or '').isdigit():
                ident=str(content['share']['itemId'])
                if ident not in note_ids:note_ids.append(ident)
        service=PlatformService(store,ROOT,SimpleNamespace(notify=lambda:None))
        cfg=store.get_config('media');cfg.update(enabled=True,audio_enabled=False);store.set_config('media',cfg)
        cfg=store.get_config('platforms');cfg['douyin']['enabled']=True;store.set_config('platforms',cfg)
        async def get_comments(**kwargs):return await web.call('companion_read_comments',**kwargs)
        service.adapters['douyin']=SimpleNamespace(fetch_media_frames=web.fetch_media_frames,get_content_comments=get_comments)
        reading=await service.fetch_media_frames('douyin',content_id=note_ids[0],frame_count=2,include_audio=False,comment_limit=30)
        with patch.dict(os.environ,{'COMPANION_DATABASE':str(store.path)}):
            from dsh_companion.mcp_tools import _video_content
            output=_video_content(reading)
        image_count=sum(block.type=='image' for block in output)
        print('Image post result:',image_count,reading['comments']['status'],reading['comments']['count'],reading['comments'].get('error',''),flush=True)
        assert image_count>0 and reading['comments']['status'] in ('ok','empty')
        for block in output:
            if block.type=='image':
                with Image.open(io.BytesIO(base64.b64decode(block.data))) as image:assert min(image.size)>1
        report['douyin_image_post']={'images':image_count,'comments':reading['comments']['count'],
                                    'comments_status':reading['comments']['status'],'mcp_images_valid':True}
        print('Live image post + comments + MCP:',report['douyin_image_post'],flush=True)
    finally:await web.close()
    return report


def main():
    configs,events=source_data();report={}
    with tempfile.TemporaryDirectory(prefix='reading-live-') as folder:
        path=Path(folder)
        media,pipeline,visual=inspect_media(configs,events,path)
        report.update(media=media,pipeline=pipeline)
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        try:report['vision']=vision(configs,visual)
        except Exception as exc:report['vision']={'error':type(exc).__name__+': '+str(exc)[:160]};print('VISION FAILED',report['vision'],flush=True)
        try:report['comments']=asyncio.run(comments(configs,events,path))
        except Exception as exc:report['comments']={'error':type(exc).__name__+': '+str(exc)[:200]};print('COMMENTS FAILED',report['comments'],flush=True)
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved aggregate report:',REPORT,flush=True)
    if any(item['failed'] for item in report['media'].values()) or not report.get('vision',{}).get('passed') or 'error' in report.get('comments',{}):
        raise SystemExit(1)

if __name__=='__main__':main()

