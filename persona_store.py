"""人格库数据层 —— personas / world_templates / onboarding_drafts 的读写。

为什么单独一个文件：
- db.py 已经 1300 行，再往里塞人格 CRUD 会更难读。
- 人设读取方有4 处（prompts.py / llm.py / proactive.py / routes/chat.py），
  统一从这里拿，兜底逻辑只写一遍：**库里没有就回退读旧的 JSON 文件**。
  这样即使迁移没做、人库是空的，服务也不会崩。

设计约定：
- JSON 列在库里存字符串，读出来时统一反序列化成Python 对象，调用方不用管格式。
- 所有读取都做进程内缓存（带版本号），因为人设每轮对话都要读，不能每次查库。
  写入 / 删除时 bump 版本号让缓存失效。

人设生效源（v0.15.3 明确，双源问题收口）：
- **数据库 personas 表是唯一生效源**，一切写入口（API / 引导 / 保存）只写库。
- persona_anchor.json / persona_local.json 降级为**种子/备份基线**：仅当人格库为空时
  兜底读取（保证服务不崩），且兜底发生时会打 warning 日志（只打一次，不刷屏）。
- 改人设的正确路径：走 API（POST /api/personas/{id}）；
  要把种子文件写进库：POST /api/personas/import-seed（自动先备份现有库）。
- 查当前生效来源：GET /api/personas/active（source = db / file / none）。
"""
import json
import logging
import os
import sqlite3
import threading
import time

import db

logger = logging.getLogger("persona_store")

# ============ 进程内缓存（人设每轮对话都读，不能每次查库）============
_cache = {}
_cache_ver = 0
_cache_lock = threading.Lock()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def bump_cache():
    """人设有写入时调用，让缓存失效"""
    global _cache_ver
    with _cache_lock:
        _cache_ver += 1
        _cache.clear()


def _cache_get(key, loader):
    """按 (key, 当前版本) 缓存，版本一变自然失效"""
    ck = (key, _cache_ver)
    if ck not in _cache:
        _cache[ck] = loader()
    return _cache[ck]


def _row_to_dict(row, json_cols):
    """sqlite3.Row → dict，并把指定列的 JSON 字符串反序列化"""
    d = dict(row)
    for col in json_cols:
        if col in d:
            v = d[col]
            if isinstance(v, str) and v:
                try:
                    d[col] = json.loads(v)
                except Exception:
                    pass
    return d


PERSONA_JSON_COLS = ("core_traits", "bond_origin", "intimacy",
                     "rules", "rule_levels", "examples")
TEMPLATE_JSON_COLS = ("factions", "boundaries", "customs")


# ================================================================
# 世界模板
# ================================================================

def list_world_templates(include_builtin=True):
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM world_templates"
    if not include_builtin:
        sql += " WHERE is_builtin = 0"
    sql += " ORDER BY is_builtin DESC, created"
    rows = conn.execute(sql).fetchall()
    return [_row_to_dict(r, TEMPLATE_JSON_COLS) for r in rows]


def get_world_template(tpl_id):
    if not tpl_id:
        return None
    return _cache_get(("tpl", tpl_id), lambda: _get_tpl_uncached(tpl_id))


def _get_tpl_uncached(tpl_id):
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM world_templates WHERE id = ?", (tpl_id,)).fetchone()
    return _row_to_dict(r, TEMPLATE_JSON_COLS) if r else None


