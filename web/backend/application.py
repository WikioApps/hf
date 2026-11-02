from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from bot.core.settings import BOT_DEFAULTS, validate

FRONTEND = Path(__file__).resolve().parents[1]/'frontend'


def create_app(runtime):
    @asynccontextmanager
    async def lifespan(app):
        try:
            runtime.launch()
            yield
        finally:
            await runtime.close()

    async def index(request):
        return FileResponse(FRONTEND/'index.html',headers={'Cache-Control':'no-store'})

    async def health(request):
        healthy = runtime.healthy()
        return JSONResponse({'status':'ready' if healthy else runtime.state},status_code=200 if healthy else 503)

    async def identity(request):
        record = await runtime.db.session(request.cookies.get('hf_session',''))
        if not record or not await runtime.db.allowed(record['uid']):
            return None
        return record['uid']

    def same_origin(request):
        # Mutations are only accepted from the configured HTTPS portal.
        origin = request.headers.get('origin','')
        return bool(runtime.settings.get('base_url') and origin == runtime.settings['base_url'])

    async def session(request):
        if not same_origin(request): return JSONResponse({'error':'Invalid origin.'},403)
        if request.method == 'DELETE':
            raw = request.cookies.get('hf_session','')
            await runtime.db.session(raw,consume=True)
            response = JSONResponse({'ok':True}); response.delete_cookie('hf_session',path='/')
            return response
        data = await body(request)
        link = await runtime.db.session(str(data.get('token','')),'link',consume=True)
        if not link or not await runtime.db.allowed(link['uid']):
            return JSONResponse({'error':'Sign-in link expired. Get a new link with /web.'},401)
        token = await runtime.db.issue_session(link['uid'],'web',1440)
        response = JSONResponse({'ok':True})
        response.set_cookie('hf_session',token,secure=True,httponly=True,samesite='strict',max_age=86400,path='/')
        return response

    async def public(request):
        result = {'ready':runtime.healthy(),'status':runtime.state,'bot':runtime.username,'public_access':runtime.settings.get('public_mode',False)}
        if runtime.settings.get('public_status'):
            result['active'] = len(runtime.manager.active())
            result['downloads'] = runtime.manager.downloads.running
            result['uploads'] = runtime.manager.uploads.running
        return JSONResponse(result)

    async def portal(request):
        uid = await identity(request)
        if uid is None: return JSONResponse({'error':'Sign in from Telegram with /web.'},401)
        owner = uid == runtime.config.owner_id
        profile = await runtime.db.user(uid)
        active = runtime.manager.active(None if owner else uid)
        records = await runtime.db.tasks(None if owner else uid,30)
        result = {'user':{'id':uid,'name':profile.get('name','User'),'owner':owner},
                  'destination':{k:profile[k] for k in ('namespace','repo_name','repo_type','branch','path')},
                  'token_set':bool(profile['token']),
                  'active':[task_data(task) for task in active],
                  'history':[{'id':t['_id'],'name':t['name'],'phase':t['phase'],'created':t['created'],
                              'links':[r['url'] for r in t.get('results',[])[:5]],'error':t.get('error','')} for t in records]}
        if owner:
            result['settings'] = runtime.settings
            users = await runtime.db.users(int(request.query_params.get('offset','0')))
            result['users'] = [{k:u.get(k) for k in ('user_id','name','authorized','blocked')} for u in users]
        return JSONResponse(result)

    async def action(request):
        uid = await identity(request)
        if uid is None: return JSONResponse({'error':'Please sign in again.'},401)
        if not same_origin(request): return JSONResponse({'error':'Invalid origin.'},403)
        data = await body(request)
        command = data.get('action')
        if command in ('cancel','retry','clean'):
            tid = str(data.get('id',''))
            import re
            if not re.fullmatch('[a-f0-9]{12}',tid): raise ValueError('Invalid task ID.')
            await getattr(runtime.manager,command)(tid,uid)
        elif uid == runtime.config.owner_id and command == 'setting':
            key = data.get('key')
            if key not in BOT_DEFAULTS: raise ValueError('Unknown setting.')
            await runtime.db.update_bot(key,validate(key,data.get('value')))
            await runtime.reload_settings()
        elif uid == runtime.config.owner_id and command in ('allow','block','reset'):
            target = int(data['user'])
            if target == runtime.config.owner_id: raise ValueError('Owner access cannot be changed.')
            await runtime.db.user(target)
            await runtime.db.update_user(target,'authorized',command == 'allow')
            await runtime.db.update_user(target,'blocked',command == 'block')
        else: return JSONResponse({'error':'Action not allowed.'},403)
        return JSONResponse({'ok':True})

    async def bad_request(request,error):
        return JSONResponse({'error':str(error)[:200]},400)

    app = Starlette(lifespan=lifespan,routes=[Route('/',index),Route('/healthz',health),
        Route('/api/public',public),Route('/api/session',session,methods=['POST','DELETE']),
        Route('/api/me',portal),Route('/api/action',action,methods=['POST']),
        Mount('/assets',StaticFiles(directory=FRONTEND),name='assets')],exception_handlers={ValueError:bad_request})

    @app.middleware('http')
    async def security_headers(request,call_next):
        if request.url.path.startswith('/api/') and request.url.path != '/api/public' and not runtime.healthy():
            response = JSONResponse({'error':'Bot is not ready yet. Please wait.','status':runtime.state},503)
        else:
            response = await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'X-Frame-Options':'DENY','Cache-Control':'no-store',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response
    return app


async def body(request):
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 16384: raise ValueError('Request is too large.')
    data = json.loads(chunks or b'{}')
    if not isinstance(data,dict): raise ValueError('Expected a JSON object.')
    return data


def task_data(task):
    return {'id':task.id,'name':task.name,'phase':task.phase,'done':task.done,'total':task.total,
            'speed':task.speed if task.phase in ('Download','Upload','Extract') else 0,
            'eta':task.eta,'created':task.created,'user':task.user_id}
