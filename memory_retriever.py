"""记忆检索模块 — 事实提取 + 向量语义检索"""
import asyncio
import logging

import db
import embedding

logger = logging.getLogger("retriever")


# 内存中的向量缓存: [{id, memory_id, embedding, content, tags, session_id}]
_vector_cache = []
_cache_loaded = False


def preload_cache():
    """启动时预加载所有向量到内存"""
    global _vector_cache, _cache_loaded
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
        _cache_loaded = True
        logger.info("向量缓存预加载完成: %d 条", len(_vector_cache))
    except Exception as e:
        logger.error("向量缓存预加载失败: %s", e)
        _cache_loaded = True


def add_to_cache(memory_id, embedding_vec, content, tags="", session_id=""):
    """新增向量到缓存"""
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


def remove_from_cache(memory_id):
    """从缓存中删除指定记忆的向量"""
    global _vector_cache
    _vector_cache = [v for v in _vector_cache if v["memory_id"] != memory_id]


def retrieve_relevant_memories(query, session_id="", top_k=5, min_score=0.3):
    """语义检索：返回当前会话中与 query 最相关的 top_k 条记忆"""
    if not _vector_cache:
        return []

    query_vec = embedding.embed_text(query)
    if not query_vec:
        return []

    # 按 session_id 过滤：只检索当前会话的记忆 + 无 session_id 的全局记忆
    candidates = [
        v for v in _vector_cache
        if v["session_id"] == session_id or v["session_id"] == ""
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


def build_memory_context(query, session_id="", top_k=5):
    """构建注入系统提示的记忆文本（按会话隔离）"""
    memories = retrieve_relevant_memories(query, session_id, top_k)
    if not memories:
        return ""

    lines = []
    for m in memories:
        lines.append(f"- {m['content']}")
    return "\n".join(lines)


def store_memory_with_embedding(content, tags="", session_id=""):
    """存储记忆并生成向量"""
    memory_id = db.save_memory(content, tags, session_id)
    if not memory_id:
        return None

    vec = embedding.embed_text(content)
    if vec:
        emb_json = embedding.vector_to_json(vec)
        db.save_memory_embedding(memory_id, emb_json, content)
        add_to_cache(memory_id, vec, content, tags, session_id)
        logger.info("记忆已存储+向量化: id=%s, session=%s, content=%s...", memory_id, session_id[:20], content[:30])

    return memory_id


def extract_facts_from_conversation(user_text, assistant_text, llm_call_func):
    """用 LLM 从一轮对话中提取关键事实"""
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
    """自动提取事实并存储（后台调用，不阻塞主流程）"""
    facts = extract_facts_from_conversation(user_text, assistant_text, llm_call_func)
    if not facts:
        return 0

    count = 0
    for fact in facts:
        if fact and len(fact) > 2:
            store_memory_with_embedding(fact, session_id=session_id)
            count += 1

    if count > 0:
        logger.info("自动提取并存储了 %d 条事实", count)
    return count
