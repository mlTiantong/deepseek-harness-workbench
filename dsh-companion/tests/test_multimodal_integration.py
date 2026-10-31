"""Real DSH process: assert that the HTTP provider receives inline image inputs."""
import base64
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from PIL import Image
from dsh_companion.store import Store
from dsh_companion.dsh_runtime import DshRuntime
from dsh_companion.message_media import visual_blocks_from_path

class VisualProvider(BaseHTTPRequestHandler):
    requests=[]
    def log_message(self,*_args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(body)
        chunks=[{'id':'test','object':'chat.completion.chunk','created':1,'model':'mock',
            'choices':[{'index':0,'delta':{'role':'assistant','content':'看到了图片。'},'finish_reason':None}]},
            {'id':'test','object':'chat.completion.chunk','created':1,'model':'mock',
             'choices':[{'index':0,'delta':{},'finish_reason':'stop'}],
             'usage':{'prompt_tokens':30,'completion_tokens':10,'total_tokens':40}}]
        data=(''.join('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n').encode()
        self.send_response(200);self.send_header('Content-Type','text/event-stream')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

class MultimodalIntegrationTest(unittest.TestCase):
    def test_private_sticker_reaches_provider_as_sampled_image_frames(self):
        VisualProvider.requests=[]
        server=ThreadingHTTPServer(('127.0.0.1',0),VisualProvider)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'sticker.gif'
            frames=[Image.new('RGB',(30,30),color) for color in ['red','green','blue','purple']]
            frames[0].save(path,save_all=True,append_images=frames[1:],duration=100,loop=0)
            store=Store(root/'db.sqlite')
            cfg=store.get_config('platforms');cfg['douyin'].update(enabled=True,messages=True);store.set_config('platforms',cfg)
            event,job,_=store.ingest_event('douyin','message',{'text':'[表情]',
                'content':{'kind':'emoji','path':str(path)}},sender_id='test',account_id='self',conversation_id='test')
            runtime=DshRuntime(store,Path.cwd(),data_dir=root/'runtime')
            runtime.configure_model(model='mock',base_url=f'http://127.0.0.1:{server.server_port}/v1',
                api_key='test',reasoning_effort='off',compatibility='standard',max_tokens=256)
            try:
                runtime.start()
                result=runtime.execute_job_sync(job)
                self.assertEqual(result['finish_reason'],'completed')
                self.assertGreater(len(VisualProvider.requests),0)
                images=[part for message in VisualProvider.requests[-1]['messages']
                    for part in (message['content'] if isinstance(message.get('content'),list) else [])
                    if part.get('type')=='image_url']
                self.assertEqual(len(images),4)
                for image in images:
                    uri=image['image_url']['url'];self.assertTrue(uri.startswith('data:image/png;base64,'))
                    with Image.open(io.BytesIO(base64.b64decode(uri.split(',',1)[1]))) as decoded:
                        self.assertEqual(decoded.size,(30,30))
                self.assertEqual(len(store.list_media()),1)
                self.assertEqual(store.list_media()[0]['mime_type'],'image/gif')
            finally:runtime.close()

if __name__=='__main__':unittest.main()
