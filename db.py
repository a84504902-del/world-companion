"""数据库操作模块"""
import sqlite3
import threading
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger("db")

# ⚠️ 每个线程一个连接。
# 曾经是全局共享一个连接（check_same_thread=False），但 SQLite 的连接对象
# **本身不是线程安全的**——那个参数只是关掉了检查，不代表可以并发用。
# 后台任务（留言生成 / 场景提取 / 承诺抽取 / 原文索引）同时写时，
# 内部状态会互相干扰，一个事务卡住就让所有写操作报 "database is locked"。
_local = threading.local()


def connect():
    """统一数据库连接入口（每个线程独立连接）"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        # 并发写入时等待锁，而不是立刻失败
        conn.execute("PRAGMA busy_timeout=15000")
        # 开启外键约束，让 ON DELETE CASCADE 级联删除真正生效（SQLite 默认关闭）
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def close_conn():
    """关闭当前线程的数据库连接"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def init_db():
    """初始化数据库表结构"""
    conn = connect()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        tags TEXT DEFAULT '',
        session_id TEXT DEFAULT ''
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mem_ts ON memories(timestamp)")

    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT,
        system_prompt TEXT DEFAULT '',
        created TEXT,
        -- ⚠️ updated 目前【没有任何地方读它】。原来用来给会话列表排序，
        -- 但那会导致"点了就跳"（切换会话会刷新它），已改成按 created 排。
        -- 保留着是为将来可能做"按最近活跃排序"的选项。
        updated TEXT
    )""")

    # 视角切换（v0.11.30）：当前会话的扮演角色，空 = 默认夏雪
    _scols = [r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()]
    if "actor" not in _scols:
        c.execute("ALTER TABLE sessions ADD COLUMN actor TEXT DEFAULT ''")

    c.execute("""CREATE TABLE IF NOT EXISTS people (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        age INTEGER DEFAULT 0,
        description TEXT DEFAULT '',
        session_id TEXT DEFAULT ''
    )""")

    # 人物卡结构化字段（v0.11.25 迁移：老库逐列补齐，已有数据不受影响）
    for _col, _def in [
        ("birthday", "TEXT DEFAULT ''"),
        ("occupation", "TEXT DEFAULT ''"),
        ("personality", "TEXT DEFAULT ''"),
        ("speech_style", "TEXT DEFAULT ''"),
        ("likes", "TEXT DEFAULT ''"),
        ("dislikes", "TEXT DEFAULT ''"),
    ]:
        _cols = [r[1] for r in c.execute("PRAGMA table_info(people)").fetchall()]
        if _col not in _cols:
            c.execute(f"ALTER TABLE people ADD COLUMN {_col} {_def}")

    c.execute("""CREATE TABLE IF NOT EXISTS relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_a_id INTEGER NOT NULL,
        relation_type TEXT NOT NULL,
        person_b_id INTEGER NOT NULL,
        session_id TEXT DEFAULT ''
    )""")

    # 称呼字段（v0.11.26：把"开口怎么叫"变成数据，模型零推理）
    for _col in ["call_a_to_b", "call_b_to_a"]:
        _cols = [r[1] for r in c.execute("PRAGMA table_info(relations)").fetchall()]
        if _col not in _cols:
            c.execute(f"ALTER TABLE relations ADD COLUMN {_col} TEXT DEFAULT ''")

    # 迁移：添加 system_prompt 列（如果不存在）
    try:
        conn.execute("SELECT system_prompt FROM sessions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE sessions ADD COLUMN system_prompt TEXT DEFAULT ''")

    # 迁移：添加 title_locked 列（如果不存在）
    try:
        conn.execute("SELECT title_locked FROM sessions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE sessions ADD COLUMN title_locked INTEGER DEFAULT 0")

    # 迁移：添加 weight 列
    try:
        conn.execute("SELECT weight FROM memories LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE memories ADD COLUMN weight REAL DEFAULT 1.0")

    # 迁移：添加 source 列（记忆来源：manual=手动/指令保存, auto=自动提取）
    try:
        conn.execute("SELECT source FROM memories LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE memories ADD COLUMN source TEXT DEFAULT 'manual'")

    # 迁移：剧情时钟（story_now = 剧情当前时刻，last_seen = 上次交互的真实时间）
    try:
        conn.execute("SELECT story_now FROM sessions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE sessions ADD COLUMN story_now TEXT")

    try:
        conn.execute("SELECT last_seen FROM sessions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE sessions ADD COLUMN last_seen TEXT")

    # 迁移：上次生成"她主动发消息"的时间（防止重复生成留言）
    try:
        conn.execute("SELECT last_proactive FROM sessions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE sessions ADD COLUMN last_proactive TEXT")

    # 迁移：每个会话记住自己用的模型（对话1 用 DeepSeek、对话2 用 agnes）
    try:
        conn.execute("SELECT model FROM sessions LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE sessions ADD COLUMN model TEXT")

    # 向量嵌入表
    c.execute("""CREATE TABLE IF NOT EXISTS memory_embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        memory_id INTEGER NOT NULL,
        embedding TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mem_emb_mid ON memory_embeddings(memory_id)")

    # 会话摘要表（周期摘要落库，每轮注入，解决"摘要只存不注入"）
    c.execute("""CREATE TABLE IF NOT EXISTS summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_summaries_sid ON summaries(session_id)")

    # 世界状态卡（覆盖式写入，长期有效的当前事实，每轮注入）
    c.execute("""CREATE TABLE IF NOT EXISTS session_states (
        session_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        updated TEXT
    )""")

    # 钦点记忆（用户"记住：X"写入，常驻注入，永不自动淘汰 —— "刻在石头上"的层）
    c.execute("""CREATE TABLE IF NOT EXISTS pinned_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        content TEXT NOT NULL,
        slot TEXT DEFAULT '其他',          -- 槽位：关系共识/约定安排/雷区偏好/名场面/人物印象/其他
        status TEXT DEFAULT 'active',      -- active=生效 / superseded=被同槽位新条目替换
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT DEFAULT (datetime('now', 'localtime'))
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pins_sid ON pinned_memories(session_id, status)")

    # 未完结事件 / 承诺（核心引擎：你说过的话会长出后果）
    c.execute("""CREATE TABLE IF NOT EXISTS open_loops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        content TEXT NOT NULL,
        raw_quote TEXT DEFAULT '',
        promisor TEXT DEFAULT 'user',
        created_at TEXT,
        due_at TEXT,
        status TEXT DEFAULT 'pending',
        weight INTEGER DEFAULT 2,
        reminded_count INTEGER DEFAULT 0,
        last_reminded_at TEXT,
        -- ⚠️ closed_at 记录了"什么时候闭环的"，但目前【没有任何地方读它】。
        -- 面板只显示了 outcome 文字。留着是因为它是有意义的历史数据。
        closed_at TEXT,
        outcome TEXT DEFAULT ''
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_loops_sid ON open_loops(session_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_loops_sid_status ON open_loops(session_id, status)")

    # 场景状态（覆盖式写入：她在哪、在干嘛、你在哪 —— 给状态条"世界感"用）
    c.execute("""CREATE TABLE IF NOT EXISTS scene_states (
        session_id TEXT PRIMARY KEY,
        place TEXT DEFAULT '',
        her_doing TEXT DEFAULT '',
        user_place TEXT DEFAULT '',
        -- ⚠️ updated 同样没人读（前端只用了 place / her_doing / user_place）
        updated TEXT
    )""")

    # 对话原文分块（记忆检索基础：原文无损，替代有损的自动事实提取）
    c.execute("""CREATE TABLE IF NOT EXISTS conversation_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        content TEXT NOT NULL,
        start_message_id INTEGER DEFAULT 0,
        end_message_id INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_sid ON conversation_chunks(session_id)")

    # 原文分块向量
    c.execute("""CREATE TABLE IF NOT EXISTS chunk_embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chunk_id INTEGER NOT NULL,
        embedding TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (chunk_id) REFERENCES conversation_chunks(id) ON DELETE CASCADE
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chunk_emb_ck ON chunk_embeddings(chunk_id)")

    # 人物提案表（AI 自动检测新人物 → 用户确认后入库，防止乱加）
    c.execute("""CREATE TABLE IF NOT EXISTS character_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_proposals_sid ON character_proposals(session_id)")

    conn.commit()


def new_session_id():
    """生成新的会话ID"""
    return f"session_{int(datetime.now().timestamp() * 1000)}"


def create_message_table(session_id):
    """创建会话消息表"""
    table_name = f"messages_{session_id}"
    conn = connect()
    conn.execute(f"""CREATE TABLE IF NOT EXISTS [{table_name}] (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )""")
    conn.commit()


def drop_message_table(session_id):
    """删除会话消息表"""
    table_name = f"messages_{session_id}"
    conn = connect()
    conn.execute(f"DROP TABLE IF EXISTS [{table_name}]")
    conn.commit()


def save_message(session_id, role, content, timestamp=None):
    """保存消息。

    role 可以是 user / assistant / proactive（她主动发来的留言）。
    timestamp 可指定（留言的时间是"离开期间的某一刻"，不是现在）。
    """
    table_name = f"messages_{session_id}"
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute(f"INSERT INTO [{table_name}] (role, content, timestamp) VALUES (?, ?, ?)",
                 (role, content, ts))
    conn.commit()


def load_messages(session_id):
    """加载会话消息"""
    table_name = f"messages_{session_id}"
    conn = connect()
    try:
        rows = conn.execute(f"SELECT role, content, timestamp FROM [{table_name}] ORDER BY id").fetchall()
        return [{"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]} for r in rows]
    except Exception as e:
        logger.error("加载消息失败: %s", e)
        return []


def get_message_count(session_id):
    """获取消息数量"""
    table_name = f"messages_{session_id}"
    conn = connect()
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()[0]
        return count
    except Exception as e:
        logger.error("获取消息数量失败: %s", e)
        return 0


# 记忆操作
def save_memory(content, tags="", session_id="", weight=1.0, source="manual"):
    """保存记忆，返回 memory_id（source: manual=手动/指令保存, auto=自动提取）"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    cur = conn.execute(
        "INSERT INTO memories (content, timestamp, tags, session_id, weight, source) VALUES (?, ?, ?, ?, ?, ?)",
        (content, timestamp, tags, session_id, weight, source))
    memory_id = cur.lastrowid
    conn.commit()
    return memory_id


def save_memory_embedding(memory_id, embedding_json, content):
    """保存记忆向量"""
    conn = connect()
    conn.execute("INSERT INTO memory_embeddings (memory_id, embedding, content) VALUES (?, ?, ?)",
                 (memory_id, embedding_json, content))
    conn.commit()


def get_all_memory_embeddings():
    """获取所有记忆向量（启动时预加载，含 session_id）"""
    conn = connect()
    rows = conn.execute("SELECT me.id, me.memory_id, me.embedding, me.content, m.tags, m.session_id "
                        "FROM memory_embeddings me "
                        "JOIN memories m ON me.memory_id = m.id").fetchall()
    return [dict(r) for r in rows]


def get_memory_by_ids(ids):
    """批量获取记忆"""
    if not ids:
        return []
    conn = connect()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, content, timestamp, tags FROM memories WHERE id IN ({placeholders})", ids).fetchall()
    return [dict(r) for r in rows]


def delete_memory_embedding_by_memory_id(memory_id):
    """删除记忆对应的向量"""
    conn = connect()
    conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
    conn.commit()


def load_memories(tag=None, session_id=None):
    """加载记忆列表"""
    conn = connect()
    query = "SELECT id, content, timestamp, tags, session_id, weight, source FROM memories"
    conditions = []
    params = []

    if tag:
        conditions.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if session_id:
        conditions.append("session_id = ?")
        params.append(session_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY weight DESC, timestamp DESC"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_memory_weight(memory_id, weight):
    """更新记忆权重"""
    conn = connect()
    conn.execute("UPDATE memories SET weight = ? WHERE id = ?", (weight, memory_id))
    conn.commit()


def delete_memory(memory_id):
    """删除记忆及其向量"""
    conn = connect()
    conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
    conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()


# 会话操作
def get_all_sessions():
    """获取所有会话。

    ⚠️ 按**创建时间**排序，不能用 updated（最后活跃时间）——
    后者会让列表"点了就跳"：切换会话时会刷新当前会话的 updated，
    于是它跳到最上面，列表来回移动（用户明确反馈过）。
    按 created 排的话，新会话出现在最上面，之后位置就固定不动了。
    """
    conn = connect()
    rows = conn.execute(
        "SELECT id, title, system_prompt, created, updated FROM sessions "
        "ORDER BY created DESC, id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id):
    """获取单个会话"""
    conn = connect()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def get_actor(session_id):
    """当前会话的扮演角色（空 = 默认夏雪）"""
    conn = connect()
    row = conn.execute("SELECT actor FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return (row[0] or "") if row else ""


def set_actor(session_id, actor):
    """设置当前会话的扮演角色（传空串 = 切回夏雪）"""
    conn = connect()
    conn.execute("UPDATE sessions SET actor = ? WHERE id = ?", (actor or "", session_id))
    conn.commit()


def create_session(session_id, title=None, system_prompt=None, title_locked=False):
    """创建会话"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not title:
        # 不用"新对话"——会跟左栏那个「+ 新对话」按钮撞名
        now = datetime.now()
        title = f"{now.month}月{now.day}日 {now.strftime('%H:%M')}"
    if not system_prompt:
        system_prompt = ""
    conn = connect()
    conn.execute("INSERT INTO sessions (id, title, system_prompt, created, updated, title_locked) VALUES (?, ?, ?, ?, ?, ?)",
                 (session_id, title, system_prompt, timestamp, timestamp, 1 if title_locked else 0))
    conn.commit()


def update_session(session_id, title=None, system_prompt=None):
    """更新会话"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    if title is not None and system_prompt is not None:
        conn.execute("UPDATE sessions SET title = ?, system_prompt = ?, updated = ? WHERE id = ?",
                     (title, system_prompt, timestamp, session_id))
    elif title is not None:
        conn.execute("UPDATE sessions SET title = ?, updated = ? WHERE id = ?",
                     (title, timestamp, session_id))
    elif system_prompt is not None:
        conn.execute("UPDATE sessions SET system_prompt = ?, updated = ? WHERE id = ?",
                     (system_prompt, timestamp, session_id))
    else:
        conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (timestamp, session_id))
    conn.commit()


def update_system_prompt(session_id, system_prompt):
    """更新系统提示词"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("UPDATE sessions SET system_prompt = ?, updated = ? WHERE id = ?",
                 (system_prompt, timestamp, session_id))
    conn.commit()


def rename_session(session_id, new_title):
    """重命名会话并锁定标题"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("UPDATE sessions SET title = ?, title_locked = 1, updated = ? WHERE id = ?",
                 (new_title, timestamp, session_id))
    conn.commit()


def is_title_locked(session_id):
    """检查标题是否已锁定"""
    conn = connect()
    row = conn.execute("SELECT title_locked FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row["title_locked"] if row else False


def delete_memory_embeddings_by_session(session_id):
    """按会话删除所有记忆向量（memory_embeddings 无 session_id 列，需关联 memories）"""
    conn = connect()
    conn.execute(
        "DELETE FROM memory_embeddings WHERE memory_id IN "
        "(SELECT id FROM memories WHERE session_id = ?)",
        (session_id,)
    )
    conn.commit()


def delete_session(session_id):
    """删除会话（消息表、记忆、向量、人物、关系、摘要、状态卡、原文块全部清理，彻底不留残留）"""
    conn = connect()
    # 先删向量（memory_embeddings 无 session_id 列，靠关联 memories 删）
    conn.execute(
        "DELETE FROM memory_embeddings WHERE memory_id IN "
        "(SELECT id FROM memories WHERE session_id = ?)",
        (session_id,)
    )
    conn.execute("DELETE FROM memories WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM people WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM relations WHERE session_id = ?", (session_id,))
    # 新增：摘要、世界状态卡、对话原文块及其向量
    conn.execute("DELETE FROM summaries WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM session_states WHERE session_id = ?", (session_id,))
    conn.execute(
        "DELETE FROM chunk_embeddings WHERE chunk_id IN "
        "(SELECT id FROM conversation_chunks WHERE session_id = ?)",
        (session_id,)
    )
    conn.execute("DELETE FROM conversation_chunks WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM character_proposals WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    drop_message_table(session_id)
    # 同步清理内存中的向量缓存，防止重启前检索到已删会话的记忆
    try:
        import memory_retriever
        memory_retriever.clear_cache_by_session(session_id)
    except Exception:
        pass


# 人物操作
def get_people(session_id):
    """获取人物列表（含人物卡结构化字段）"""
    conn = connect()
    rows = conn.execute("SELECT id, name, age, description, birthday, occupation, "
                        "personality, speech_style, likes, dislikes FROM people WHERE session_id = ?",
                        (session_id,)).fetchall()
    return [dict(r) for r in rows]


# 人物卡的全部可编辑字段（add/update 共用）
_PERSON_FIELDS = ["name", "age", "description", "birthday", "occupation",
                  "personality", "speech_style", "likes", "dislikes"]


def add_person(name, age=0, description="", session_id="", **extra):
    """添加人物（extra: birthday/occupation/personality/speech_style/likes/dislikes）"""
    conn = connect()
    fields = {"name": name, "age": age, "description": description,
              "session_id": session_id, **{k: v for k, v in extra.items() if k in _PERSON_FIELDS}}
    cols = ", ".join(fields.keys())
    marks = ", ".join("?" for _ in fields)
    cursor = conn.execute(f"INSERT INTO people ({cols}) VALUES ({marks})", tuple(fields.values()))
    person_id = cursor.lastrowid
    conn.commit()
    return person_id


def update_person(person_id, **fields):
    """更新人物（只更新传入的字段，字段名白名单防注入）"""
    conn = connect()
    updates = {k: v for k, v in fields.items() if k in _PERSON_FIELDS and v is not None}
    if not updates:
        return
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE people SET {sets} WHERE id = ?", (*updates.values(), person_id))
    conn.commit()


def delete_person(person_id):
    """删除人物"""
    conn = connect()
    conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
    conn.execute("DELETE FROM relations WHERE person_a_id = ? OR person_b_id = ?",
                 (person_id, person_id))
    conn.commit()


# 关系操作
def get_relations(session_id):
    """获取关系列表（含称呼字段）"""
    conn = connect()
    rows = conn.execute("""
        SELECT r.id, r.relation_type, r.person_a_id, r.person_b_id,
               r.call_a_to_b, r.call_b_to_a,
               pa.name as person_a_name, pb.name as person_b_name
        FROM relations r
        LEFT JOIN people pa ON r.person_a_id = pa.id
        LEFT JOIN people pb ON r.person_b_id = pb.id
        WHERE r.session_id = ?
    """, (session_id,)).fetchall()
    return [dict(r) for r in rows]


def add_relation(person_a_id, relation_type, person_b_id, session_id="",
                 call_a_to_b="", call_b_to_a=""):
    """添加关系（call_*: A称呼B / B称呼A，可选）"""
    conn = connect()
    cursor = conn.execute(
        "INSERT INTO relations (person_a_id, relation_type, person_b_id, session_id, "
        "call_a_to_b, call_b_to_a) VALUES (?, ?, ?, ?, ?, ?)",
        (person_a_id, relation_type, person_b_id, session_id, call_a_to_b, call_b_to_a))
    relation_id = cursor.lastrowid
    conn.commit()
    return relation_id


def update_relation(relation_id, relation_type):
    """更新关系"""
    conn = connect()
    conn.execute("UPDATE relations SET relation_type = ? WHERE id = ?",
                 (relation_type, relation_id))
    conn.commit()


def delete_relation(relation_id):
    """删除关系"""
    conn = connect()
    conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
    conn.commit()


def search_messages(query, session_id=None):
    """搜索消息（按会话隔离）"""
    conn = connect()
    results = []

    if session_id:
        table_name = f"messages_{session_id}"
        try:
            rows = conn.execute(
                f"SELECT role, content, timestamp FROM [{table_name}] WHERE content LIKE ? ORDER BY timestamp DESC LIMIT 20",
                (f"%{query}%",)
            ).fetchall()
            session = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            title = session["title"] if session else "未知会话"
            for row in rows:
                results.append({
                    "session_id": session_id,
                    "session_title": title,
                    "role": row["role"],
                    "content": row["content"],
                    "timestamp": row["timestamp"]
                })
        except Exception as e:
            logger.error("搜索消息失败: %s", e)
    else:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'messages_%'"
        ).fetchall()
        for table in tables:
            tname = table["name"]
            sid = tname.replace("messages_", "")
            try:
                rows = conn.execute(
                    f"SELECT role, content, timestamp FROM [{tname}] WHERE content LIKE ? ORDER BY timestamp DESC LIMIT 10",
                    (f"%{query}%",)
                ).fetchall()
                session = conn.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
                title = session["title"] if session else "未知会话"
                for row in rows:
                    results.append({
                        "session_id": sid,
                        "session_title": title,
                        "role": row["role"],
                        "content": row["content"],
                        "timestamp": row["timestamp"]
                    })
            except Exception as e:
                logger.error("搜索消息失败(%s): %s", tname, e)
                continue

    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


def get_stats():
    """获取统计数据"""
    conn = connect()
    stats = {}

    # 会话数
    stats["session_count"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    # 消息总数
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'messages_%'").fetchall()
    total_messages = 0
    for t in tables:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM [{t['name']}]").fetchone()[0]
            total_messages += count
        except Exception as e:
            logger.error("统计消息数量失败(%s): %s", t['name'], e)
    stats["message_count"] = total_messages

    # 记忆数
    stats["memory_count"] = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    # 人物数
    stats["people_count"] = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]

    return stats


# ============ 会话摘要 ============
# ============ 钦点记忆（用户"记住：X"专用，常驻注入层） ============
def add_pinned(session_id, content, slot="其他"):
    """新增钦点记忆，返回 pin_id"""
    conn = connect()
    cur = conn.execute(
        "INSERT INTO pinned_memories (session_id, content, slot) VALUES (?, ?, ?)",
        (session_id, content, slot))
    conn.commit()
    return cur.lastrowid


def list_active_pins(session_id):
    """当前会话所有生效中的钦点记忆（按创建时间正序）"""
    conn = connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, content, slot, created_at FROM pinned_memories "
        "WHERE session_id = ? AND status = 'active' ORDER BY id",
        (session_id,)).fetchall()
    return [dict(r) for r in rows]


def supersede_pin(pin_id):
    """同槽位新条目替换旧条目：旧的不删，标记 superseded 留痕"""
    conn = connect()
    conn.execute(
        "UPDATE pinned_memories SET status='superseded', "
        "updated_at=datetime('now','localtime') WHERE id = ?", (pin_id,))
    conn.commit()


def delete_pinned(pin_id):
    """彻底删除钦点记忆（用户手动）"""
    conn = connect()
    conn.execute("DELETE FROM pinned_memories WHERE id = ?", (pin_id,))
    conn.commit()


def delete_session_pins(session_id):
    """删除会话时级联清理钦点记忆"""
    conn = connect()
    conn.execute("DELETE FROM pinned_memories WHERE session_id = ?", (session_id,))
    conn.commit()


def save_summary(session_id, summary):
    """保存会话摘要（周期摘要落库，供每轮注入）"""
    conn = connect()
    conn.execute("INSERT INTO summaries (session_id, summary) VALUES (?, ?)",
                 (session_id, summary))
    conn.commit()


def get_recent_summaries(session_id, limit=2):
    """获取最近 N 份摘要（按新旧倒序）"""
    conn = connect()
    rows = conn.execute(
        "SELECT id, summary, created_at FROM summaries WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ============ 世界状态卡（覆盖式） ============
def save_session_state(session_id, state_text):
    """覆盖式保存世界状态卡（不追加历史，只维护当前仍然成立的事实）"""
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO session_states (session_id, state, updated) VALUES (?, ?, ?)",
                 (session_id, state_text, updated))
    conn.commit()


def get_session_state(session_id):
    """获取世界状态卡文本，无则返回 None"""
    conn = connect()
    row = conn.execute("SELECT state FROM session_states WHERE session_id = ?", (session_id,)).fetchone()
    return row["state"] if row else None


# ============ 剧情时钟（story_now / last_seen） ============
def get_story_clock(session_id):
    """取剧情时钟，返回 dict：{story_now, last_seen, created}，不存在返回 None"""
    conn = connect()
    row = conn.execute(
        "SELECT story_now, last_seen, created FROM sessions WHERE id = ?",
        (session_id,)).fetchone()
    return dict(row) if row else None


def set_story_clock(session_id, story_now, last_seen):
    """写回剧情时钟（story_now 与 last_seen 均为 '%Y-%m-%d %H:%M:%S' 字符串）"""
    conn = connect()
    conn.execute("UPDATE sessions SET story_now = ?, last_seen = ? WHERE id = ?",
                 (story_now, last_seen, session_id))
    conn.commit()


# ============ 她主动发消息（proactive） ============
def get_last_proactive(session_id):
    """取上次生成留言的时间，没有返回 None（用于防止重复生成）"""
    conn = connect()
    row = conn.execute("SELECT last_proactive FROM sessions WHERE id = ?",
                       (session_id,)).fetchone()
    return row["last_proactive"] if row else None


def set_last_proactive(session_id, ts=None):
    ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("UPDATE sessions SET last_proactive = ? WHERE id = ?", (ts, session_id))
    conn.commit()


# ============ 每个会话的模型偏好 ============
def get_session_model(session_id):
    """取这个会话记住的模型；没设过返回空串（此时用全局默认）"""
    conn = connect()
    row = conn.execute("SELECT model FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return (row["model"] or "") if row else ""


def set_session_model(session_id, model):
    conn = connect()
    conn.execute("UPDATE sessions SET model = ? WHERE id = ?", (model or "", session_id))
    conn.commit()


# ============ 未完结事件 / 承诺（open loops） ============
_LOOP_UPDATABLE = {
    "content", "raw_quote", "promisor", "due_at", "status",
    "weight", "reminded_count", "last_reminded_at", "outcome",
}


def save_open_loop(session_id, content, raw_quote="", promisor="user", due_at=None, weight=2):
    """新建一条未完结事件，返回 id"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    cur = conn.execute(
        """INSERT INTO open_loops
           (session_id, content, raw_quote, promisor, created_at, due_at, status, weight)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (session_id, content, raw_quote, promisor, now, due_at, weight))
    conn.commit()
    return cur.lastrowid


def get_open_loops(session_id, status="pending"):
    """取该会话指定状态的事件，权重高的在前"""
    conn = connect()
    rows = conn.execute(
        """SELECT * FROM open_loops WHERE session_id = ? AND status = ?
           ORDER BY weight DESC, id ASC""",
        (session_id, status)).fetchall()
    return [dict(r) for r in rows]


def get_all_open_loops(session_id):
    """取该会话全部事件（含已闭环），用于面板展示"""
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM open_loops WHERE session_id = ? ORDER BY id DESC", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def update_open_loop(loop_id, **fields):
    """更新指定字段（白名单校验，None 值忽略）"""
    fields = {k: v for k, v in fields.items() if v is not None and k in _LOOP_UPDATABLE}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn = connect()
    conn.execute(f"UPDATE open_loops SET {sets} WHERE id = ?", list(fields.values()) + [loop_id])
    conn.commit()


def close_open_loop(loop_id, status, outcome=""):
    """闭环：fulfilled（兑现）/ expired（没做到）/ dropped（不了了之）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute(
        "UPDATE open_loops SET status = ?, outcome = ?, closed_at = ? WHERE id = ?",
        (status, outcome, now, loop_id))
    conn.commit()


def delete_open_loop(loop_id):
    conn = connect()
    conn.execute("DELETE FROM open_loops WHERE id = ?", (loop_id,))
    conn.commit()


# ============ 场景状态（覆盖式：她在哪、在干嘛、你在哪） ============
def get_scene(session_id):
    """取场景状态，没有则返回 None"""
    conn = connect()
    row = conn.execute("SELECT * FROM scene_states WHERE session_id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def set_scene(session_id, place="", her_doing="", user_place=""):
    """覆盖式写入场景状态（空值不覆盖已有值，避免模型偶尔漏写就清空）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = get_scene(session_id) or {}
    place = place or old.get("place", "")
    her_doing = her_doing or old.get("her_doing", "")
    user_place = user_place or old.get("user_place", "")
    conn = connect()
    conn.execute(
        """INSERT INTO scene_states (session_id, place, her_doing, user_place, updated)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
             place = excluded.place,
             her_doing = excluded.her_doing,
             user_place = excluded.user_place,
             updated = excluded.updated""",
        (session_id, place, her_doing, user_place, now))
    conn.commit()


# ============ 对话原文分块（记忆检索基础） ============
def save_conversation_chunk(session_id, content, start_message_id, end_message_id):
    """保存对话原文块，返回 chunk_id"""
    conn = connect()
    cur = conn.execute(
        "INSERT INTO conversation_chunks (session_id, content, start_message_id, end_message_id) VALUES (?, ?, ?, ?)",
        (session_id, content, start_message_id, end_message_id))
    conn.commit()
    return cur.lastrowid


def get_chunks_last_indexed_id(session_id):
    """获取已索引原文块的最大消息 id（用于增量切块）"""
    conn = connect()
    row = conn.execute(
        "SELECT MAX(end_message_id) AS mid FROM conversation_chunks WHERE session_id = ?",
        (session_id,)).fetchone()
    return row["mid"] if row and row["mid"] else 0


def save_chunk_embedding(chunk_id, embedding_json, content):
    """保存原文块向量"""
    conn = connect()
    conn.execute("INSERT INTO chunk_embeddings (chunk_id, embedding, content) VALUES (?, ?, ?)",
                 (chunk_id, embedding_json, content))
    conn.commit()


def get_all_chunk_embeddings():
    """获取所有原文块向量（含 session_id，启动预加载用）"""
    conn = connect()
    rows = conn.execute(
        "SELECT ce.id, ce.chunk_id, ce.embedding, ce.content, cc.session_id "
        "FROM chunk_embeddings ce JOIN conversation_chunks cc ON ce.chunk_id = cc.id").fetchall()
    return [dict(r) for r in rows]


def get_messages_with_id(session_id, after_id=0):
    """获取指定 id 之后的消息（含 id，用于原文切块索引）"""
    table_name = f"messages_{session_id}"
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT id, role, content FROM [{table_name}] WHERE id > ? ORDER BY id", (after_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("获取消息失败(%s): %s", table_name, e)
        return []


def clear_all_memories():
    """清空所有记忆与向量（一次性清理历史脏数据用；用户已授权直接清除）"""
    conn = connect()
    conn.execute("DELETE FROM memory_embeddings")
    conn.execute("DELETE FROM memories")
    conn.commit()


# ===== 人物提案（AI 自动检测新人物 → 用户确认） =====

def add_character_proposal(session_id, name, description=""):
    """添加人物提案（去重：同会话同名 pending 提案不重复添加）"""
    conn = connect()
    existing = conn.execute(
        "SELECT id FROM character_proposals WHERE session_id = ? AND name = ? AND status = 'pending'",
        (session_id, name)
    ).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO character_proposals (session_id, name, description) VALUES (?, ?, ?)",
        (session_id, name, description)
    )
    conn.commit()
    return cur.lastrowid


def get_pending_proposals(session_id):
    """获取会话的待确认提案"""
    conn = connect()
    rows = conn.execute(
        "SELECT id, name, description FROM character_proposals WHERE session_id = ? AND status = 'pending' ORDER BY created_at DESC",
        (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def accept_proposal(proposal_id):
    """接受提案 → 返回人物信息（由调用方写入 people 表）→ 标记提案为 accepted"""
    conn = connect()
    row = conn.execute(
        "SELECT name, description, session_id FROM character_proposals WHERE id = ? AND status = 'pending'",
        (proposal_id,)
    ).fetchone()
    if not row:
        return None
    conn.execute("UPDATE character_proposals SET status = 'accepted' WHERE id = ?", (proposal_id,))
    conn.commit()
    return dict(row)


def reject_proposal(proposal_id):
    """拒绝提案 → 标记为 rejected"""
    conn = connect()
    conn.execute("UPDATE character_proposals SET status = 'rejected' WHERE id = ?", (proposal_id,))
    conn.commit()


def delete_proposals_by_session(session_id):
    """删除会话的所有提案（会话删除时调用）"""
    conn = connect()
    conn.execute("DELETE FROM character_proposals WHERE session_id = ?", (session_id,))
    conn.commit()
