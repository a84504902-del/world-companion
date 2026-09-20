"""第三轮：承诺抽取准确性回归测试（临时脚本）"""
import json
import urllib.request
import sys
import io
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
import db

BASE = 'http://localhost:8877'
PASS = 0
FAIL = []


def post(path, payload, timeout=240):
    rq = urllib.request.Request(BASE + path,
                                data=json.dumps(payload).encode(),
                                headers={'Content-Type': 'application/json'})
    r = urllib.request.urlopen(rq, timeout=timeout)
    return r.status, r.read().decode('utf-8', 'ignore')


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

CASES = [
    # (原话, 期望是否抽取, 标签)
    ('我明天带你去吃那家新开的火锅', True, '明确约定'),
    ('周末我一定陪你去海边看日出', True, '高权重约定'),
    ('下次带你去见见我朋友', True, '明确约定'),
    ('给你带了你爱吃的草莓蛋糕', True, '明确要给她'),
    ('今天天气挺不错的', False, '闲聊'),
    ('我明天下午要开会，可能很晚', False, '陈述自己安排 ←上轮BUG'),
    ('我出门上班了', False, '陈述自己安排'),
    ('今天好累啊', False, '无关情绪'),
    ('行吧', False, '敷衍'),
    ('嗯嗯知道了', False, '敷衍'),
    ('我今晚要加班，可能很晚回来', False, '陈述自己安排'),
    ('宝贝', False, '称呼'),
]

for text, should, label in CASES:
    before = len(db.get_all_open_loops(sid))
    st, reply = chat(text)
    time.sleep(7)
    after = db.get_all_open_loops(sid)
    got = len(after) > before
    ok = (got == should)
    if ok:
        PASS += 1
    else:
        FAIL.append(f'{label}: {text}')
    mark = 'OK  ' if ok else 'FAIL'
    print(f'  {mark} [{label}] "{text}"')
    print(f'       期望={should} 实际={got}', end='')
    if got and after:
        print(f'  抽到: {after[0]["content"][:50]}')
    else:
        print()

print()
print('=' * 64)
print(f'抽取准确率: {PASS}/{len(CASES)} = {PASS * 100 // len(CASES)}%')
if FAIL:
    print('失败项:')
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
print('已清理测试会话')
