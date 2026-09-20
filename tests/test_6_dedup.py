"""第六轮：承诺去重 + 导出接口（临时脚本）"""
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


post('/new_chat', {})
sid = db.connect().execute('SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1').fetchone()['id']
print('测试会话:', sid)
print()

print('=' * 64)
print('【A】同一件事连说 3 次 -> 应只记 1 条')
for i in range(3):
    chat('我明天带你去吃那家新开的火锅')
    time.sleep(7)
hot = [x for x in db.get_all_open_loops(sid) if '火锅' in x['content']]
print(f'  库里火锅相关承诺: {len(hot)} 条')
for h in hot:
    print(f'     [w={h["weight"]}] {h["content"]}')
check('连说 3 次只记 1 条', len(hot) == 1, f'实际 {len(hot)} 条')
if hot:
    check('重复后权重被提升', hot[0]['weight'] >= 3, f'w={hot[0]["weight"]}')

print()
print('=' * 64)
print('【B】两件不同的事 -> 应记 2 条（不能误判成同一件）')
n_before = len(db.get_all_open_loops(sid))
chat('我明天带你去电影院看那部新片')
time.sleep(7)
after = db.get_all_open_loops(sid)
movie = [x for x in after if '电影' in x['content'] or '片' in x['content']]
print(f'  新增: {[x["content"][:30] for x in after[:2]]}')
check('不同的事没有被合并', len(after) >= 2, f'总共 {len(after)} 条')
check('电影被单独记录', len(movie) >= 1, f'电影相关 {len(movie)} 条')

print()
print('=' * 64)
print('【C】导出接口（上一轮我测错了方法）')
try:
    r = urllib.request.urlopen(BASE + '/export_chat?session_id=' + sid + '&format=json', timeout=30)
    check('GET /export_chat 正常', r.status == 200, f'status={r.status}')
    body = r.read().decode('utf-8', 'ignore')
    check('导出内容非空', len(body) > 10, f'{len(body)} 字节')
except urllib.error.HTTPError as e:
    check('GET /export_chat 正常', False, f'HTTP {e.code}')
except Exception as e:
    check('GET /export_chat 正常', False, str(e))

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
print('已清理，剩余会话:', conn.execute('SELECT COUNT(*) c FROM sessions').fetchone()['c'])
