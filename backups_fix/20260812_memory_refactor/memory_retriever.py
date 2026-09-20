"""记忆检索模块 — 对话原文分块检索（episodic）+ 手动保存记忆（semantic），严格会话隔离"""
import asyncio
import logging

import db
import embedding

logger = logging.getLogger("retriever")

# 内存中的记忆向量缓存: [{id, memory_id, embedding, content, tags, session_id}]
_vector_cache = []
# 内存中的对话原文分块向量缓存: [{chunk_id, embedding, content, session_id}]
_chunk_cache = []
_cache_loaded = False

# 每 3 轮对话（1 轮 = user + assistant 两条消息）切一个原文块
CHUNK_ROUNDS = 3


def preload_cache():
    """启动时预加载所有向量（记忆 + 对话原文块）到内存"""
    global _vector_cache, _chunk_cache, _cache_loaded
    if _cache_loaded:
        return
    try:
        rows = db.get_all_memory_embeddings()
        _vector_cache = []
        for r in rows:
            vec = embedding.vector_from_json(r["embedding"])
            if vec:
                _vector_cache.append({
                    "id": r["id"],
                    "memory_id": r["memory_id"],
                    "embedding": vec,
                    "content": r["content"],
                    "tags": r["tags"] or "",
                    "session_id": r["session_id"] or ""
                })
        # 对话原文分块向量
        chunk_rows = db.get_all_chunk_embeddings()
        _chunk_cache = []
        for r in chunk_rows:
            vec = embedding.vector_from_json(r["embedding"])
            if vec:
                _chunk_cache.append({
                    "chunk_id": r["chunk_id"],
                    "embedding": vec,
                    "content": r["content"],
                    "session_id": r["session_id"] or ""
                })
        _cache_loaded = True
        logger.info("向量缓存预加载完成: 记忆 %d 条, 原文块 %d 条", len(_vector_cache), len(_chunk_cache))
    except Exception as e:
        logger.error("向量缓存预加载失败: %s", e)
        _cache_loaded = True


def add_to_cache(memory_id, embedding_vec, content, tags="", session_id=""):
    """新增记忆向量到缓存"""
    global _vector_cache
    if embedding_vec:
        _vector_cache.append({
            "id": None,
            "memory_id": memory_id,
            "embedding": embedding_vec,
            "content": content,
            "tags": tags,
            "session_id": session_id
        })


def add_chunk_to_cache(chunk_id, embedding_vec, content, session_id=""):
    """新增原文块向量到缓存"""
    global _chunk_cache
    if embedding_vec:
        _chunk_cache.append({
            "chunk_id": chunk_id,
            "embedding": embedding_vec,
            "content": content,
            "session_id": session_id
        })


def remove_from_cache(memory_id):
    """从缓存中删除指定记忆的向量"""
    global _vector_cache
    _vector_cache = [v for v in _vector_cache if v["memory_id"] != memory_id]


def clear_cache_by_session(session_id):
    """删除会话后同步清理该会话的所有向量缓存（记忆 + 原文块），防止残留"""
    global _vector_cache, _chunk_cache
    _vector_cache = [v for v in _vector_cache if v.get("session_id") != session_id]
    _chunk_cache = [c for c in _chunk_cache if c.get("session_id") != session_id]


def clear_all_cache():
    """清空内存向量缓存（一次性清理历史脏数据后调用）"""
    global _vector_cache, _chunk_cache
    _vector_cache = []
    _chunk_cache = []


