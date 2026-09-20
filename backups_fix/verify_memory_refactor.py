"""临时端到端验证脚本：原文切块索引 / 检索 / 会话隔离 / 状态卡 / 摘要 / 删除完整性（用临时库，不碰真实数据）"""
import os
import sys
import tempfile

sys.path.insert(0, r"E:\teams\ai-companion")
os.chdir(r"E:\teams\ai-companion")

import db

# 切换到临时数据库
tmp = os.path.join(tempfile.gettempdir(), "ai_companion_verify.db")
if os.path.exists(tmp):
    os.remove(tmp)
db.DB_PATH = tmp
db._conn = None
db.init_db()

import memory_retriever

# 1. 创建会话 + 注入 6 轮对话
sid = "verify_session_1"
db.create_session(sid)
db.create_message_table(sid)
pairs = [
    ("我叫小明，很高兴认识你", "你好小明！我是夏雪，很高兴认识你～"),
    ("我养了一只猫叫小花", "小花这个名字真可爱！它是只什么样的猫呀？"),
    ("小花是只橘猫，很粘人", "橘猫性格都超好的！我超喜欢粘人的猫"),
    ("最近我在学做饭", "做饭很有意思！你学会的第一道菜是什么？"),
    ("我学会了番茄炒蛋", "番茄炒蛋是经典！下次可以做给我吃吗？"),
    ("好的，改天做给你吃", "一言为定！我很期待"),
]
for u, a in pairs:
    db.save_message(sid, "user", u)
    db.save_message(sid, "assistant", a)

# 2. 原文切块索引
n = memory_retriever.index_conversation_chunks(sid)
print("【切块数】", n, "（预期 1：6 轮 = 12 条 = 3轮×2×2 组）")

# 3. 检索测试
chunks = memory_retriever.retrieve_chunks("小花", sid)
print("【检索'小花'】", [(c["score"], c["content"][:30].replace("\n", " ")) for c in chunks])

mem = memory_retriever.build_memory_context("做饭", sid)
print("【记忆上下文(做饭)】\n", mem[:180])

# 4. 会话隔离
sid2 = "verify_session_2"
db.create_session(sid2)
db.create_message_table(sid2)
db.save_message(sid2, "user", "张三和李四在谈生意")
db.save_message(sid2, "assistant", "好的，我记下了")
memory_retriever.index_conversation_chunks(sid2)
chunks2 = memory_retriever.retrieve_chunks("小花", sid2)
print("【跨会话检索'小花'(应空)】", chunks2)
memories_cross = memory_retriever.retrieve_relevant_memories("小花", sid2)
print("【跨会话记忆检索(应空)】", memories_cross)

# 5. 状态卡 + 摘要存取
db.save_session_state(sid, "- 人物关系：小明和夏雪是朋友\n- 约定：小明要做番茄炒蛋给夏雪吃")
print("【状态卡读取】", db.get_session_state(sid))
db.save_summary(sid, "小明介绍了自己、宠物猫小花和做饭爱好，答应做番茄炒蛋给夏雪吃")
print("【摘要读取】", db.get_recent_summaries(sid))

# 6. 删除会话完整性（新表数据必须清零）
db.delete_session(sid)
conn = db.connect()
checks = {
    "summaries": conn.execute("SELECT COUNT(*) FROM summaries WHERE session_id=?", (sid,)).fetchone()[0],
    "session_states": conn.execute("SELECT COUNT(*) FROM session_states WHERE session_id=?", (sid,)).fetchone()[0],
    "conversation_chunks": conn.execute("SELECT COUNT(*) FROM conversation_chunks WHERE session_id=?", (sid,)).fetchone()[0],
    "chunk_embeddings": conn.execute(
        "SELECT COUNT(*) FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM conversation_chunks WHERE session_id=?)",
        (sid,)).fetchone()[0],
    "memories": conn.execute("SELECT COUNT(*) FROM memories WHERE session_id=?", (sid,)).fetchone()[0],
    "messages表": len(db.load_messages(sid)),
}
print("【删除会话后残留检查】", checks)

# 7. 内存缓存也已清理
print("【删除后内存缓存】chunk_cache 中残留:", [c for c in memory_retriever._chunk_cache if c["session_id"] == sid])

# 8. 状态卡 JSON 格式化函数验证
from routes.chat import _format_state_card
card = _format_state_card('{"relations": ["小明 和 夏雪 是朋友"], "events": [], "promises": ["小明要做番茄炒蛋"], "preferences": ["喜欢橘猫"], "conflicts": []}')
print("【状态卡格式化】\n", card)

db.close_conn()
os.remove(tmp)
print("=== 全部验证完成 ===")
