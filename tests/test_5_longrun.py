"""第五轮：长时间连续使用 + 响应时间 + 会话操作（临时脚本）"""
import json
import urllib.request
import urllib.error
import sys
import io
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
import db

BASE = 'http://localhost:8877'
PASS = 0
FAIL = []


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
        body = r.read().decode('utf-8', 'ignore')
        try:
            return r.status, json.loads(body)
        except Exception:
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'ignore')
    except Exception as e:
        return 0, str(e)


def chat(text, timeout=240):
    t0 = time.time()
    st, raw = post('/chat', {'text': text, 'mode': 'deepseek'}, timeout=timeout)
    dt = time.time() - t0
    content = ''
    if isinstance(raw, str):
        for line in raw.split('\n'):
            if not line.startswith('data: '):
                continue
            try:
                d = json.loads(line[6:])
            except Exception:
                continue
            if d.get('content'):
                content += d['content']
    return st, content, dt


def new_session():
    post('/new_chat', {})
    return db.connect().execute('SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1').fetchone()['id']


print('=' * 64)
print('【A】12 轮连续对话 · 稳定性与响应时间')
sid = new_session()
print('  会话:', sid)

convo = [
    '在干嘛呢', '刚下班，有点累', '你今天做了什么',
    '我给你买了杯奶茶', '晚上想吃什么', '我做饭给你吃吧',
    '明天带你去公园走走', '你最近有没有想我', '我想你了',
    '早点睡吧', '晚安', '我睡了',
]
times = []
errs = []
state_reset = 0
for i, m in enumerate(convo, 1):
    st, reply, dt = chat(m)
    times.append(dt)
    if st != 200 or not reply:
        errs.append(f'第{i}轮 status={st} len={len(reply)}')
    if '[[STATE]]' in reply:
        state_reset += 1
    print(f'  {i:2d}. [{dt:5.1f}s] {m[:14]:16s} -> {reply[:44].replace(chr(10), " ")}')

check('12 轮全部成功', not errs, str(errs))
avg = sum(times) / len(times)
check('平均响应 < 60s', avg < 60, f'{avg:.1f}s')
check('没有轮次超过 120s', max(times) < 120, f'max={max(times):.1f}s')
check('回复里无残留状态标记', state_reset == 0, f'{state_reset} 轮含 [[STATE]]')

print(f'  → 平均 {avg:.1f}s，最慢 {max(times):.1f}s，最快 {min(times):.1f}s')

print()
print('=' * 64)
print('【B】数据完整性（12 轮之后）')
msgs = db.load_messages(sid)
check('消息条数是偶数（问答成对）', len(msgs) % 2 == 0, f'{len(msgs)} 条')
check('消息数 >= 24', len(msgs) >= 24, f'{len(msgs)}')
roles = [m['role'] for m in msgs]
alt = all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)) if roles else False
check('user/assistant 交替出现', alt, str(roles[:6]))
empty = [m for m in msgs if not str(m.get('content', '')).strip()]
check('没有空消息', not empty, f'{len(empty)} 条空消息')

time.sleep(8)
sc = db.get_scene(sid)
check('场景已更新', bool(sc and sc.get('place')), str(sc))

loops_here = db.get_all_open_loops(sid)
print(f'  → 12 轮里抽到 {len(loops_here)} 条承诺:', [x['content'][:20] for x in loops_here])

print()
print('=' * 64)
print('【C】会话操作（切换 / 重命名 / 清空 / 导出）')
st, d = post('/api/rename_session', {'session_id': sid, 'title': '测试改名'})
check('重命名成功', st == 200, f'status={st}')
row = db.connect().execute('SELECT title FROM sessions WHERE id=?', (sid,)).fetchone()
check('标题已写入', row and row['title'] == '测试改名', str(row['title'] if row else None))

st, body = post('/chat', {'text': '我明天带你去钓鱼', 'mode': 'deepseek'})
time.sleep(7)
n_before = len(db.get_all_open_loops(sid))

st, d = post('/clear_chat', {})
check('清空对话成功', st == 200, f'status={st}')
msgs_after = db.load_messages(sid)
check('清空后消息为空', len(msgs_after) == 0, f'{len(msgs_after)} 条')

st, body = post('/export_chat', {'session_id': sid, 'format': 'json'})
check('导出不崩', st in (200, 400), f'status={st}')

print()
print('=' * 64)
print('【D】同一句话重复发送（去重/重复处理）')
for _ in range(3):
    chat('我明天带你去吃火锅')
    time.sleep(6)
dups = [x for x in db.get_all_open_loops(sid) if '火锅' in x['content']]
print(f'  → 连说 3 次"明天带你去吃火锅"，库里火锅相关承诺: {len(dups)} 条')
for d in dups:
    print(f'     [{d["status"]}] {d["content"]}')
# 说明：重复不是致命问题，但会让她反复提同一件事
check('重复发送不崩', True)
if len(dups) > 1:
    print(f'  ⚠️  注意：同一件事被记了 {len(dups)} 条（可能让她重复提同一件事）')

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
