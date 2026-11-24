"""Dash Arena room server. Python 3.9+, standard library only."""
import json, math, os, random, secrets, string, threading, time, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROOMS = {}
LOCK = threading.RLock()
PATTERNS = [[2,2,1,0,0,2,2,1],[0,2,2,2,1,2,0,2],[2,1,1,2,2,0,2,2],[2,2,3,3,2,2,1,0],[1,0,2,2,2,2,0,1],[2,2,0,2,1,2,2,0],[2,3,2,1,1,2,3,2],[0,1,2,2,2,1,2,0],[2,2,2,0,2,2,2,1],[1,2,3,2,0,3,2,1]]
COLORS = ['ed597a','50b8e9','ae78ec','79ce77','f4c252','ed854b','7c8ce9','51ad58']

def num(v, lo, hi):
    try: v = float(v)
    except (TypeError, ValueError, OverflowError): return 0
    return max(lo, min(hi, v)) if math.isfinite(v) else 0

def spawn(r,z):
    r['serial'] += 1
    r['walls'].append(dict(id=r['serial'], z=z, pattern=PATTERNS[0] if r['serial']==1 else random.choice(PATTERNS), broken=[], passed=False))

def advance(r, now):
    if r['phase'] != 'playing': return
    dt = max(0, now-r['tick']); r['tick']=now
    # Fixed substeps, shared server clock for all obstacles.
    dt=min(dt, 2)
    while dt>0 and r['phase']=='playing':
        step=min(.025,dt); dt-=step; r['elapsed']+=step
        factor=1.13**(r['waves']//10)
        r['spawn']-=step
        if r['spawn']<=0: spawn(r,-16); r['spawn']+=5/factor
        for w in r['walls']:
            w['z']+=4.4*factor*step
            if w['z']>12.6 and not w['passed']: w['passed']=True; r['waves']+=1
        r['walls']=[w for w in r['walls'] if w['z']<18]
        if r['waves']>=30: end(r)

def end(r):
    if r['phase']!='playing': return
    r['phase']='ended'; r['winners']=[p['id'] for p in r['players'].values() if p['alive']]

def check_end(r):
    if r['phase']=='playing' and sum(p['alive'] for p in r['players'].values())<=1: end(r)

def sweep(now):
    for code,r in list(ROOMS.items()):
        for pid,p in list(r['players'].items()):
            if now-p['seen']>20:
                del r['players'][pid]
        if not r['players']:
            del ROOMS[code]; continue
        if r['host'] not in r['players']: r['host']=next(iter(r['players']))
        advance(r,now); check_end(r)

def snapshot(r):
    return dict(code=r['code'],host=r['host'],phase=r['phase'],players=[{k:v for k,v in p.items() if k not in ('token','seen')} for p in r['players'].values()],elapsed=r['elapsed'],waves=r['waves'],walls=r['walls'],winners=r['winners'],round=r['round'])

def dispatch(d):
    now=time.monotonic(); sweep(now); action=d.get('action')
    if action in ('create','join'):
        name=str(d.get('name','')).strip()
        if not re.fullmatch(r'[\w-]{2,20}',name): raise ValueError('Nickname inválido.')
        if action=='create':
            if len(ROOMS)>=100: raise ValueError('Servidor cheio.')
            code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(5))
            while code in ROOMS: code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(5))
            r=dict(code=code,host=None,phase='lobby',players={},tick=now,elapsed=0,waves=0,walls=[],spawn=4.8,serial=0,winners=[],round=0); ROOMS[code]=r
        else:
            r=ROOMS.get(str(d.get('code','')).strip().upper())
            if not r: raise ValueError('Sala não encontrada.')
            if r['phase']!='lobby': raise ValueError('Partida já iniciada. Crie outra sala.')
        if len(r['players'])>=8: raise ValueError('Sala cheia (8 jogadores).')
        if any(p['name'].casefold()==name.casefold() for p in r['players'].values()): raise ValueError('Nickname já está nesta sala. Altere seu perfil.')
        pid=secrets.token_hex(8); token=secrets.token_hex(24)
        p=dict(id=pid,token=token,name=name,seen=now,c=COLORS[len(r['players'])%8],alive=True,x=0,y=0,z=4,angle=0,pose=0,run=0,ground=True,fall=False,phase=0)
        r['players'][pid]=p
        if r['host'] is None:r['host']=pid
        return dict(id=pid,token=token,room=snapshot(r))
    r=ROOMS.get(str(d.get('code','')).upper()); pid=d.get('id')
    p=r['players'].get(pid) if r and isinstance(pid,str) else None
    if not p or not isinstance(d.get('token'),str) or not secrets.compare_digest(p['token'],d['token']): raise ValueError('Conexão com a sala perdida.')
    p['seen']=now
    if action=='leave':
        del r['players'][pid]; sweep(now); return {'ok':True}
    if action=='reopen':
        if r['host']!=pid or r['phase']!='ended': raise ValueError('Somente o anfitrião pode reabrir o grupo após a partida.')
        r.update(phase='lobby',elapsed=0,waves=0,walls=[],winners=[],tick=now,round=r['round']+1)
        for v in r['players'].values(): v.update(alive=True,x=0,y=0,z=4,fall=False)
    elif action=='start':
        if r['host']!=pid: raise ValueError('Apenas o anfitrião pode iniciar.')
        if r['phase']!='lobby': raise ValueError('A partida já iniciou.')
        if len(r['players'])<2: raise ValueError('São necessários pelo menos 2 jogadores.')
        r.update(phase='playing',elapsed=0,waves=0,walls=[],spawn=4.8,serial=0,winners=[],tick=now,round=r['round']+1)
        for i,v in enumerate(r['players'].values()): v.update(alive=True,x=(i-(len(r['players'])-1)/2)*1.75,z=4,y=0,angle=0,pose=0,run=0,ground=True,fall=False,phase=0)
        spawn(r,-12.5)
    elif action=='poll':
        if r['phase']=='playing' and d.get('round')==r['round'] and p['alive']:
            a=d.get('actor')
            if isinstance(a,dict):
                for k,lo,hi in [('x',-30,30),('y',-10,8),('z',-30,30),('angle',-1e8,1e8),('pose',0,1),('run',0,12),('phase',-1e8,1e8)]: p[k]=num(a.get(k),lo,hi)
                for k in ('ground','fall'):p[k]=bool(a.get(k))
                if a.get('alive') is False or p['y']<-4.9: p['alive']=False
            broken=d.get('broken',[])
            if isinstance(broken,list):
                for item in broken[:32]:
                    if not isinstance(item,list) or len(item)!=2:continue
                    wid,idx=item
                    if type(idx)!=int or not 0<=idx<8:continue
                    for w in r['walls']:
                        if w['id']==wid and w['pattern'][idx]==3 and idx not in w['broken']:w['broken'].append(idx)
            check_end(r)
    else: raise ValueError('Ação inválida.')
    return {'room':snapshot(r)}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] not in ('/','/index.html'): self.send_error(404); return
        body=(ROOT/'index.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if self.path!='/api/room': self.send_error(404); return
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=16384: raise ValueError('Pedido inválido.')
            d=json.loads(self.rfile.read(n));
            if not isinstance(d,dict): raise ValueError('Pedido inválido.')
            with LOCK: result=dispatch(d)
            status=200
        except (ValueError,TypeError,KeyError) as e: result={'error':str(e)}; status=400
        body=json.dumps(result,ensure_ascii=False,allow_nan=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def log_message(self,*args): pass

if __name__=='__main__':
    port=int(os.environ.get('PORT','8000')); server=ThreadingHTTPServer(('0.0.0.0',port),Handler)
    print(f'Dash Arena Online: http://localhost:{port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: server.server_close()
