"""数据库操作模块"""
import sqlite3
import threading
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger("db")

_conn = None
_lock = threading.Lock()


def connect():
    """统一数据库连接入口（共享连接）"""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        # 开启外键约束，让 ON DELETE CASCADE 级联删除真正生效（SQLite 默认关闭）
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.row_factory = sqlite3.Row
    return _conn


def close_conn():
    """关闭共享数据库连接"""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


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
        updated TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS people (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        age INTEGER DEFAULT 0,
        description TEXT DEFAULT '',
        session_id TEXT DEFAULT ''
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_a_id INTEGER NOT NULL,
        relation_type TEXT NOT NULL,
        person_b_id INTEGER NOT NULL,
        session_id TEXT DEFAULT ''
    )""")

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


def save_message(session_id, role, content):
    """保存消息"""
    table_name = f"messages_{session_id}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute(f"INSERT INTO [{table_name}] (role, content, timestamp) VALUES (?, ?, ?)",
                 (role, content, timestamp))
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
    """获取所有会话"""
    conn = connect()
    rows = conn.execute("SELECT id, title, system_prompt, created, updated FROM sessions ORDER BY updated DESC").fetchall()
    return [dict(r) for r in rows]


def get_session(session_id):
    """获取单个会话"""
    conn = connect()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def create_session(session_id, title=None, system_prompt=None):
    """创建会话"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not title:
        title = f"新对话 {datetime.now().strftime('%H:%M')}"
    if not system_prompt:
        system_prompt = ""
    conn = connect()
    conn.execute("INSERT INTO sessions (id, title, system_prompt, created, updated) VALUES (?, ?, ?, ?, ?)",
                 (session_id, title, system_prompt, timestamp, timestamp))
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
    """获取人物列表"""
    conn = connect()
    rows = conn.execute("SELECT id, name, age, description FROM people WHERE session_id = ?",
                        (session_id,)).fetchall()
    return [dict(r) for r in rows]


def add_person(name, age=0, description="", session_id=""):
    """添加人物"""
    conn = connect()
    cursor = conn.execute("INSERT INTO people (name, age, description, session_id) VALUES (?, ?, ?, ?)",
                          (name, age, description, session_id))
    person_id = cursor.lastrowid
    conn.commit()
    return person_id


def update_person(person_id, name=None, age=None, description=None):
    """更新人物"""
    conn = connect()
    if name is not None:
        conn.execute("UPDATE people SET name = ? WHERE id = ?", (name, person_id))
    if age is not None:
        conn.execute("UPDATE people SET age = ? WHERE id = ?", (age, person_id))
    if description is not None:
        conn.execute("UPDATE people SET description = ? WHERE id = ?", (description, person_id))
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
    """获取关系列表"""
    conn = connect()
    rows = conn.execute("""
        SELECT r.id, r.relation_type, r.person_a_id, r.person_b_id,
               pa.name as person_a_name, pb.name as person_b_name
        FROM relations r
        LEFT JOIN people pa ON r.person_a_id = pa.id
        LEFT JOIN people pb ON r.person_b_id = pb.id
        WHERE r.session_id = ?
    """, (session_id,)).fetchall()
    return [dict(r) for r in rows]


def add_relation(person_a_id, relation_type, person_b_id, session_id=""):
    """添加关系"""
    conn = connect()
    cursor = conn.execute(
        "INSERT INTO relations (person_a_id, relation_type, person_b_id, session_id) VALUES (?, ?, ?, ?)",
        (person_a_id, relation_type, person_b_id, session_id))
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
