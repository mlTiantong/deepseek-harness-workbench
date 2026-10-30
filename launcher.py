"""Launch a local DeepSeek Harness installation without committing credentials."""
import os
import pathlib
import subprocess
import sys

import yaml


root = pathlib.Path(__file__).resolve().parent
env = os.environ.copy()
home = pathlib.Path(env.get('DSH_HOME', root / 'data')).resolve()
env['DSH_HOME'] = str(home)
env['PATH'] = os.pathsep.join([str(root / '.venv/Scripts'), str(root), env.get('PATH', '')])
host_modules = root / 'app/resources/app.asar/dsh/node_modules/@deepseek-ai'
if host_modules.exists():
    env.setdefault('DSH_HOST_MODULES', str(host_modules))
env.setdefault('DSH_DOUYIN_HOME', str(home / 'douyin-duty'))

credentials_path = home / '.credentials.yaml'
if credentials_path.exists():
    credentials = yaml.safe_load(credentials_path.read_text(encoding='utf-8')) or {}
    env.update({key: str(value) for key, value in credentials.get('refs', {}).items()})

config_path = home / 'cordis.patch.yml'
if config_path.exists():
    config = yaml.safe_load(config_path.read_text(encoding='utf-8')) or []
    provider_block = next((row.get('config', {}).get('providers', {}) for row in config
                           if row.get('id') == 'llm-pi-ai'), {})
    provider_id = env.get('DSH_PROVIDER') or next(iter(provider_block), None)
    primary = provider_block.get(provider_id, {}) if provider_id else {}
    api_key_env = primary.get('apiKeyEnv')
    if api_key_env and env.get(api_key_env):
        env.setdefault('DEEPSEEK_API_KEY', env[api_key_env])
    if primary.get('baseURL'):
        env.setdefault('DEEPSEEK_BASE_URL', primary['baseURL'])
    models = primary.get('models') or []
    if models and models[0].get('id'):
        env.setdefault('DEEPSEEK_DEFAULT_MODEL', models[0]['id'])

env.setdefault('PYTHONUTF8', '1')
model_providers = home / 'xhs-providers.yaml'
if model_providers.exists():
    env.setdefault('MODEL_PROVIDERS_FILE', str(model_providers))

if '--run-python' in sys.argv:
    sys.exit(subprocess.run([str(root / '.venv/Scripts/python.exe'), *sys.argv[sys.argv.index('--run-python') + 1:]], env=env, cwd=root).returncode)

app = pathlib.Path(env.get('DSH_APP_PATH', root / 'app/DeepSeek Harness.exe')).resolve()
if not app.exists():
    raise SystemExit('DeepSeek Harness executable not found. Install the official app or set DSH_APP_PATH.')

if '--cli' in sys.argv:
    env['ELECTRON_RUN_AS_NODE']='1'
    cli=root/'desktop-cli.mjs'
    process=subprocess.Popen([str(app),'--expose-internals',str(cli),*sys.argv[sys.argv.index('--cli')+1:]],env=env,cwd=env.get('DSH_WORKDIR',str(root)),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    try:
        for line in iter(process.stdout.readline,b''):
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        sys.exit(process.wait())
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        sys.exit(130)
subprocess.Popen([str(app)], env=env, cwd=root)
