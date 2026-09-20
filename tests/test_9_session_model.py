"""会话级模型记忆 · 测试（临时脚本）"""
import json
import urllib.request
import urllib.error
import sys
import io

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


def post(path, payload, timeout=60):
    try:
        rq = urllib.request.Request(BASE + path,
                                    data=json.dumps(payload).encode(),
                                    headers={'Content-Type': 'application/json'})
        r = urllib.request.urlopen(rq, timeout=timeout)
        return r.status, json.loads(r.read().decode('utf-8', 'ignore'))
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


def get(path, timeout=30):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=timeout)
        return r.status, json.loads(r.read().decode('utf-8', 'ignore'))
    except Exception as e:
        return 0, {'err': str(e)}


# 先看可用模型 id
st, d = get('/api/llms')
llms = [l['id'] for l in d.get('llms', [])]
print('可用模型:', llms)
M1 = llms[0]
M2 = llms[1] if len(llms) > 1 else llms[0]
print(f'测试用: 会话A={M1}  会话B={M2}')
print()

print('=' * 60)
print('【A】新建会话时继承当前模型')
st, d = post('/new_chat', {'model': M1})
sidA = d.get('session_id')
check('新建会话A（带 model）', bool(sidA))
check('A 记住了模型', db.get_session_model(sidA) == M1, repr(db.get_session_model(sidA)))

st, d = post('/new_chat', {'model': M2})
sidB = d.get('session_id')
check('新建会话B（带 model）', bool(sidB))
check('B 记住了模型', db.get_session_model(sidB) == M2, repr(db.get_session_model(sidB)))

print()
print('=' * 60)
print('【B】切换会话时能读回各自的模型')
st, d = post('/switch_chat', {'session_id': sidA})
check('切到 A 返回 A 的模型', d.get('model') == M1, f"得到 {d.get('model')!r}")

st, d = post('/switch_chat', {'session_id': sidB})
check('切到 B 返回 B 的模型', d.get('model') == M2, f"得到 {d.get('model')!r}")

st, d = post('/switch_chat', {'session_id': sidA})
check('再切回 A 还是 A 的模型', d.get('model') == M1, f"得到 {d.get('model')!r}")

print()
print('=' * 60)
print('【C】改模型会写回当前会话')
st, d = post('/api/session_model', {'session_id': sidA, 'model': M2})
check('保存成功', st == 200 and d.get('ok'))
check('A 的模型已改', db.get_session_model(sidA) == M2)
check('B 不受影响', db.get_session_model(sidB) == M2)   # B 本来就是 M2

st, d = post('/api/session_model', {'session_id': sidA, 'model': M1})
check('改回 M1', db.get_session_model(sidA) == M1)

print()
print('=' * 60)
print('【D】/history 返回当前会话的模型（首次打开页面用）')
st, d = post('/switch_chat', {'session_id': sidB})
st, d = get('/history')
check('/history 带 model 字段', 'model' in d, str(list(d.keys())))
check('/history 返回的是 B 的模型', d.get('model') == M2, f"得到 {d.get('model')!r}")

print()
print('=' * 60)
print('【E】没设过模型的会话返回空串（前端会用全局默认）')
st, d = post('/new_chat', {})
sidC = d.get('session_id')
check('新建会话没传 model 时为空', db.get_session_model(sidC) in ('', None),
      repr(db.get_session_model(sidC)))
st, d = post('/switch_chat', {'session_id': sidC})
check('切换时返回空串不崩', d.get('model') in ('', None), repr(d.get('model')))

print()
print('=' * 60)
print('【F】切换会话没丢 timestamp（顺带修的 BUG）')
st, d = post('/switch_chat', {'session_id': sidB})
hist = d.get('history') or []
check('切换后的历史带 timestamp', all('timestamp' in m for m in hist) if hist else True,
      f'{sum(1 for m in hist if "timestamp" not in m)} 条缺失')

print()
print('=' * 60)
print('【G】异常参数')
st, d = post('/api/session_model', {'model': 'x'})
check('缺 session_id 不崩', st in (200, 400), f'status={st}')
st, d = post('/api/session_model', {'session_id': '__nope__', 'model': 'x'})
check('不存在的 session 不崩', st == 200, f'status={st}')

print()
print('=' * 60)
print(f'阶段小结: 通过 {PASS} 项, 失败 {len(FAIL)} 项')
for f in FAIL:
    print('   -', f)

# 清理
conn = db.connect()
for sid in (sidA, sidB, sidC):
    if not sid:
        continue
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