def retrieve_relevant_memories(query, session_id="", top_k=5, min_score=0.3):
    """语义检索：只返回当前会话中与 query 最相关的 top_k 条手动保存的记忆（严格会话隔离）"""
    if not _vector_cache:
        return []

    query_vec = embedding.embed_text(query)
    if not query_vec:
        return []

    # 严格按 session_id 过滤：空 session_id 的记忆不再被所有会话共享（堵会话隔离漏洞）
    candidates = [
        v for v in _vector_cache
        if v["session_id"] == session_id
    ]

    scored = []
    for item in candidates:
        score = embedding.cosine_similarity(query_vec, item["embedding"])
        if score >= min_score:
            scored.append({
                "memory_id": item["memory_id"],
                "content": item["content"],
                "score": round(score, 4),
                "tags": item["tags"]
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def retrieve_chunks(query, session_id, top_k=3, min_score=0.35):
    """按语义检索当前会话的对话原文块（原文无损，替代有损的事实提取）"""
    if not _chunk_cache:
        return []

    query_vec = embedding.embed_text(query)
    if not query_vec:
        return []

    candidates = [c for c in _chunk_cache if c["session_id"] == session_id]

    scored = []
    for item in candidates:
        score = embedding.cosine_similarity(query_vec, item["embedding"])
        if score >= min_score:
            scored.append({
                "chunk_id": item["chunk_id"],
                "content": item["content"],
                "score": round(score, 4)
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def build_memory_context(query, session_id="", top_k=5):
    """构建注入系统提示的记忆文本：
    1. 对话原文片段（episodic，按话题检索，无损耗无幻觉）
    2. 手动保存的记忆（semantic，用户主动要记的事实）
    """
    parts = []

    # 1. 对话原文片段
    chunks = retrieve_chunks(query, session_id, top_k=3)
    if chunks:
        chunk_text = "\n\n".join(c["content"] for c in chunks)
        parts.append(f"【过往对话片段（与当前话题可能相关）】\n{chunk_text}")

    # 2. 手动保存的记忆
    memories = retrieve_relevant_memories(query, session_id, top_k)
    if memories:
        lines = [f"- {m['content']}" for m in memories]
        parts.append("【保存的记忆】\n" + "\n".join(lines))

    return "\n\n".join(parts)


def store_memory_with_embedding(content, tags="", session_id="", source="manual"):
    """存储记忆并生成向量（source: manual=手动/指令保存, auto=自动提取）"""
    memory_id = db.save_memory(content, tags, session_id, source=source)
    if not memory_id:
        return None

    vec = embedding.embed_text(content)
    if vec:
        emb_json = embedding.vector_to_json(vec)
        db.save_memory_embedding(memory_id, emb_json, content)
        add_to_cache(memory_id, vec, content, tags, session_id)
        logger.info("记忆已存储+向量化: id=%s, session=%s, content=%s...", memory_id, session_id[:20], content[:30])

    return memory_id


# ============ 对话原文切块索引 ============
def index_conversation_chunks(session_id):
    """把会话中新增的消息按 3 轮一组切块、向量化、入库（增量且幂等，可重复调用）"""
    if not session_id:
        return 0
    last_id = db.get_chunks_last_indexed_id(session_id)
    rows = db.get_messages_with_id(session_id, after_id=last_id)
    if not rows:
        return 0

    count = 0
    buffer = []
    for r in rows:
        buffer.append(r)
        if len(buffer) >= CHUNK_ROUNDS * 2:
            count += _flush_chunk(session_id, buffer)
            buffer = []
    if buffer:
        count += _flush_chunk(session_id, buffer)
    return count


def _flush_chunk(session_id, msgs):
    """把一组消息拼成原文块并向量化入库"""
    lines = []
    for m in msgs:
        role = "用户" if m["role"] == "user" else "AI"
        content = (m["content"] or "").strip()
        if content:
            lines.append(f"{role}：{content}")
    if not lines:
        return 0

    content = "\n".join(lines)
    start_id = msgs[0]["id"]
    end_id = msgs[-1]["id"]

    chunk_id = db.save_conversation_chunk(session_id, content, start_id, end_id)
    vec = embedding.embed_text(content)
    if vec:
        db.save_chunk_embedding(chunk_id, embedding.vector_to_json(vec), content)
        add_chunk_to_cache(chunk_id, vec, content, session_id)
    logger.info("对话原文块已索引: chunk=%s, 消息数=%d, session=%s", chunk_id, len(msgs), session_id[:20])
    return 1


# ============ 自动事实提取（已停用，保留函数以防历史调用） ============
def extract_facts_from_conversation(user_text, assistant_text, llm_call_func):
    """用 LLM 从一轮对话中提取关键事实（有损+易幻觉，已废弃，仅保留定义）"""
    conversation = f"用户：{user_text}\n助手：{assistant_text}"

    prompt = f"""从以下对话中提取2-4条关于用户的关键事实。
每条事实一句话，简洁明确，例如：
- 用户喜欢吃火锅
- 用户养了一只猫叫小花
- 用户下周要去上海出差

只输出事实列表，每行以"- "开头，不要编号，不要其他内容。如果没有值得记住的事实，输出"无"。

对话：
{conversation}"""

    try:
        messages = [{"role": "user", "content": prompt}]
        result = llm_call_func(messages)
        if not result or result.strip() == "无":
            return []

        facts = []
        for line in result.strip().split("\n"):
            line = line.strip()
            if line.startswith("- "):
                facts.append(line[2:].strip())
            elif line.startswith("· "):
                facts.append(line[2:].strip())
        return facts[:4]
    except Exception as e:
        logger.error("事实提取失败: %s", e)
        return []


def auto_extract_and_store(user_text, assistant_text, llm_call_func, session_id=""):
    """自动提取事实并存储（已废弃：会把角色扮演剧情当真，产生脏记忆；chat.py 已停用）"""
    return 0
