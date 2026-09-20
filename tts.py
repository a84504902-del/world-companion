"""TTS 语音合成模块"""
import os
import json
import hashlib
import asyncio
import tempfile
from config import TTS_PROXY_PORT, TTS_VOICE


async def synthesize(text):
    """调用 Edge TTS 合成语音，返回音频文件路径"""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts 未安装，请运行: pip install edge-tts")

    # 生成唯一的文件名
    text_hash = hashlib.md5(text.encode('utf-8', errors='ignore')).hexdigest()[:12]
    output_file = os.path.join(tempfile.gettempdir(), f"tts_{text_hash}.mp3")

    # 如果已存在，直接返回
    if os.path.exists(output_file):
        return output_file

    # 清理文本
    clean_text = text.replace("*", "").replace("**", "").replace("#", "")
    clean_text = clean_text.replace("【", "").replace("】", "").strip()

    if not clean_text:
        raise ValueError("文本为空")

    # 调用 edge-tts
    communicate = edge_tts.Communicate(clean_text, TTS_VOICE)
    await communicate.save(output_file)

    return output_file


async def synthesize_to_bytes(text):
    """合成语音并返回字节数据"""
    file_path = await synthesize(text)
    with open(file_path, "rb") as f:
        return f.read()


def get_audio_url(text_hash):
    """获取音频文件的 URL 路径"""
    return f"/audio/{text_hash}"
