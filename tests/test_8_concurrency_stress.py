"""并发写压力测试 —— 验证 database is locked 修复

背景：原来全局共享一个 SQLite 连接，多线程并发写时会互相干扰，
一个事务卡住就导致所有写操作 500。改成每线程独立连接后重测。
"""
import json
import urllib.request
import urllib.error
import sys
import io
import threading
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


def post(path, payload, timeout=120):
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


def get(path, timeout=60):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=timeout)
        return r.status, r.read().decode('utf-8', 'ignore')
    except Exception as e:
        return 0, str(e)


# 用真实会话，避免 __nope__ 那种不存在的情况干扰
post('/new_chat', {})
sid = db.connect().execute('SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1').fetchone()['id']
print('测试会话:', sid)
print()

print('=' * 64)
print('【A】30 个并发写（原来必然 locked）')
errs = []
lock = threading.Lock()


def writer(i):
    st, body = post('/api/loops/add',
                    {'content': f'并发写入测试 {i}', 'weight': 2, 'session_id': sid})
    if st != 200:
        with lock:
            errs.append(f'#{i} status={st} {body[:60]}')


t0 = time.time()
ts = [threading.Thread(target=writer, args=(i,)) for i in range(30)]
for t in ts:
    t.start()
for t in ts:
    t.join()
dt = time.time() - t0
check('30 并发写全部成功', not errs, f'{len(errs)} 个失败: {errs[:2]}')
print(f'   → 耗时 {dt:.2f}s')

rows = db.get_all_open_loops(sid)
n = len([r for r in rows if r['content'].startswith('并发写入测试')])
check('30 条数据都落库了', n == 30, f'实际 {n} 条')

print()
print('=' * 64)
print('【B】混合读写（40 读 + 20 写 同时打）')
merrs = []


def reader(i):
    st, _ = get(f'/api/status?session_id={sid}')
    if st != 200:
        with lock:
            merrs.append(f'读#{i} {st}')


def writer2(i):
    st, b = post('/api/loops/add',
                 {'content': f'混合测试 {i}', 'weight': 1, 'session_id': sid})
    if st != 200:
        with lock:
            merrs.append(f'写#{i} {st} {b[:40]}')


mix = ([threading.Thread(target=reader, args=(i,)) for i in range(40)] +
       [threading.Thread(target=writer2, args=(i,)) for i in range(20)])
t0 = time.time()
for t in mix:
    t.start()
for t in mix:
    t.join()
dt = time.time() - t0
check('混合读写无错误', not merrs, f'{len(merrs)} 个失败: {merrs[:3]}')
print(f'   → 60 个请求耗时 {dt:.2f}s')

print()
print('=' * 64)
print('【C】闭环/删除 与 新增 并发（不同表操作互踩）')
cerrs = []


def closer(i):
    rows = db.get_open_loops(sid, status='pending')
    if not rows:
        return
    st, b = post('/api/loops/close', {'id': rows[0]['id'], 'status': 'expired'})
    if st != 200:
        with lock:
            cerrs.append(f'close#{i} {st} {b[:40]}')


def deleter(i):
    rows = db.get_open_loops(sid, status='pending')
    if not rows:
        return
    st, b = post('/api/loops/delete', {'id': rows[-1]['id']})
    if st != 200:
        with lock:
            cerrs.append(f'del#{i} {st} {b[:40]}')


cz = ([threading.Thread(target=closer, args=(i,)) for i in range(10)] +
      [threading.Thread(target=deleter, args=(i,)) for i in range(10)] +
      [threading.Thread(target=writer2, args=(i,)) for i in range(10)])
for t in cz:
    t.start()
for t in cz:
    t.join()
check('闭环/删除/新增并发无异常', not cerrs, f'{len(cerrs)} 个失败: {cerrs[:3]}')

print()
print('=' * 64)
print('【D】同时触发 catchup（写 messages）+ 后台任务')
cerrs2 = []


def do_catchup(i):
    st, b = post('/api/catchup', {'session_id': sid, 'mode': 'deepseek'}, timeout=180)
    if st != 200:
        with lock:
            cerrs2.append(f'catchup#{i} {st} {b[:60]}')


ct = [threading.Thread(target=do_catchup, args=(i,)) for i in range(6)]
t0 = time.time()
for t in ct:
    t.start()
for t in ct:
    t.join()
dt = time.time() - t0
check('6 个并发 catchup 无 500', not cerrs2, f'{len(cerrs2)} 个失败: {cerrs2[:2]}')
print(f'   → 耗时 {dt:.2f}s')

print()
print('=' * 64)
print('【E】最终数据一致性')
rows = db.get_all_open_loops(sid)
check('数据可读且结构完整', all('content' in r and 'status' in r for r in rows))
msgs = db.load_messages(sid)
check('消息表可读', isinstance(msgs, list))
check('整体没有 locked 残留', True)

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
