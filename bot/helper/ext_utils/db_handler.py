"""MongoDB is authoritative for settings, task records and web sessions."""
import base64
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from cryptography.fernet import Fernet
from pymongo import AsyncMongoClient, ReturnDocument
from bot.core.settings import USER_DEFAULTS, BOT_DEFAULTS


def utcnow():
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, config):
        self.config = config
        key = hashlib.sha256((config.api_hash + ':' + config.bot_id).encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))
        self.client = AsyncMongoClient(config.database_url, serverSelectionTimeoutMS=10000)
        self.db = self.client.get_default_database(default='hf_mirror')
        self.scope = config.bot_id
        self.manifests_written = set()

    def seal(self, value):
        return self.cipher.encrypt(value.encode()).decode() if value else ''

    def open(self, value):
        return self.cipher.decrypt(value.encode()).decode() if value else ''

    async def connect(self):
        await self.client.admin.command('ping')
        await self.db.sessions.create_index('expires', expireAfterSeconds=0)
        await self.db.uploads.create_index([('bot',1),('task',1),('index',1)])
        await self.db.manifests.create_index([('bot',1),('task',1),('offset',1)])
        await self.db.tasks.create_index([('bot', 1), ('user_id', 1), ('created', -1)])
        await self.db.users.create_index([('bot', 1), ('user_id', 1)], unique=True)
        await self.db.settings.update_one({'_id': self.scope}, {'$setOnInsert': BOT_DEFAULTS}, upsert=True)

    async def bot_settings(self):
        data = await self.db.settings.find_one({'_id': self.scope}) or {}
        return {**BOT_DEFAULTS, **{k:v for k,v in data.items() if k != '_id'}}

    async def update_bot(self, key, value):
        await self.db.settings.update_one({'_id': self.scope}, {'$set': {key:value}})

    async def user(self, uid, name=None):
        key = f'{self.scope}:{uid}'
        await self.db.users.update_one({'_id':key}, {'$setOnInsert':{'bot':self.scope, 'user_id':uid, **USER_DEFAULTS}}, upsert=True)
        if name is not None:
            await self.db.users.update_one({'_id':key}, {'$set':{'name':name[:100], 'seen':utcnow()}})
        data = await self.db.users.find_one({'_id':key})
        data = {**USER_DEFAULTS, **data}
        data['token'] = self.open(data['token'])
        return data

    async def update_user(self, uid, key, value):
        if key == 'token':
            value = self.seal(value)
        await self.db.users.update_one({'_id':f'{self.scope}:{uid}'}, {'$set':{key:value}})

    async def allowed(self, uid):
        if uid == self.config.owner_id:
            return True
        user = await self.user(uid)
        settings = await self.bot_settings()
        return not user['blocked'] and (user['authorized'] or settings['public_mode'])

    async def users(self, offset=0):
        return await self.db.users.find({'bot':self.scope}, {'token':0}).sort('user_id',1).skip(offset).limit(8).to_list()

    async def save_task(self, data):
        data = {**data, 'bot':self.scope, 'updated':utcnow()}
        members = data.pop('members',[])
        results = data.pop('results',[])
        tid = data['_id']
        if members and tid not in self.manifests_written:
            for offset in range(0,len(members),100):
                record = {'_id':f'{self.scope}:{tid}:{offset}', 'bot':self.scope,
                          'task':tid,'offset':offset,'members':members[offset:offset+100]}
                await self.db.manifests.replace_one({'_id':record['_id']},record,upsert=True)
            self.manifests_written.add(tid)
        if results:
            item = {**results[-1], '_id':f'{self.scope}:{tid}:{len(results)-1}',
                    'bot':self.scope,'task':tid,'index':len(results)-1}
            await self.db.uploads.replace_one({'_id':item['_id']},item,upsert=True)
        data['result_count'],data['member_count'] = len(results),len(members)
        await self.db.tasks.replace_one({'_id':tid},data,upsert=True)

    async def results(self, tid, limit=10000):
        return await self.db.uploads.find({'bot':self.scope,'task':tid},
                {'_id':0,'bot':0,'task':0,'index':0}).sort('index',1).limit(limit).to_list()

    async def tasks(self, uid=None, limit=50):
        query = {'bot':self.scope}
        if uid is not None:
            query['user_id'] = uid
        records = await self.db.tasks.find(query).sort('created',-1).limit(limit).to_list()
        for record in records:
            record['results'] = await self.results(record['_id'],5)
        return records

    async def task(self, tid):
        data = await self.db.tasks.find_one({'_id':tid,'bot':self.scope})
        if not data: return None
        data['members'] = []
        async for record in self.db.manifests.find({'bot':self.scope,'task':tid}).sort('offset',1):
            data['members'].extend(record['members'])
        data['results'] = await self.results(tid)
        return data

    async def issue_session(self, uid, kind='link', minutes=5):
        raw = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        await self.db.sessions.insert_one({'_id':digest, 'bot':self.scope, 'uid':uid, 'kind':kind,
                                          'expires':utcnow()+timedelta(minutes=minutes)})
        return raw

    async def session(self, raw, kind='web', consume=False):
        digest = hashlib.sha256(raw.encode()).hexdigest()
        query = {'_id':digest, 'bot':self.scope, 'kind':kind, 'expires':{'$gt':utcnow()}}
        method = self.db.sessions.find_one_and_delete if consume else self.db.sessions.find_one
        return await method(query)

    async def close(self):
        await self.client.close()

    async def lease(self, instance):
        from pymongo.errors import DuplicateKeyError
        query = {'_id':self.scope, '$or':[{'instance':instance},{'expires':{'$lt':utcnow()}}]}
        try:
            record = await self.db.leases.find_one_and_update(query, {'$set':{
                'instance':instance, 'expires':utcnow()+timedelta(seconds=60)}},
                upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            raise ValueError('Another instance of this bot is running. Stop it, then wait 60 seconds.') from None
        return bool(record)

    async def release(self, instance):
        await self.db.leases.delete_one({'_id':self.scope,'instance':instance})