def save_world_template(data, tpl_id=None):
    """新建或更新一个世界模板。data 是 dict，缺字段用空值补齐。

    返回模板 id。传了 tpl_id 就是更新，没传就是新建（自动生成 id）。
    """
    tpl_id = tpl_id or data.get("id") or f"wtpl_{int(time.time() * 1000)}"

    def _s(v):
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return v if v is not None else ""

    fields = {
        "name": data.get("name") or "未命名模板",
        "summary": data.get("summary", ""),
        "genre": data.get("genre", ""),
        "era": data.get("era", ""),
        "factions": _s(data.get("factions", [])),
        "boundaries": _s(data.get("boundaries", [])),
        "customs": _s(data.get("customs", [])),
        "user_nick": data.get("user_nick", ""),
        "updated": _now(),
    }
    conn = db.connect()
    cur = conn.cursor()
    exists = cur.execute("SELECT id FROM world_templates WHERE id = ?", (tpl_id,)).fetchone()
    if exists:
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur.execute(f"UPDATE world_templates SET {sets} WHERE id = ?",
                    (*fields.values(), tpl_id))
    else:
        cols = ", ".join(["id"] + list(fields.keys()) + ["is_builtin", "created"])
        marks = ", ".join(["?"] * (len(fields) + 3))
        cur.execute(f"INSERT INTO world_templates ({cols}) VALUES ({marks})",
                    (tpl_id, *fields.values(), data.get("is_builtin", 0), _now()))
    conn.commit()
    bump_cache()
    return tpl_id


def delete_world_template(tpl_id):
    """删模板。内置模板不删（否则新建角色会失去唯一的世界底座）。"""
    conn = db.connect()
    conn.execute("DELETE FROM world_templates WHERE id = ? AND is_builtin = 0", (tpl_id,))
    conn.commit()
    bump_cache()


def world_template_to_text(tpl):
    """把世界模板渲染成可注入提示词的文本块"""
    if not tpl:
        return ""
    lines = []
    if tpl.get("summary"):
        lines.append(str(tpl["summary"]))
    if tpl.get("genre"):
        lines.append(f"题材：{tpl['genre']}")
    if tpl.get("era"):
        lines.append(f"时代与地点：{tpl['era']}")
    for label, key in [("主要圈层", "factions"), ("日常惯例", "customs"),
                       ("内容边界", "boundaries")]:
        vals = tpl.get(key) or []
        if isinstance(vals, list) and vals:
            lines.append(f"{label}：" + "；".join(str(v) for v in vals))
    return "\n".join(lines)


# ================================================================
# 角色人格
# ================================================================

def list_personas(protagonist_only=False):
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM personas"
    if protagonist_only:
        sql += " WHERE is_protagonist = 1"
    sql += " ORDER BY is_protagonist DESC, created"
    rows = conn.execute(sql).fetchall()
    return [_row_to_dict(r, PERSONA_JSON_COLS) for r in rows]


def get_persona(persona_id):
    if not persona_id:
        return None
    return _cache_get(("p", persona_id), lambda: _get_p_uncached(persona_id))


def _get_p_uncached(persona_id):
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM personas WHERE id = ?", (persona_id,)).fetchone()
    return _row_to_dict(r, PERSONA_JSON_COLS) if r else None


def get_persona_by_name(name):
    """按名字取人格（视角切换时 people 表还没关联 persona_id 的过渡期用）。"""
    if not name:
        return None
    return _cache_get(("pname", name), lambda: _get_p_by_name_uncached(name))


def _get_p_by_name_uncached(name):
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT * FROM personas WHERE name = ? ORDER BY is_protagonist DESC, created LIMIT 1",
        (name,),
    ).fetchone()
    return _row_to_dict(r, PERSONA_JSON_COLS) if r else None


def get_protagonist(world_scope=None):
    """取主角人格（视角切换"切回"的目标）。没有则返回 None。

    v0.17.5 世界隔离：主角唯一性按世界判（world_scope）。
    - 传 world_scope：取该世界的主角（不同世界各有一个主角，互不抢位）；
    - 不传：取全局第一个主角（兼容不区分世界的旧调用，按创建时间取最老的）。
    """
    def _load():
        conn = db.connect()
        conn.row_factory = sqlite3.Row
        if world_scope is None:
            r = conn.execute(
                "SELECT * FROM personas WHERE is_protagonist = 1 ORDER BY created LIMIT 1"
            ).fetchone()
        else:
            r = conn.execute(
                "SELECT * FROM personas WHERE is_protagonist = 1"
                " AND COALESCE(world_scope, '') = ? ORDER BY created LIMIT 1",
                (world_scope or "",),
            ).fetchone()
        return _row_to_dict(r, PERSONA_JSON_COLS) if r else None
    return _cache_get(("protagonist", world_scope), _load)


