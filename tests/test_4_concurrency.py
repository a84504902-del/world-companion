"""第四轮：并发 / 会话隔离 / 闭环 / 时间系统（临时脚本）"""
import json
import urllib.request
import urllib.error
import sys
import io
import time
import threading
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
import db
import timeflow

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


def new_session():
    post('/new_chat', {})
    return db.connect().execute('SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1').fetchone()['id']


print('=' * 64)
print('【A】承诺闭环流程')
s1 = new_session()
print('  会话1:', s1)

st, d = post('/api/loops/add', {'content': '带她去看电影', 'weight': 3, 'session_id': s1})
lid = d.get('id')
check('添加承诺', st == 200 and lid, f'status={st}')

st, d = get(f'/api/loops/list?session_id={s1}')
check('列表能查到', len(d.get('loops', [])) == 1)
check('状态为 pending', d['loops'][0]['status'] == 'pending')

st, d = post('/api/loops/close', {'id': lid, 'status': 'fulfilled', 'outcome': '真去了'})
check('标记做到', st == 200)

st, d = get(f'/api/loops/list?session_id={s1}')
check('pending 里不再出现', len(d.get('loops', [])) == 0)

st, d = get(f'/api/loops/list?session_id={s1}&all=1')
check('all=1 能查到已闭环', len(d.get('loops', [])) == 1)
check('闭环状态正确', d['loops'][0]['status'] == 'fulfilled', d['loops'][0]['status'])
check('outcome 已记录', d['loops'][0]['outcome'] == '真去了')

print()
print('=' * 64)
print('【B】会话隔离（关键：不能串数据）')
s2 = new_session()
print('  会话2:', s2)
check('两个会话 id 不同', s1 != s2)

post('/api/loops/add', {'content': '会话2的承诺', 'weight': 2, 'session_id': s2})

st, d1 = get(f'/api/loops/list?session_id={s1}&all=1')
st, d2 = get(f'/api/loops/list?session_id={s2}&all=1')
c1 = [x['content'] for x in d1.get('loops', [])]
c2 = [x['content'] for x in d2.get('loops', [])]
check('会话1 只有自己的', '带她去看电影' in c1 and '会话2的承诺' not in c1, f'{c1}')
check('会话2 只有自己的', '会话2的承诺' in c2 and '带她去看电影' not in c2, f'{c2}')

# 场景隔离
db.set_scene(s1, place='家', her_doing='做饭', user_place='公司')
sc1 = db.get_scene(s1)
sc2 = db.get_scene(s2)
check('场景也按会话隔离', sc1 and sc1['place'] == '家' and not sc2, f'sc1={sc1} sc2={sc2}')

print()
print('=' * 64)
print('【C】并发请求（20 个同时打）')
errors = []
results = []


def worker(i):
    try:
        st, d = get('/api/status?session_id=' + s1)
        results.append(st)
        if st != 200:
            errors.append(f'#{i} status={st}')
    except Exception as e:
        errors.append(f'#{i} {e}')


threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check('20 并发全部 200', not errors, str(errors[:3]))
check('返回数量正确', len(results) == 20, f'{len(results)}')

# 并发写入
werrs = []


def wworker(i):
    try:
        st, d = post('/api/loops/add', {'content': f'并发{i}', 'weight': 2, 'session_id': s1})
        if st != 200:
            werrs.append(f'#{i} {st}')
    except Exception as e:
        werrs.append(f'#{i} {e}')


ws = [threading.Thread(target=wworker, args=(i,)) for i in range(10)]
for t in ws:
    t.start()
for t in ws:
    t.join()
check('10 并发写入无错误', not werrs, str(werrs[:3]))
st, d = get(f'/api/loops/list?session_id={s1}&all=1')
check('并发写入的数据都在', len([x for x in d.get('loops', []) if x['content'].startswith('并发')]) == 10,
      f"实际 {len([x for x in d.get('loops', []) if x['content'].startswith('并发')])}")

print()
print('=' * 64)
print('【D】时间系统端到端（模拟离开）')
clock0 = db.get_story_clock(s1)
t0 = clock0['story_now'] if clock0 else None

# 模拟离开 3 小时
db.set_story_clock(s1, clock0['story_now'] if clock0 else datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                   (datetime.now() - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'))
r = timeflow.settle(s1)
check('离开 3 小时能结算', r is not None)
check('推进量接近饱和函数值（6.6h）',
      r and 6.0 < r['advanced_hours'] < 7.2, f"{r['advanced_hours'] if r else None}")

# 模拟离开 30 天，验证封顶
db.set_story_clock(s1, db.get_story_clock(s1)['story_now'],
                   (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S'))
r2 = timeflow.settle(s1)
check('离开 30 天封顶在 30h 内',
      r2 and r2['advanced_hours'] <= timeflow.W_MAX_HOURS + 0.5,
      f"{r2['advanced_hours'] if r2 else None}")

# peek 不推进（关键：刷新页面不该推剧情）
before = db.get_story_clock(s1)['story_now']
timeflow.peek(s1)
timeflow.peek(s1)
after = db.get_story_clock(s1)['story_now']
check('peek 不推进剧情时间', before == after, f'{before} -> {after}')

print()
print('=' * 64)
print('【E】前端资源完整性')
import urllib.request as _u
for path, key in [('/static/js/chat.js', 'stripState'),
                  ('/static/js/chat.js', 'dividerHtml'),
                  ('/static/js/chat.js', 'loadStatusBar'),
                  ('/static/js/app.js', 'autoGrowTextarea'),
                  ('/static/js/ui.js', 'openLoopsModal'),
                  ('/static/css/style.css', 'typingBounce'),
                  ('/static/css/style.css', 'sbFade')]:
    try:
        body = _u.urlopen(BASE + path, timeout=10).read().decode('utf-8', 'ignore')
        check(f'{path} 含 {key}', key in body)
    except Exception as e:
        check(f'{path} 含 {key}', False, str(e))

print()
print('=' * 64)
print(f'阶段小结: 通过 {PASS} 项, 失败 {len(FAIL)} 项')
for f in FAIL:
    print('   -', f)

# 清理
conn = db.connect()
for sid in (s1, s2):
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
