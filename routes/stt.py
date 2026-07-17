"""语音识别路由 - 使用 Dashscope qwen3-asr-flash"""
import os
import base64
import tempfile
import subprocess
import logging
import json

import aiohttp
from aiohttp import web

import config

logger = logging.getLogger("stt")

DASHSCOPE_ASR_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
ASR_MODEL = "qwen3-asr-flash"


async def speech_to_text(request):
    """接收音频 blob → ffmpeg 转 WAV → Dashscope ASR → 返回文字"""
    api_key = config.DASHSCOPE_API_KEY
    if not api_key:
        return web.json_response({"error": "未配置 DASHSCOPE_API_KEY"}, status=500)

    reader = await request.multipart()
    field = await reader.next()
    if not field or field.name != "audio":
        return web.json_response({"error": "缺少 audio 字段"}, status=400)

    audio_data = await field.read(decode=False)

    if not audio_data or len(audio_data) < 1000:
        return web.json_response({"error": "音频数据过短"}, status=400)

    # 写入临时 WebM 文件
    webm_path = None
    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            webm_path = f.name
            f.write(audio_data)

        wav_path = webm_path.replace(".webm", ".wav")

        # ffmpeg 转换: WebM → WAV (16kHz, mono, 16-bit PCM)
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", webm_path,
                "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
                "-f", "wav", wav_path
            ],
            capture_output=True, timeout=30
        )
        if result.returncode != 0:
            logger.error("ffmpeg 转换失败: %s", result.stderr.decode("utf-8", errors="replace"))
            return web.json_response({"error": "音频格式转换失败"}, status=500)

        # 读取 WAV 并 base64 编码
        with open(wav_path, "rb") as f:
            wav_bytes = f.read()
        encoded = f"data:audio/wav;base64,{base64.b64encode(wav_bytes).decode('utf-8')}"

        # 调用 Dashscope OpenAI 兼容 ASR 接口
        payload = {
            "model": ASR_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": encoded}}
                    ]
                }
            ],
            "stream": False
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(DASHSCOPE_ASR_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error("Dashscope ASR 错误 (%s): %s", resp.status, error_text)
                    return web.json_response({"error": f"语音识别失败: {resp.status}"}, status=500)

                result_data = await resp.json()
                text = result_data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

                if not text:
                    return web.json_response({"error": "未识别到语音内容"}, status=400)

                logger.info("语音识别结果: %s", text)
                return web.json_response({"text": text})

    except subprocess.TimeoutExpired:
        logger.error("ffmpeg 转换超时")
        return web.json_response({"error": "音频处理超时"}, status=500)
    except aiohttp.ClientError as e:
        logger.error("Dashscope 请求失败: %s", e)
        return web.json_response({"error": "语音识别服务请求失败"}, status=500)
    except Exception as e:
        logger.error("语音识别异常: %s", e)
        return web.json_response({"error": f"语音识别异常: {str(e)}"}, status=500)
    finally:
        for path in [webm_path, wav_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
