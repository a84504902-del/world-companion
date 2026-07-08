"""本地向量嵌入模块 — 使用 sentence-transformers"""
import json
import math
import threading
import logging

logger = logging.getLogger("embedding")

_model = None
_model_lock = threading.Lock()
_model_loading = False
_model_ready = False


def load_model():
    """懒加载 sentence-transformers 模型（线程安全）"""
    global _model, _model_loading, _model_ready
    if _model_ready:
        return _model
    with _model_lock:
        if _model_ready:
            return _model
        if _model_loading:
            return None
        _model_loading = True
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("加载嵌入模型 all-MiniLM-L6-v2 ...")
            _model = SentenceTransformer("all-MiniLM-L6-v2")
            _model_ready = True
            logger.info("模型加载完成")
        except Exception as e:
            logger.error("模型加载失败: %s", e)
            _model = None
        finally:
            _model_loading = False
    return _model


def is_ready():
    return _model_ready


def embed_text(text):
    """单条文本嵌入，返回 list[float]"""
    model = load_model()
    if model is None:
        return []
    try:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()
    except Exception as e:
        logger.error("embed_text 失败: %s", e)
        return []


def embed_batch(texts):
    """批量文本嵌入，返回 list[list[float]]"""
    model = load_model()
    if model is None:
        return [[] for _ in texts]
    try:
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [v.tolist() for v in vecs]
    except Exception as e:
        logger.error("embed_batch 失败: %s", e)
        return [[] for _ in texts]


def cosine_similarity(a, b):
    """余弦相似度（假设向量已归一化，直接点积即可）"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))


def vector_to_json(vec):
    """向量转 JSON 字符串存储"""
    return json.dumps(vec, ensure_ascii=False)


def vector_from_json(text):
    """JSON 字符串转回向量"""
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []
