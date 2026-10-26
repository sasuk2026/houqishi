"""houqishi - Flask Web 服务。

接口：
  GET  /                 首页（上传/合成页面）
  GET  /health           健康检查
  POST /api/video-to-mp3 上传视频 -> 返回提取出的 MP3
  POST /api/merge-mp3    上传多个音频 + 每个音频的间隔秒数 -> 返回合成 MP3
"""
import os
import shutil
import tempfile
import time

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from media_utils import MediaError, merge_audio_files, video_to_mp3

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 单次请求最大 200MB

WORK_DIR = os.path.join(tempfile.gettempdir(), "houqishi_jobs")

VIDEO_EXT = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv",
    ".m4v", ".ts", ".mpeg", ".mpg", ".3gp", ".gif",
}
AUDIO_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga",
    ".flac", ".wma", ".opus", ".amr", ".mp4",
}


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _cleanup_response(resp, path: str):
    """响应发送完成后删除临时目录。"""
    @resp.call_on_close
    def _cleanup():
        shutil.rmtree(path, ignore_errors=True)
    return resp


def _cleanup_stale_jobs(max_age_hours: int = 2):
    try:
        now = time.time()
        for name in os.listdir(WORK_DIR):
            p = os.path.join(WORK_DIR, name)
            if os.path.isdir(p) and now - os.path.getmtime(p) > max_age_hours * 3600:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/video-to-mp3")
def api_video_to_mp3():
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify({"error": "请先上传视频文件"}), 400

    ext = _ext(file.filename)
    if ext not in VIDEO_EXT:
        return jsonify({"error": f"不支持的视频格式：{ext or '未知'}"}), 400

    os.makedirs(WORK_DIR, exist_ok=True)
    _cleanup_stale_jobs()
    workdir = tempfile.mkdtemp(prefix="vid_", dir=WORK_DIR)

    try:
        in_path = os.path.join(workdir, "input" + ext)
        file.save(in_path)

        base = secure_filename(os.path.splitext(file.filename)[0]) or "audio"
        out_name = base + ".mp3"
        out_path = os.path.join(workdir, out_name)
        video_to_mp3(in_path, out_path)
    except MediaError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"error": "服务器内部错误，请重试"}), 500

    resp = send_from_directory(workdir, out_name, as_attachment=True)
    return _cleanup_response(resp, workdir)


@app.post("/api/merge-mp3")
def api_merge_mp3():
    files = request.files.getlist("files")
    gaps_raw = request.form.getlist("gaps")

    entries = []  # (FileStorage, gap_seconds)
    for i, f in enumerate(files):
        if not f or not f.filename:
            continue
        ext = _ext(f.filename)
        if ext not in AUDIO_EXT:
            return jsonify({"error": f"不支持的音频格式：{f.filename}"}), 400
        gap = 0.0
        if i < len(gaps_raw) and gaps_raw[i] not in ("", None):
            try:
                gap = float(gaps_raw[i])
            except ValueError:
                return jsonify({"error": "间隔时间必须是数字"}), 400
        entries.append((f, max(0.0, gap)))

    if not entries:
        return jsonify({"error": "请至少上传一个音频文件"}), 400

    os.makedirs(WORK_DIR, exist_ok=True)
    _cleanup_stale_jobs()
    workdir = tempfile.mkdtemp(prefix="mrg_", dir=WORK_DIR)

    try:
        input_paths = []
        for i, (f, _gap) in enumerate(entries):
            p = os.path.join(workdir, f"in_{i:04d}" + _ext(f.filename))
            f.save(p)
            input_paths.append(p)

        out_path = os.path.join(workdir, "houqishi_merged.mp3")
        merge_audio_files(input_paths, [g for _, g in entries], out_path)
    except MediaError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"error": "服务器内部错误，请重试"}), 500

    resp = send_from_directory(workdir, "houqishi_merged.mp3", as_attachment=True)
    return _cleanup_response(resp, workdir)


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "文件太大，单次请求上限 200MB"}), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
