"""她主动发消息 · 端到端测试（临时脚本）"""
import json
import urllib.request
import urllib.error
import sys
import io
import time
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
import db
import proactive

BASE = 'http://localhost:8877'
PASS = 0
FAIL = []
FMT = '%Y-%m-%d %H:%M:%S'


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  OK   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name}  {detail}')


def post(path, payload, timeout=240):
    try:
        rq = urllib.request.Request(BASE + path,
                                    data=json.dumps(payload).encode(),
                                    headers={'Content-Type': 'application/json'})
        r = urllib.request.urlopen(rq, timeout=timeout)
        return r.status, r.read().decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'ignore')
    except Exception as e:
        return 0, str(e)


def chat(text):
    st, raw = post('/chat', {'text': text, 'mode': 'deepseek'})
    content = ''
    for line in raw.split('\n'):
        if not line.startswith('data: '):
            continue
        try:
            d = json.loads(line[6:])
        except Exception:
            continue
        if d.get('content'):
            content += d['content']
    return st, content


def catchup(sid):
    st, raw = post('/api/catchup', {'session_id': sid, 'mode': 'deepseek'})
    try:
        return st, json.loads(raw)
    except Exception:
        return st, {}


post('/new_chat', {})
sid = db.connect().execute('SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1').fetchone()['id']
print('测试会话:', sid)
print()

print('=' * 64)
print('【A】铺垫：聊两句，让她有状态和惦记的事')
chat('我出门上班了')
time.sleep(7)
chat('我明天带你去吃火锅')
time.sleep(7)
print('  场景:', {k: v for k, v in (db.get_scene(sid) or {}).items() if k in ('place', 'her_doing', 'user_place')})
print('  承诺:', [x['content'] for x in db.get_open_loops(sid)])

print()
print('=' * 64)
print('【B】离开不够久 -> 不该生成')
st, d = catchup(sid)
check('刚聊完不生成留言', not d.get('messages'), f'返回 {len(d.get("messages") or [])} 条')

print()
print('=' * 64)
print('【C】模拟离开 5 小时 -> 应该生成')
clock = db.get_story_clock(sid)
db.set_story_clock(sid, clock['story_now'],
                   (datetime.now() - timedelta(hours=5)).strftime(FMT))
db.set_last_proactive(sid, None) if False else None
conn = db.connect()
conn.execute('UPDATE sessions SET last_proactive = NULL WHERE id = ?', (sid,))
conn.commit()

t0 = time.time()
st, d = catchup(sid)
dt = time.time() - t0
msgs = d.get('messages') or []
print(f'  生成耗时 {dt:.1f}s')
check('离开 5 小时生成了留言', len(msgs) > 0, f'返回 {len(msgs)} 条')
check('条数合理（1-3）', 1 <= len(msgs) <= 3, f'{len(msgs)} 条')
print()
for i, m in enumerate(msgs, 1):
    print(f'   {i}. [{m["timestamp"][5:16]}] {m["content"]}')

print()
print('=' * 64)
print('【D】时间分布检查（不能全堆在"现在"）')
if msgs:
    parse = lambda s: datetime.strptime(s, FMT)
    times = [parse(m['timestamp']) for m in msgs]
    clock2 = db.get_story_clock(sid)
    last_seen = parse(clock2['last_seen'])
    now = datetime.now()
    check('留言时间都在"离开期间"',
          all(last_seen - timedelta(minutes=2) <= t <= now for t in times),
          f'last_seen={last_seen}')
    check('留言时间递增', all(times[i] <= times[i + 1] for i in range(len(times) - 1)))
    span_min = (times[-1] - times[0]).total_seconds() / 60
    print(f'   → 第一条到第三条跨了 {span_min:.0f} 分钟')

print()
print('=' * 64)
print('【E】重复调用 -> 不应该重复生成')
st, d2 = catchup(sid)
check('同一段时间不重复生成', not d2.get('messages'),
      f'又生成了 {len(d2.get("messages") or [])} 条')

print()
print('=' * 64)
print('【F】数据落库检查')
rows = db.load_messages(sid)
pro = [m for m in rows if m['role'] == 'proactive']
check('留言已写入历史', len(pro) == len(msgs), f'{len(pro)} vs {len(msgs)}')
check('留言 role 为 proactive', all(m['role'] == 'proactive' for m in pro))
check('留言有时间戳', all(m.get('timestamp') for m in pro))

print()
print('=' * 64)
print('【G】对后续对话的影响（历史里含 proactive，不能崩）')
st, reply = chat('我回来了')
check('含留言的历史下对话不崩', st == 200 and len(reply) > 0, f'status={st}')
print('   她回:', reply[:110].replace('\n', ' '))

print()
print('=' * 64)
print(f'阶段小结: 通过 {PASS} 项, 失败 {len(FAIL)} 项')
for f in FAIL:
    print('   -', f)

# 清理
conn = db.connect()
try:
    db.drop_message_table(sid)
except Exception:
    pass
for t in ('session_states', 'open_loops', 'conversation_chunks', 'scene_states'):
    conn.execute(f'DELETE FROM {t} WHERE session_id = ?', (sid,))
conn.execute('DELETE FROM sessions WHERE id = ?', (sid,))
conn.commit()
print()
print('已清理测试会话，剩余:', conn.execute('SELECT COUNT(*) c FROM sessions').fetchone()['c'])
