#!/usr/bin/env python3
"""Dedicated host service. Only fixed StarBridge services may be replaced.

The web app can publish an operation request, never a shell command. All builds,
journals and backups are root-owned and outside the request directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit

ROOT=Path('/opt/1panel/apps/starbridge')
STATE=Path('/var/lib/starbridge-updater')
BACKUPS=Path('/opt/1panel/apps/codex-release-backups/starbridge')
PUBLIC=ROOT/'.maintenance'
REPO='https://github.com/yigerende/StarBridge.git'
SERVICES=['app','registration-worker']
BUSY={'checking','fetching','building','waiting','backing_up','switching','verifying','rolling_back'}
BLOCKING={'waiting','backing_up','switching','verifying','rolling_back'}


class UpdateError(Exception):pass


def write_json(path,value,mode=0o600):
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w') as stream:
        json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
    temp.chmod(mode);os.replace(temp,path)


def read_json(path,default=None):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return default


def origin(value):
    if not isinstance(value,str) or len(value)>300 or any(c.isspace() for c in value) or '\\' in value:
        raise UpdateError('访问地址格式无效')
    try:
        u=urlsplit(value)
        if u.scheme not in ('http','https') or not u.hostname or u.username is not None or u.password is not None or u.path not in ('','/') or u.query or u.fragment or not re.fullmatch(r'[A-Za-z0-9.:[\]-]+',u.hostname):raise ValueError()
        port=u.port
        if port is not None and not 1<=port<=65535:raise ValueError()
        return value.rstrip('/')
    except ValueError:raise UpdateError('访问地址只能包含协议、域名和可选端口') from None


def validate_request(value):
    if not isinstance(value,dict) or not re.fullmatch('[0-9a-f]{32}',str(value.get('id',''))):raise UpdateError('请求编号无效')
    if value.get('action') not in ('check','prepare','restart','rollback'):raise UpdateError('操作无效')
    if type(value.get('created_at')) not in (int,float) or abs(time.time()-value['created_at'])>300:raise UpdateError('请求已过期，请重新操作')
    if value['action']=='prepare':value['public_origin']=origin(value.get('public_origin'))
    for field in ('prepared_id','version_id'):
        if field in value and not re.fullmatch('[0-9a-f]{32}',str(value[field])):raise UpdateError('版本编号无效')
    return value


class Updater:
    def __init__(self):
        self.status=read_json(PUBLIC/'status.json',{})
        self.private=read_json(STATE/'state.json',{'releases':[]})
        self.log=None

    def publish(self,phase=None,**values):
        if phase:self.status['phase']=phase
        self.status.update(values);self.status['updated_at']=time.time()
        self.status['rollback_versions']=[{'id':r['id'],'version':r['version'],'compatible':r.get('epoch')==1} for r in self.private.get('releases',[])][-3:][::-1]
        write_json(PUBLIC/'status.json',self.status,0o644)

    def save(self):write_json(STATE/'state.json',self.private)

    def run(self,args,cwd=ROOT,timeout=120,input=None,capture=True):
        result=subprocess.run(args,cwd=cwd,input=input,stdout=subprocess.PIPE if capture else self.log,
                              stderr=subprocess.PIPE if capture else subprocess.STDOUT,timeout=timeout)
        if result.returncode:
            if self.log and capture:
                self.log.write(result.stdout or b'');self.log.write(result.stderr or b'');self.log.flush()
            raise UpdateError('维护命令未完成，请查看更新服务日志')
        return result.stdout.decode().strip() if capture else ''

    def compose(self,source,extra=None):
        args=['docker','compose','-p','starbridge','--project-directory',str(ROOT),'--env-file',str(ROOT/'.env'),
              '-f',str(source/'compose.yml'),'-f',str(source/'compose.registration.yml')]
        if extra:args+=['-f',str(extra)]
        return args

    def current(self):
        return self.run(['docker','inspect','--format','{{ index .Config.Labels "org.opencontainers.image.revision" }}','starbridge-app'])

    def fetch(self):
        remote=self.run(['git','remote','get-url','origin'])
        if remote not in (REPO,REPO.removesuffix('.git')):raise UpdateError('项目远程仓库不匹配')
        self.run(['git','fetch','--prune','origin','main'],timeout=180)
        sha=self.run(['git','rev-parse','origin/main'])
        if not re.fullmatch('[0-9a-f]{40}',sha):raise UpdateError('版本校验失败')
        self.publish(latest_version=sha,current_version=self.current())
        return sha

    def prepare(self,job):
        self.publish('fetching',message='正在从 GitHub 获取版本',error='')
        sha=self.fetch();directory=STATE/'builds'/job['id'];directory.mkdir(parents=True)
        archive=directory/'source.tar'
        self.run(['git','archive','--format=tar','-o',str(archive),sha])
        source=directory/'source';source.mkdir()
        with tarfile.open(archive) as tar:
            for entry in tar.getmembers():
                if not (entry.isfile() or entry.isdir()) or not (source/entry.name).resolve().is_relative_to(source):raise UpdateError('源码归档包含不允许的路径')
            tar.extractall(source)
        archive.unlink()
        if read_json(source/'deploy/update-policy.json')!={'schema_epoch':1,'project':'starbridge','services':SERVICES}:raise UpdateError('此版本需要人工升级，不能自动切换')
        self.publish('building',message='正在准备新版本，网站继续运行',target_version=sha)
        for name,context in [('starbridge-app',source),('starbridge-registration',source/'services/gpt-register')]:
            self.run(['docker','build','--build-arg','VCS_REF='+sha,'-t',name+':'+sha,str(context)],timeout=2400,capture=False)
        images={service:self.run(['docker','image','inspect','--format','{{.Id}}',name+':'+sha]) for service,name in zip(SERVICES,['starbridge-app','starbridge-registration'])}
        self.private['prepared']={'id':job['id'],'version':sha,'source':str(source),'origin':job['public_origin'],'images':images,'base':self.current(),'epoch':1}
        self.save();self.publish('ready',message='更新已准备完成，点击立即重启生效',prepared_id=job['id'],prepared_origin=job['public_origin'])

    def sql(self,query):
        return self.run(['docker','exec','-i','starbridge-postgres','sh','-c','exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1'],input=query.encode())

    def engine_idle(self):
        # Credentials remain inside the existing container. No token is printed.
        script="""const s=require('./.runtime/platform-secrets.json');
        async function get(p){const r=await fetch('http://127.0.0.1:3211/api/v1/'+p,{headers:{authorization:'Bearer '+s.internal},signal:AbortSignal.timeout(7000)});if(!r.ok)throw Error('health');return r.json()}
        (async()=>{const h=await get('health'),r=await get('robots?compact=1'),b=await get('rebind/status'),m=await get('maintenance-status');
        const busy=Boolean(m.pendingWrites||m.operations||m.chainsBusy||m.linkopsStarting||h.activeRunIds?.length||Object.values(h.taskCapacity||{}).some(x=>x.running||x.starting)||b.running||b.starting||r.robots?.some(x=>(x.enabled&&x.id!=='proxy-refresh')||x.pendingRun||['running','stopping','waiting'].includes(x.status)));
        console.log(busy?'busy':'idle')})().catch(()=>{process.exitCode=1});"""
        return self.run(['docker','exec','starbridge-registration-worker','node','-e',script],timeout=35)=='idle'

    def wait_idle(self):
        self.publish('waiting',message='正在等待在途任务结束；业务自动任务需处于暂停状态')
        query="""SELECT (SELECT count(*) FROM orders WHERE deleted_at IS NULL AND status IN ('queued','processing','review'))+
         (SELECT count(*) FROM renewal_tasks WHERE status IN ('queued','processing','checking'))+
         (SELECT count(*) FROM platform_items WHERE status IN ('queued','running','oauth_pending','review'))+
         (SELECT count(*) FROM proxy_checks WHERE state IN ('queued','running'))+
         (SELECT count(*) FROM proxy_leases WHERE expires_at>now())+
         (SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND classid IN (1886011,1886012));"""
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            if self.sql(query)=='0' and self.engine_idle():return
            time.sleep(3)
        raise UpdateError('在途或自动任务尚未空闲，本次未切换；任务结束或暂停自动任务后可重试')

    def backup(self,job):
        self.publish('backing_up',message='正在保存数据和配置')
        target=BACKUPS/('update-'+time.strftime('%Y%m%d-%H%M%S')+'-'+job['id'][:6]);target.mkdir(parents=True,mode=0o700)
        shutil.copy2(ROOT/'.env',target/'.env')
        active=Path(self.private.get('active_source',str(ROOT)))
        for name in ('compose.yml','compose.registration.yml'):shutil.copy2(active/name,target/name)
        with (target/'database.dump').open('wb') as stream:
            r=subprocess.run(['docker','exec','starbridge-postgres','sh','-c','exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'],stdout=stream,stderr=subprocess.PIPE,timeout=180)
        if r.returncode or (target/'database.dump').stat().st_size<100:raise UpdateError('数据库备份未完成，未切换版本')
        with (target/'database.dump').open('rb') as stream:
            self.run(['docker','exec','-i','starbridge-postgres','pg_restore','--list'],input=stream.read(),timeout=120)
        with tarfile.open(target/'registration-runtime.tar.gz','w:gz') as tar:tar.add(ROOT/'.registration-runtime',arcname='.registration-runtime')
        images={service:self.run(['docker','inspect','--format','{{.Image}}',name]) for service,name in zip(SERVICES,['starbridge-app','starbridge-registration-worker'])}
        record={'id':job['id'],'version':self.current(),'images':images,'source':str(target),'epoch':1}
        write_json(target/'manifest.json',record)
        return record

    def override(self,images):
        path=STATE/'images.json'
        write_json(path,{'services':{key:{'image':value} for key,value in images.items()}})
        return path

    def activate_compose(self,source):
        active=STATE/'deployed';active.mkdir(exist_ok=True)
        if source.resolve()!=active.resolve():
            for name in ('compose.yml','compose.registration.yml'):shutil.copy2(source/name,active/name)
        self.private['active_source']=str(active)
        return active

    def prune_backups(self):
        candidates=[]
        for path in BACKUPS.glob('update-*'):
            if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(BACKUPS.resolve()):continue
            if not all((path/name).is_file() for name in ('verified.json','manifest.json','database.dump','.env','registration-runtime.tar.gz','compose.yml','compose.registration.yml')):continue
            candidates.append(path)
        protected={r['source'] for r in self.private.get('releases',[])}|{self.private.get('active_source','')}
        for path in sorted(candidates,reverse=True)[3:]:
            if str(path) not in protected:shutil.rmtree(path)

    def replace_env(self,version,public_origin):
        text=(ROOT/'.env').read_text()
        for key,value in [('APP_VERSION',version),('PUBLIC_ORIGIN',origin(public_origin))]:
            pattern=r'^'+key+r'=.*$'
            if re.search(pattern,text,re.M):text=re.sub(pattern,lambda _:key+'='+value,text,flags=re.M)
            else:text+='\n'+key+'='+value+'\n'
        temp=ROOT/'.env.update';temp.write_text(text);temp.chmod(0o600);os.replace(temp,ROOT/'.env')

    def health(self,images):
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            try:
                for name,service in zip(['starbridge-app','starbridge-registration-worker'],SERVICES):
                    result=json.loads(self.run(['docker','inspect',name]))[0]
                    if result['Image']!=images[service] or not result['State'].get('Running') or result['State'].get('Restarting'):raise ValueError()
                with urllib.request.urlopen('http://127.0.0.1:5876/health/ready',timeout=3) as response:
                    if response.status!=200:raise ValueError()
                self.run(['docker','exec','starbridge-registration-worker','node','-e',"fetch('http://127.0.0.1:3211/healthz',{signal:AbortSignal.timeout(3000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],timeout=5)
                return
            except (OSError,ValueError,UpdateError):pass
            time.sleep(1)
        raise UpdateError('新版本健康检查未通过')

    def restore(self,previous):
        self.publish('rolling_back',message='正在恢复先前版本')
        backup=Path(previous['source'])
        shutil.copy2(backup/'.env',ROOT/'.env')
        self.run(self.compose(self.activate_compose(backup),self.override(previous['images']))+['up','-d','--no-deps','--no-build','--force-recreate',*SERVICES],timeout=200,capture=False)
        self.health(previous['images'])

    def restart(self,job,rollback=False):
        target=next((r for r in self.private.get('releases',[]) if r['id']==job.get('version_id') and r.get('epoch')==1),None) if rollback else self.private.get('prepared')
        if not target or not rollback and (target['id']!=job.get('prepared_id') or target['base']!=self.current()):raise UpdateError('准备版本已失效，请重新更新')
        self.wait_idle();previous=self.backup(job)
        self.private['journal']={'previous':previous,'target':target};self.save()
        self.publish('switching',message='正在重启应用',restart_started_at=time.time())
        try:
            if rollback:
                # Rollback changes application binaries, retaining today's configuration.
                current_env=(ROOT/'.env').read_text()
                current_origin=re.search(r'^PUBLIC_ORIGIN=(.*)$',current_env,re.M)
                self.replace_env(target['version'],current_origin.group(1) if current_origin else '')
                self.run(self.compose(self.activate_compose(Path(target['source'])),self.override(target['images']))+['up','-d','--no-deps','--no-build','--force-recreate',*SERVICES],timeout=200,capture=False)
                self.health(target['images'])
            else:
                if self.run(['git','status','--porcelain','--untracked-files=no']):raise UpdateError('正式源码有未提交修改，未覆盖')
                self.run(['git','merge','--ff-only',target['version']])
                self.replace_env(target['version'],target['origin'])
                self.run(self.compose(self.activate_compose(Path(target['source'])),self.override(target['images']))+['up','-d','--no-deps','--no-build','--force-recreate',*SERVICES],timeout=200,capture=False)
                self.publish('verifying',message='正在验证服务恢复')
                self.health(target['images'])
            self.private.setdefault('releases',[]).append(previous)
            self.private['releases']=self.private['releases'][-3:]
            self.private.pop('prepared',None);self.private.pop('journal',None);self.save()
            write_json(Path(previous['source'])/'verified.json',{'version':target['version'],'time':time.time()})
            self.publish('completed',message='更新完成，服务已恢复',current_version=target['version'],prepared_id='',error='')
            try:self.prune_backups()
            except OSError:self.publish(backup_cleanup_pending=True)
        except Exception:
            self.restore(previous)
            self.private.pop('journal',None);self.save()
            raise UpdateError('更新未完成，已恢复先前应用版本；数据保留') from None

    def execute(self,job):
        with (STATE/(job['id']+'.log')).open('ab',buffering=0) as self.log:
            try:
                self.publish(operation_id=job['id'],error='')
                action=job['action']
                if action=='check':
                    prior=self.status.get('phase');self.publish('checking',message='正在检查 GitHub 版本');self.fetch()
                    self.publish('ready' if prior=='ready' and self.private.get('prepared') else 'idle',message='版本检查完成')
                elif action=='prepare':self.prepare(job)
                else:self.restart(job,action=='rollback')
            except Exception as error:
                # Never publish command output, env values or service credentials.
                self.publish('recovery_required' if self.private.get('journal') else 'failed',error=str(error) if isinstance(error,UpdateError) else '更新操作未完成，请查看维护日志',message='当前操作未完成')
            finally:self.log=None


def install():
    if os.geteuid()!=0:raise SystemExit('Install as root')
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    PUBLIC.mkdir(exist_ok=True,mode=0o755);PUBLIC.chmod(0o755);os.chown(PUBLIC,0,0)
    requests=PUBLIC/'requests';requests.mkdir(exist_ok=True,mode=0o700);os.chown(requests,1000,1000);requests.chmod(0o700)
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=Path('/usr/local/lib/starbridge');target.mkdir(parents=True,exist_ok=True)
    shutil.copy2(Path(__file__),target/'updater.py')
    unit='''[Unit]
Description=StarBridge version preparation and restart
After=docker.service network-online.target
Requires=docker.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/starbridge/updater.py --serve
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
'''
    Path('/etc/systemd/system/starbridge-updater.service').write_text(unit)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','starbridge-updater.service'],check=True)


def serve():
    import fcntl
    lock=(STATE/'lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    updater=Updater();worker=None
    if updater.private.get('journal'):
        with (STATE/'recovery.log').open('ab',buffering=0) as updater.log:updater.restore(updater.private['journal']['previous'])
        updater.private.pop('journal');updater.save();updater.publish('failed',message='维护服务恢复，已恢复先前版本',error='请重新检查更新')
    elif updater.status.get('phase') in BUSY:updater.publish('failed',message='上次维护已中断，请重新操作')
    else:updater.publish()
    while True:
        write_json(PUBLIC/'heartbeat.json',{'time':time.time()},0o644)
        pending=PUBLIC/'requests/request.json'
        if not worker or not worker.is_alive():
            try:
                fd=os.open(pending,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            except FileNotFoundError:fd=None
            except OSError:
                pending.unlink(missing_ok=True);fd=None
            if fd is not None:
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size>4096:raise UpdateError('无效更新请求')
                    raw=os.read(fd,4097);pending.unlink();job=validate_request(json.loads(raw))
                    if (STATE/(job['id']+'.log')).exists():raise UpdateError('请求已处理，请勿重复提交')
                    worker=threading.Thread(target=updater.execute,args=(job,),daemon=False);worker.start()
                except (ValueError,UpdateError):
                    pending.unlink(missing_ok=True)
                finally:os.close(fd)
        time.sleep(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--install',action='store_true');parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    if args.install:install()
    elif args.serve:serve()
