"""Native Tk + real local HTTP API verification, with isolated jobs and no model calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import os
import socket
import threading
import time
import tkinter as tk
import uvicorn
from urllib.error import URLError
from PIL import ImageGrab, ImageChops
from dsh_companion.app import Companion, create_app
from dsh_companion.desktop_pet.window import HarnessPet

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'var/feature-qa';OUTPUT.mkdir(parents=True,exist_ok=True)
async def idle(self): pass

with TemporaryDirectory(prefix='mini-whale-qa-') as folder, \
     patch.object(Companion,'start',idle), patch.object(Companion,'stop',idle):
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=create_app(ROOT,data_dir=folder)
    companion=app.state.companion
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    while not server.started:time.sleep(.02)
    with patch.dict(os.environ,{'COMPANION_API_URL':f'http://127.0.0.1:{port}',
                               'COMPANION_PET_STATE':str(Path(folder)/'position.json')}):
        pet=HarnessPet()
        pet.root.geometry('+650+150')
        backdrop=tk.Tk();backdrop.overrideredirect(True)
        backdrop.configure(bg='#e8f2ff');backdrop.geometry('800x600+200+100')
        backdrop.attributes('-topmost',True);backdrop.update();backdrop.lower(pet.root);pet.root.lift()
        def pump_until(predicate,timeout=5):
            deadline=time.monotonic()+timeout
            def check():
                if predicate() or time.monotonic()>deadline:pet.root.quit()
                else:pet.root.after(20,check)
            pet.root.after(20,check);pet.root.mainloop()
            assert predicate(), (pet.status,pet.current_animation)
        def pump_for(seconds):
            pet.root.after(round(seconds*1000),pet.root.quit);pet.root.mainloop()
        def capture():
            pet.root.update_idletasks()
            x,y=pet.root.winfo_x(),pet.root.winfo_y()
            return ImageGrab.grab(bbox=(x,y,x+pet.root.winfo_width(),y+pet.root.winfo_height()))
        try:
            pump_until(lambda:pet.status.get('action')=='idle')
            # Real Tk controls -> real HTTP -> isolated main queue, never live platforms.
            pet._show_task_panel(); pump_for(.15)
            companion.scheduler.pause()
            companion.store.set_config('autonomy', {**companion.store.get_config('autonomy'), 'enabled':False})
            for action, animation in [('wake','think'), ('watch','watch'), ('socialize','reply')]:
                previous = (pet.status.get('command') or {}).get('id')
                pet.panel_buttons[action].invoke()
                pump_until(lambda: not pet.is_assigning and pet.status.get('command') and
                           pet.status['command']['id'] != previous)
                command = pet.status['command']; job = companion.store.get_job(command['id'])
                assert job['actor_id']=='main' and not job['autonomous'] and job['status']=='ready'
                assert '已排队' in pet.panel_labels['feedback'].cget('text')
                companion.store.start_job(job['id'])
                pump_until(lambda:pet.current_animation==animation)
                assert '在做' in pet.panel_labels['feedback'].cget('text')
                if action=='watch':
                    pump_for(.2) # allow the Windows compositor to present the latest Tk frame
                    x=pet.task_panel.winfo_x();y=min(pet.task_panel.winfo_y(),pet.root.winfo_y())
                    right=pet.root.winfo_x()+pet.root.winfo_width()
                    bottom=max(pet.root.winfo_y()+pet.root.winfo_height(),pet.task_panel.winfo_y()+pet.panel_height)
                    ImageGrab.grab(bbox=(x-8,y-8,right+8,bottom+8)).save(OUTPUT/'desktop-pet-commands.png')
                companion.store.finish_job(job['id'],result={'response':'看到了猫猫，评论也读啦'})
                pump_until(lambda:pet.status['command']['status']=='succeeded')
                assert '猫猫' in pet.panel_labels['feedback'].cget('text')
            assert companion.store.get_config('paused')
            assert not companion.store.get_config('autonomy')['enabled']
            pet.panel_entry.insert(0,'去看看猫猫视频')
            pump_for(1.6)
            assert pet.panel_entry.get()=='去看看猫猫视频' # refresh never rebuilds the Entry
            pet.task_panel.focus_force();pet.panel_entry.focus_set();pump_for(.1)
            pet.panel_entry.event_generate('<Return>')
            pump_until(lambda:not pet.is_assigning and pet.panel_entry.get()=='')
            pump_until(lambda:pet.status['command']['title']=='执行你的安排')
            custom=companion.store.get_job(pet.status['command']['id'])
            assert custom['payload']['text']=='去看看猫猫视频'
            companion.store.start_job(custom['id']);companion.store.finish_job(custom['id'],error='测试平台未登录')
            pump_until(lambda:pet.status['command']['status']=='failed')
            assert '未完成' in pet.panel_labels['feedback'].cget('text')
            pet.panel_entry.insert(0,'保留这句话')
            with patch('dsh_companion.desktop_pet.window.assign_task',side_effect=URLError('offline')):
                pet.panel_send.invoke();pump_until(lambda:not pet.is_assigning)
            assert pet.panel_entry.get()=='保留这句话'
            assert pet.panel_labels['feedback'].cget('text')=='工作台没连接'
            assert str(pet.panel_send.cget('state'))=='normal'
            pet.panel_entry.delete(0,'end');pet.command_feedback=''
            pet.task_panel.event_generate('<Escape>');pump_for(.1)
            assert not pet._task_panel_is_visible()
            companion.scheduler.resume()
            companion.store.set_config('autonomy',{**companion.store.get_config('autonomy'),'direction':'做一份夜景拍摄指南'})
            job=companion.store.create_job('main','manual',{'message':'聊聊夜景'})
            companion.store.start_job(job['id'])
            callback=companion.runtime._usage_recorder('main','session','',job['id'])
            frames=[]
            for tool,action in [(None,'reply'),('watch_video','watch'),('get_content_comments','read'),
                                ('write','write'),('generate_image','draw'),('shell','code')]:
                if tool:
                    callback(SimpleNamespace(method='session.event',payload={'event':{'type':'tool/call',
                        'data':{'name':'mcp__companion__'+tool,'callId':tool}}}))
                pump_until(lambda:pet.current_animation==action)
                first_index=pet.player.frame_index;before=capture()
                pump_for(.4)
                assert pet.player.frame_index!=first_index
                # Capture moving pixels, not merely an unchanged pose/name.
                after=capture();assert ImageChops.difference(before,after).getbbox()
                for i in range(14):frames.append(capture());pump_for(.08)
                if action=='read':
                    pet._show_task_panel();pump_for(.1)
                    assert pet.panel_labels['status'].cget('text')=='大肥鱼在读评论'
                    assert pet.task_panel.winfo_width()==248
                    assert pet.task_panel.winfo_height()<=258
                    assert abs(pet.task_panel.attributes('-alpha')-.82)<.01
                    assert str(pet.task_panel.attributes('-transparentcolor'))=='#00ff00'
                    # A genuine transparent card changes pixels with its backdrop.
                    px=pet.task_panel.winfo_x()+6;py=pet.task_panel.winfo_y()+75
                    light=ImageGrab.grab(bbox=(px,py,px+1,py+1)).getpixel((0,0))
                    backdrop.configure(bg='#334b60');backdrop.update();pump_for(.15)
                    dark=ImageGrab.grab(bbox=(px,py,px+1,py+1)).getpixel((0,0))
                    assert sum(abs(a-b) for a,b in zip(light,dark))>30, (light,dark)
                    backdrop.configure(bg='#e8f2ff');backdrop.update();pump_for(.15)
                    x=pet.task_panel.winfo_x();y=min(pet.task_panel.winfo_y(),pet.root.winfo_y())
                    right=pet.root.winfo_x()+pet.root.winfo_width()
                    bottom=max(pet.root.winfo_y()+pet.root.winfo_height(),y+pet.task_panel.winfo_height())
                    ImageGrab.grab(bbox=(x,y,right,bottom)).save(OUTPUT/'desktop-pet.png')
                    pet._hide_task_panel()
                if tool:callback(SimpleNamespace(method='session.event',payload={'event':{'type':'tool/result','data':{'callId':tool}}}))
            companion.store.finish_job(job['id'],result={'response':'好了'})
            pump_until(lambda:pet.current_animation=='celebrate')
            for i in range(14):frames.append(capture());pump_for(.08)
            companion.store.set_config('paused',True)
            pump_until(lambda:pet.current_animation=='sleep')
            for i in range(14):frames.append(capture());pump_for(.08)
            pet._set_scale(.7);pet._save_position()
            position=Path(folder)/'position.json';assert position.exists()
            assert pet.current_image.width()==252
            pet.status={'state':'idle','action':'idle','title':'大肥鱼自由待命',
                        'todos':[{'content':'这是一条特别特别长的任务标题，需要简短显示而不是撑大面板','status':'pending'}]*6}
            pet._show_task_panel();pet.root.update()
            assert pet.task_panel.winfo_height()==258
            assert len(pet.panel_labels['tasks'].cget('text').splitlines())==4
            assert '另有 3 项' in pet.panel_labels['tasks'].cget('text')
            assert '…' in pet.panel_labels['tasks'].cget('text')
            pet.panel_close.invoke();assert not pet._task_panel_is_visible()
            pet._play_idle('cube')
            pump_until(lambda:pet.current_animation=='cube')
            pet.reaction_until=time.monotonic()+pet.player.duration('wave')
            pump_until(lambda:pet.current_animation=='wave')
            pet.was_dragged=True;pump_until(lambda:pet.current_animation=='drag');pet.was_dragged=False
            frames[0].save(OUTPUT/'desktop-pet-actions.gif',save_all=True,append_images=frames[1:],duration=80,loop=0)
            print('Native pet controls + real API passed: preset queue, Enter submit, persistent input, completed/failed results, offline feedback, paused one-offs; replying, watching, reading, writing, drawing, coding, completion, pause, frame motion, compact light panel, real transparency, queue truncation, scaling, idle play, click and drag.')
        finally:
            # Finish outstanding HTTP delivery before destroying its Tk receiver.
            pump_until(lambda:not pet.is_polling)
            backdrop.destroy();pet._quit()
            server.should_exit=True;thread.join(timeout=5)
