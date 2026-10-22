"""本地向量嵌入模块 — 使用 sentence-transformers"""
import json
import math
import os
import threading
import logging
import sys

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("embedding")

# 禁用 HuggingFace 下载进度条和长重试，避免卡死
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")

_model = None
_model_lock = threading.Lock()
_model_loading = False
_model_ready = False
_load_error = None


def _candidate_model_paths():
    """按优先级列出候选模型路径。

    v0.13.2：之前只有「总线 embedding」一个目录，导致换模型必须原地覆盖那个目录
    ——而总线是多项目共享的，动了会影响别的项目。现在改成候选列表：
    环境变量 EMBEDDING_MODEL_PATH → 总线 embedding-zh → 总线 embedding → 项目本地 → HF 名字。
    这样换模型只需放一个新目录，不用碰总线原有目录。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    bus = os.path.join("E:" + os.sep, "bus", "models")
    paths = []
    env = os.environ.get("EMBEDDING_MODEL_PATH") or os.environ.get("EMBEDDING_MODEL")
    if env:
        paths.append(env)
    # 中文优先：embedding-zh 存在就用它（bge-small-zh-v1.5，词表覆盖 99.8% 中文字）
    paths.append(os.path.join(bus, "embedding-zh"))
    paths.append(os.path.join(bus, "embedding"))
    paths.append(os.path.join(here, "models", "embedding"))
    return paths


def load_model():
    """懒加载 sentence-transformers 模型（线程安全）"""
    global _model, _model_loading, _model_ready, _load_error, _model_path, _model_dim
    if _model_ready:
        return _model
    with _model_lock:
        if _model_ready:
            return _model
        if _model_loading:
            return None
        if _load_error:
            logger.warning("嵌入模型加载失败: %s，跳过向量化", _load_error)
            return None
        _model_loading = True
        try:
            from sentence_transformers import SentenceTransformer

            model_path, is_local = None, False
            for cand in _candidate_model_paths():
                if cand and os.path.exists(cand):
                    model_path, is_local = cand, True
                    break

            if is_local:
                logger.info("从本地加载嵌入模型: %s", model_path)
                _model = SentenceTransformer(model_path, trust_remote_code=True)
            else:
                # 没有本地模型 → 用中文模型名兜底（不要退回英文模型，中文会大面积变 [UNK]）
                logger.info("未找到本地模型，加载 bge-small-zh-v1.5 ...")
                _model = SentenceTransformer("BAAI/bge-small-zh-v1.5", trust_remote_code=True)

            _model_path = model_path or "BAAI/bge-small-zh-v1.5"
            try:
                # 新版 sentence-transformers 改了方法名，两个名字都试一遍
                fn = getattr(_model, "get_embedding_dimension", None) or \
                    getattr(_model, "get_sentence_embedding_dimension", None)
                _model_dim = int(fn()) if fn else 0
            except Exception:
                _model_dim = 0
            _model_ready = True
            logger.info("模型加载完成: %s（维度 %s）", _model_path, _model_dim)
        except Exception as e:
            _load_error = str(e)
            logger.error("模型加载失败（将跳过向量化功能）: %s", e)
            _model = None
        finally:
            _model_loading = False
    return _model


def is_ready():
    return _model_ready


def model_info():
    """当前模型的路径与维度（诊断 / 换模型后核对用）。"""
    return {"path": _model_path, "dim": _model_dim, "ready": _model_ready}


def check_chinese_support(samples=None):
    """检测当前模型的中文识别能力（返回 [UNK] 占比）。

    为什么需要：英文模型（all-MiniLM-L6-v2）对中文几乎全是 [UNK]，
    记忆检索会静默失准——不报错、看起来正常，就是检索不准，很难发现。
    换模型后跑一下这个函数，能立刻确认新模型是不是真的认中文。
    """
    model = load_model()
    if model is None:
        return None
    if samples is None:
        samples = ["我讨厌香菜", "今天天气怎么样", "你几岁", "在吗",
                   "我害怕打雷", "我们约好下周末去爬山"]
    total, unk = 0, 0
    for s in samples:
        toks = model.tokenizer.tokenize(s)
        total += len(toks)
        unk += sum(1 for t in toks if t == "[UNK]")
    return {"total_tokens": total, "unk_tokens": unk,
            "unk_ratio": round(unk / total, 4) if total else 0.0,
            "verdict": "✓ 认得中文" if (total and unk / total < 0.1)
                       else ("⚠️ 部分丢失" if (total and unk / total < 0.4) else "✗ 不认中文")}


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