def save_persona(data, persona_id=None):
    """新建或更新角色人格。返回 persona_id。

    set_protagonist=True 会把**同一个世界内**其他角色的 is_protagonist 清掉
    （v0.17.5：主角唯一按 world_scope 判，不同世界各有一个主角）。
    """
    persona_id = persona_id or data.get("id") or f"prs_{int(time.time() * 1000)}"

    def _s(v):
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return v if v is not None else ""

    is_prot = 1 if data.get("is_protagonist") else 0
    fields = {
        "name": data.get("name") or "未命名角色",
        "is_protagonist": is_prot,
        "depth": data.get("depth") or "full",
        "world_template_id": data.get("world_template_id", ""),
        "world_scope": data.get("world_scope", ""),
        "summary": data.get("summary", ""),
        "age": data.get("age") or 0,
        "birthday": data.get("birthday", ""),
        "occupation": data.get("occupation", ""),
        "core_traits": _s(data.get("core_traits", [])),
        "speech_style": data.get("speech_style", ""),
        "reply_format": data.get("reply_format", ""),
        "likes": data.get("likes", ""),
        "dislikes": data.get("dislikes", ""),
        "bond_origin": _s(data.get("bond_origin", {})),
        "intimacy": _s(data.get("intimacy", {})),
        "rules": _s(data.get("rules", [])),
        "rule_levels": _s(data.get("rule_levels", {})),
        "examples": _s(data.get("examples", [])),
        "anti_pattern": data.get("anti_pattern", ""),
        "content_mode": data.get("content_mode", ""),
        "memory_priority": data.get("memory_priority", ""),
        "user_name": data.get("user_name", ""),
        "notes": data.get("notes", ""),
        "updated": _now(),
    }
    conn = db.connect()
    cur = conn.cursor()
    # 主角唯一：同世界内只允许一个主角（v0.17.5；以前全局唯一，
    # 新世界建主角会把旧世界的主角位抢走，导致旧会话"切回"跑错人）
    if is_prot:
        cur.execute(
            "UPDATE personas SET is_protagonist = 0"
            " WHERE id != ? AND COALESCE(world_scope, '') = ?",
            (persona_id, data.get("world_scope") or ""),
        )
    exists = cur.execute("SELECT id FROM personas WHERE id = ?", (persona_id,)).fetchone()
    if exists:
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur.execute(f"UPDATE personas SET {sets} WHERE id = ?", (*fields.values(), persona_id))
    else:
        cols = ", ".join(["id"] + list(fields.keys()) + ["created"])
        marks = ", ".join(["?"] * (len(fields) + 2))
        cur.execute(f"INSERT INTO personas ({cols}) VALUES ({marks})",
                    (persona_id, *fields.values(), _now()))
    conn.commit()
    bump_cache()
    return persona_id


def delete_persona(persona_id):
    """删人格。主角不删（删了就没有"切回"目标，视角切换会瘫）。"""
    conn = db.connect()
    conn.execute("DELETE FROM personas WHERE id = ? AND is_protagonist = 0", (persona_id,))
    conn.commit()
    bump_cache()


def bind_persona_to_people(persona_id, person_name):
    """把人物卡关联到人格（切换视角时才有完整人设可用）"""
    conn = db.connect()
    conn.execute("UPDATE people SET persona_id = ? WHERE name = ?", (persona_id, person_name))
    conn.commit()


def set_session_persona(session_id, persona_id):
    """会话记住用哪个角色"""
    conn = db.connect()
    conn.execute("UPDATE sessions SET persona_id = ? WHERE id = ?", (persona_id, session_id))
    conn.commit()


