"""TTS 语音合成模块"""
import asyncio
import hashlib
import logging
import os
import tempfile

from config import TTS_PROXY_PORT, TTS_VOICE

logger = logging.getLogger("tts")


async def synthesize(text, retries=2):
    """调用 Edge TTS 合成语音，返回音频文件路径

    - 失败重试：微软侧对 Sec-MS-GEC 校验/频率限制会间歇性 403，重试常能赶上窗口
    - 失败清理：save() 失败也会留下 0 字节文件，不清掉会被当成有效音频缓存
    """
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts 未安装，请运行: pip install edge-tts")

    # 生成唯一的文件名
    text_hash = hashlib.md5(text.encode('utf-8', errors='ignore')).hexdigest()[:12]
    output_file = os.path.join(tempfile.gettempdir(), f"tts_{text_hash}.mp3")

    # 已存在且非空 → 直接复用
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        return output_file
    # 0 字节的坏文件先删掉（上次合成失败留下的）
    if os.path.exists(output_file):
        try:
            os.remove(output_file)
        except OSError:
            pass

    # 清理文本
    clean_text = text.replace("*", "").replace("**", "").replace("#", "")
    clean_text = clean_text.replace("【", "").replace("】", "").strip()

    if not clean_text:
        raise ValueError("文本为空")

    last_err = None
    for attempt in range(retries + 1):
        communicate = edge_tts.Communicate(clean_text, TTS_VOICE)
        try:
            await communicate.save(output_file)
            if os.path.getsize(output_file) > 0:
                return output_file
            raise ValueError("合成结果为空文件")
        except Exception as e:
            last_err = e
            # 清理失败留下的坏文件，否则下次 exists() 命中直接返回空音频
            if os.path.exists(output_file):
                try:
                    os.remove(output_file)
                except OSError:
                    pass
            if attempt < retries:
                logger.warning("TTS 合成失败（第 %d 次），2 秒后重试: %s", attempt + 1, e)
                await asyncio.sleep(2)
    raise last_err


async def synthesize_to_bytes(text):
    """合成语音并返回字节数据"""
    file_path = await synthesize(text)
    with open(file_path, "rb") as f:
        return f.read()


def get_audio_url(text_hash):
    """获取音频文件的 URL 路径"""
    return f"/audio/{text_hash}"
