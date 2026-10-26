"""houqishi - 媒体处理核心逻辑（视频提取音频 / 多音频按间隔合成）。

输出格式：
  mp3  - libmp3lame 192kbps（有损，体积小）
  flac - FLAC 无损压缩（默认推荐）
  wav  - PCM 16bit 无损（未压缩，体积大）
"""
import os
import shutil
import subprocess
import tempfile

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

SAMPLE_RATE = 44100
MP3_BITRATE = "192k"
FLAC_LEVEL = 8  # FLAC 压缩级别 0-8，8 = 体积最小
CMD_TIMEOUT = 1800  # 单个 ffmpeg 命令最长 30 分钟

SUPPORTED_FORMATS = ("mp3", "flac", "wav")
FORMAT_EXT = {"mp3": ".mp3", "flac": ".flac", "wav": ".wav"}


class MediaError(Exception):
    """媒体处理失败（对外返回友好信息）。"""


def _run(cmd: list) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=CMD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise MediaError("处理超时，请尝试更小的文件")
    except FileNotFoundError:
        raise MediaError("服务器缺少 ffmpeg，无法处理媒体文件")
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-400:]
        raise MediaError(f"ffmpeg 处理失败：{tail}")
    return proc


def has_audio_stream(path: str) -> bool:
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", path,
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    except Exception:
        return True  # 探测失败不阻断，交给 ffmpeg 自己报错
    return proc.returncode == 0 and proc.stdout.strip() != ""


def probe_duration(path: str):
    """返回音频时长（秒，保留两位小数），失败返回 None。"""
    cmd = [
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        if proc.returncode == 0 and proc.stdout.strip():
            return round(float(proc.stdout.strip()), 2)
    except Exception:
        pass
    return None


def _audio_encode_args(fmt: str) -> list:
    """返回对应格式的 ffmpeg 音频编码参数。"""
    if fmt == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", MP3_BITRATE]
    if fmt == "flac":
        return ["-c:a", "flac", "-compression_level", str(FLAC_LEVEL)]
    if fmt == "wav":
        return ["-c:a", "pcm_s16le"]
    raise MediaError(f"不支持的输出格式：{fmt}")


def video_to_audio(input_path: str, output_path: str, fmt: str = "flac") -> None:
    """从视频中提取音频并按指定格式输出。

    FLAC / WAV 为无损输出，保留源采样率与声道数，不做重采样。
    """
    if fmt not in SUPPORTED_FORMATS:
        raise MediaError(f"不支持的输出格式：{fmt}")
    if not has_audio_stream(input_path):
        raise MediaError("该视频没有音轨，无法提取音频")
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_path,
        "-vn",                       # 丢弃视频流
        *_audio_encode_args(fmt),
        output_path,
    ]
    _run(cmd)


def merge_audio_files(input_paths: list, gaps: list, output_path: str,
                      fmt: str = "flac",
                      sample_rate: int = SAMPLE_RATE) -> None:
    """把多个音频按顺序合成一个文件。

    gaps[i] 表示第 i 个音频播放结束后插入的静音秒数；
    最后一个音频之后的间隔会被忽略（末尾不加静音）。

    合成全程走 PCM 中间链：解码 -> 统一 44.1kHz/16bit/立体声 ->
    无损拼接 -> 编码为目标格式。FLAC / WAV 输出为无损，
    除源文件本身的编码损失外不引入任何额外音质损失。
    """
    if not input_paths:
        raise MediaError("没有待合并的音频文件")
    if fmt not in SUPPORTED_FORMATS:
        raise MediaError(f"不支持的输出格式：{fmt}")

    gaps = list(gaps or [])
    while len(gaps) < len(input_paths):
        gaps.append(0.0)

    tmp = tempfile.mkdtemp(prefix="houqishi_merge_")
    try:
        # 1. 统一采样率/声道，转成中间 WAV
        normalized = []
        for i, src in enumerate(input_paths):
            wav = os.path.join(tmp, f"norm_{i:04d}.wav")
            _run([
                FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                "-i", src,
                "-ar", str(sample_rate), "-ac", "2",
                "-c:a", "pcm_s16le", wav,
            ])
            normalized.append(wav)

        # 2. 生成每段之间的静音，并拼成 concat 列表
        segments = []
        for i, wav in enumerate(normalized):
            segments.append(wav)
            gap = float(gaps[i]) if i < len(gaps) else 0.0
            if i < len(normalized) - 1 and gap > 0:
                silence = os.path.join(tmp, f"gap_{i:04d}.wav")
                _run([
                    FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=stereo",
                    "-t", f"{gap:.3f}",
                    "-c:a", "pcm_s16le", silence,
                ])
                segments.append(silence)

        list_file = os.path.join(tmp, "list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for seg in segments:
                f.write(f"file '{seg}'\n")

        # 3. 无损拼接（PCM concat，无重编码）
        inter = os.path.join(tmp, "inter.wav")
        _run([
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c:a", "pcm_s16le", inter,
        ])

        # 4. 编码为目标格式（WAV 直接复用中间产物，零损耗）
        if fmt == "wav":
            shutil.copyfile(inter, output_path)
        else:
            _run([
                FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                "-i", inter,
                *_audio_encode_args(fmt),
                output_path,
            ])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