def get_session_persona(session_id):
    conn = db.connect()
    r = conn.execute("SELECT persona_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return r[0] if r else ""


def session_world_scope(session_id):
    """当前会话所属世界的归属标识（= 会话角色人格的 world_scope）。

    v0.17.5 世界隔离的总闸门：视角切换候选、生成上下文、主角判定都按它过滤，
    实现「一个世界是一个独立包，角色不串门」。
    取法：会话绑定的角色人格 → 该人格的 world_scope。
    取不到返回 ""（未归属世界的角色自成一批，互不打扰）。
    """
    try:
        pid = get_session_persona(session_id)
        if not pid:
            return ""
        pa = get_persona(pid)
        if pa:
            return pa.get("world_scope") or ""
    except Exception:
        pass
    return ""


def list_personas_by_scope(world_scope):
    """按世界归属取角色列表（视角切换候选、世界包视图用）。

    同一个 world_scope 才算同一个世界；scope 为 "" 的是一批
    （没归属世界的自由角色），彼此也隔离。
    """
    want = world_scope or ""
    return [p for p in list_personas() if (p.get("world_scope") or "") == want]


def repoint_world_scope(old_scope, new_scope):
    """把一个世界归属下的所有角色改指向新归属，返回改动行数。

    v0.17.5「模板即整包」用：自由世界（scope = 世界名/题目）存成模板后，
    旗下角色全部改归属到模板 id——模板从此带着整套角色阵容，不只是设定文本。
    """
    if not old_scope or not new_scope or old_scope == new_scope:
        return 0
    conn = db.connect()
    cur = conn.execute(
        "UPDATE personas SET world_scope = ? WHERE COALESCE(world_scope, '') = ?",
        (new_scope, old_scope))
    conn.commit()
    if cur.rowcount:
        bump_cache()
    return cur.rowcount


# ================================================================
# 引导草稿（支持中断续接）
# ================================================================

def save_draft(draft_id, collected, current_layer, history,
               target_name="", depth="full", world_template_id="",
               status="active"):
    """新建或更新引导草稿。每轮引导后调用一次，断电/走开都能续上。"""
    draft_id = draft_id or f"draft_{int(time.time() * 1000)}"
    conn = db.connect()
    cur = conn.cursor()
    exists = cur.execute("SELECT id FROM onboarding_drafts WHERE id = ?", (draft_id,)).fetchone()
    payload = (
        target_name, depth, world_template_id,
        json.dumps(collected or {}, ensure_ascii=False),
        current_layer or 1,
        json.dumps(history or [], ensure_ascii=False),
        status, _now(),
    )
    if exists:
        cur.execute(
            "UPDATE onboarding_drafts SET target_name=?, depth=?, world_template_id=?, "
            "collected=?, current_layer=?, history=?, status=?, updated=? WHERE id=?",
            (*payload, draft_id),
        )
    else:
        cur.execute(
            "INSERT INTO onboarding_drafts (target_name, depth, world_template_id, collected, "
            "current_layer, history, status, created, updated, id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (*payload, _now(), draft_id),
        )
    conn.commit()
    return draft_id


def get_draft(draft_id):
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM onboarding_drafts WHERE id = ?", (draft_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for col in ("collected", "history"):
        try:
            d[col] = json.loads(d[col]) if d[col] else ({} if col == "collected" else [])
        except Exception:
            pass
    return d


def get_active_draft():
    """取最近一个进行中的草稿（说"接着聊角色"时用）"""
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT id FROM onboarding_drafts WHERE status = 'active' ORDER BY updated DESC LIMIT 1"
    ).fetchone()
    return get_draft(r["id"]) if r else None


def delete_draft(draft_id):
    conn = db.connect()
    conn.execute("DELETE FROM onboarding_drafts WHERE id = ?", (draft_id,))
    conn.commit()


# ================================================================
# 提示词渲染
# ================================================================

def persona_to_text(pa, world_tpl=None):
    """把人格渲染成可注入 system prompt 的核心设定块。

    这是 chat.py 里那段拼字符串逻辑的库版本，格式刻意保持一致，
    这样迁移前后生成的提示词能逐字对比（验收标准）。
    """
    if not pa:
        return "", ""
    rules = pa.get("rules") or []
    rules_text = "\n".join(f"- {r}" for r in rules)
    examples_text = ""
    if pa.get("examples"):
        examples_text = "\n格式示例：\n" + "\n".join(str(e) for e in pa["examples"])
    bond = pa.get("bond_origin") or {}
    bond_bits = []
    for label, key in [("关系", "relation"), ("相识", "how_met"),
                       ("当前阶段", "stage"), ("她知道你的", "she_knows")]:
        if bond.get(key):
            bond_bits.append(f"{label}：{bond[key]}")
    bond_text = ("\n关系起点：" + "；".join(bond_bits)) if bond_bits else ""
    world_text = world_template_to_text(world_tpl or get_world_template(pa.get("world_template_id")))

    core = (
        f"【绝对不可违反的核心设定】\n"
        f"你是「{pa.get('name', '')}」，核心性格：{', '.join(pa.get('core_traits') or [])}。\n"
        f"说话风格：{pa.get('speech_style', '')}。\n"
        f"强制规则：\n{rules_text}\n"
        f"关系模式：{pa.get('relationship_mode', '')}。"
        f"{bond_text}\n"
        f"{world_text}\n"
        f"{examples_text}\n"
    )
    return core, pa.get("reply_format", "")


def persona_brief(pa):
    """一小段人设摘要，给proactive 生成用（原来读 JSON 文件的那个函数）"""
    if not pa:
        return ""
    bits = []
    if pa.get("name"):
        bits.append(str(pa["name"]))
    traits = pa.get("core_traits") or []
    if traits:
        bits.append("性格：" + "、".join(str(t) for t in traits[:4]))
    if pa.get("speech_style"):
        bits.append("说话风格：" + str(pa["speech_style"]))
    return "；".join(bits)


def fallback_system_prompt(pa):
    """无会话人设时的简短回退。"""
    if not pa:
        return "你是一个温柔体贴的AI伴侣。像日常聊天一样自然地回复用户。"
    traits = "、".join(pa.get("core_traits") or [])
    return f"你是{pa.get('name', '')}，{traits}。{pa.get('speech_style', '')}。"


# ================================================================
# 兼容层：库空时回退读旧 JSON 文件（种子/备份基线）
# ================================================================

_FILE_CACHE = {}
_file_fallback_warned = False   # 兜底告警只打一次，避免每轮对话刷屏


def _seed_files():
    """种子文件候选（local 优先，它是覆盖层）。返回存在文件的全路径。"""
    base = os.path.dirname(os.path.abspath(__file__))
    for name in ("persona_local.json", "persona_anchor.json"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            yield path


def _seed_source_name():
    """当前实际会读的种子文件名（给日志/导入回执用）"""
    for path in _seed_files():
        return os.path.basename(path)
    return ""


def _load_json_file(persona_id=""):
    """读旧 persona_local.json / persona_anchor.json，转成 persona 形状的 dict"""
    if persona_id in _FILE_CACHE:
        return _FILE_CACHE[persona_id]
    d = {}
    for path in _seed_files():
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
        break
    d.setdefault("core_traits", [])
    d.setdefault("rules", [])
    d.setdefault("examples", [])
    _FILE_CACHE[persona_id] = d
    return d


def active_persona(session_id=None):
    """当前该用的人设。这是 4 个读取方的统一入口——迁移没做也不影响服务启动。

    优先序（v0.17.5 世界隔离）：
    1. 传了 session_id 且会话绑定了角色 → 就用那个角色（陪用户聊的就是她，
       多世界共存时绝不能拿全局主角顶替）；
    2. 会话没绑定角色 → 本世界的的主角（按会话的 world_scope 找）；
    3. 都不传 → 全局第一个主角（兼容旧调用方）；
    4. 库空 → 回退读旧 JSON 种子文件（兜底发生时打一次 warning，
       以前静默回退、改 JSON 不生效也没提示，是双源问题最坑的地方）。
    """
    def _load():
        global _file_fallback_warned
        pa = None
        if session_id:
            pa = get_persona(get_session_persona(session_id))
            if not pa:
                _scope = session_world_scope(session_id)
                if _scope:
                    pa = get_protagonist(_scope)
        if not pa:
            pa = get_protagonist()
        if pa:
            return pa
        # 库空 → 用文件，并把文件内容映射成 persona 形状
        f = _load_json_file()
        if not f:
            return None
        if not _file_fallback_warned:
            _file_fallback_warned = True
            logger.warning(
                "⚠️ 人格库为空，当前人设回退读自种子文件 %s。"
                "注意：改 JSON 文件对已入库的角色不生效——"
                "改人设请走 API（POST /api/personas/{id}），"
                "或把种子写进库（POST /api/personas/import-seed，自动先备份）。",
                _seed_source_name() or "(persona_local.json / persona_anchor.json)")
        return {
            "id": "file_fallback",
            "name": f.get("persona", ""),
            "core_traits": f.get("core_traits", []),
            "speech_style": f.get("speech_style", ""),
            "reply_format": f.get("reply_format", ""),
            "rules": f.get("rules", []),
            "rule_levels": f.get("_rule_levels", {}),
            "examples": f.get("examples", []),
            "anti_pattern": f.get("anti_pattern", ""),
            "content_mode": f.get("content_mode", ""),
            "memory_priority": f.get("memory_priority", ""),
            "is_protagonist": 1,
            "from_file": True,
        }
    return _cache_get(("active", session_id), _load)


def persona_source():
    """当前人设的生效来源：db / file / none。

    双源机制最大的坑是「不知道谁生效」——这个函数就是给管理端/自查用的，
    GET /api/personas/active 透出给前端。
    """
    if get_protagonist():
        return "db"
    return "file" if _load_json_file() else "none"


def _backup_personas():
    """人格库全量导出到 backups/ 目录，返回文件路径（库为空返回 None）。

    种子导入前必调用——覆盖写库前的救命稻草。
    """
    rows = list_personas()
    if not rows:
        return None
    bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
    os.makedirs(bdir, exist_ok=True)
    path = os.path.join(bdir, f"personas_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({"exported": _now(), "personas": rows}, fp,
                  ensure_ascii=False, indent=2)
    logger.info("人格库已备份到 %s（%d 个角色）", path, len(rows))
    return path


def import_seed_persona():
    """把种子 JSON 文件（persona_local.json / persona_anchor.json）导入人格库。

    语义：
    - 导入前自动全量备份现有库到 backups/（覆盖前的救命稻草）
    - 库里已有主角 → 就地覆盖更新（保住 id，会话/视角绑定不失效）；
      DB 专属字段（summary/age/bond_origin/user_name/world_template_id）保留原值，
      只刷新种子携带的字段（规则/示例/风格等）
    - 库空 → 新建 prs_xiaxue（与 tools/migrate_persona_to_db.py 的固定 id 一致）

    返回 {id, created, backup, name, source}。种子文件为空时抛 ValueError。
    """
    f = _load_json_file()
    if not f or not (f.get("persona") or "").strip():
        raise ValueError("种子文件为空或缺少 persona 字段，无法导入")

    backup = _backup_personas()

    pro = get_protagonist()
    if pro and pro.get("id") and pro.get("id") != "file_fallback":
        target_id = pro["id"]
        data = dict(pro)          # 保住 DB 专属字段
        created = False
    else:
        target_id = "prs_xiaxue"
        data = {"id": target_id, "is_protagonist": 1, "depth": "full"}
        created = True

    data.update({
        "name": f.get("persona") or data.get("name") or "未命名角色",
        "core_traits": f.get("core_traits", []),
        "speech_style": f.get("speech_style", ""),
        "reply_format": f.get("reply_format", ""),
        "anti_pattern": f.get("anti_pattern", ""),
        "rules": f.get("rules", []),
        "rule_levels": f.get("_rule_levels", {}),
        "examples": f.get("examples", []),
        "content_mode": f.get("content_mode", ""),
        "memory_priority": f.get("memory_priority", ""),
        "is_protagonist": 1,
        "notes": f"由种子文件 {_seed_source_name()} 导入 {_now()}"
                 f"（DB 专属字段保留原值）",
    })
    save_persona(data, target_id)
    bump_cache()
    logger.info("种子导入完成：%s（%s）%s", target_id, data["name"],
                "新建" if created else "覆盖更新")
    return {"id": target_id, "created": created, "backup": backup,
            "name": data["name"], "source": _seed_source_name()}