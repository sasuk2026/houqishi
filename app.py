"""houqishi - Flask Web 服务。

功能：
  - 视频批量提取音频（mp3/flac/wav，可选转换后直接合并）
  - 多音频按间隔合成（上传文件 + 文件库链接 混合排序）
  - 导入已有音频生成链接
  - 所有产物落盘持久化，返回可分享的 /f/<id> 链接

接口：
  GET    /                 首页
  GET    /health           健康检查
  GET    /f/<fid>          播放/下载已生成的文件（?download=1 强制下载）
  GET    /api/files        文件库列表
  DELETE /api/files/<fid>  删除文件
  POST   /api/video-to-mp3 批量视频转音频（可选转换后合并）
  POST   /api/merge        多音频合成
  POST   /api/import       导入音频生成链接
"""
import json
import os
import re
import shutil
import tempfile
import time
import uuid

from flask import Flask, jsonify, render_template, request, send_from_directory

from media_utils import (
    FORMAT_EXT,
    SUPPORTED_FORMATS,
    MediaError,
    merge_audio_files,
    probe_duration,
    video_to_audio,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 单次请求最大 200MB

WORK_DIR = os.path.join(tempfile.gettempdir(), "houqishi_jobs")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.environ.get("HOUQISHI_STORAGE", os.path.join(BASE_DIR, "storage"))
STORAGE_FILES = os.path.join(STORAGE_DIR, "files")
INDEX_FILE = os.path.join(STORAGE_DIR, "index.json")
FILE_TTL_HOURS = 48  # 文件保留时长

VIDEO_EXT = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv",
    ".m4v", ".ts", ".mpeg", ".mpg", ".3gp", ".gif",
}
AUDIO_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga",
    ".flac", ".wma", ".opus", ".amr",
}
AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/x-wav",
    ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".opus": "audio/ogg", ".wma": "audio/x-ms-wma",
    ".amr": "audio/amr",
}
VIDEO_MIME = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo", ".webm": "video/webm", ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv", ".m4v": "video/mp4", ".ts": "video/mp2t",
    ".mpeg": "video/mpeg", ".mpg": "video/mpeg", ".3gp": "video/3gpp",
}
FILE_MIME = {**AUDIO_MIME, **VIDEO_MIME}


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _safe_display_name(name: str) -> str:
    """清理展示用文件名：去掉路径分隔符与控制字符，保留中文等 Unicode。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", name or "").strip()
    return cleaned[:80]


def _get_format(default: str = "flac") -> str:
    fmt = (request.form.get("format") or request.args.get("format") or default).lower()
    if fmt not in SUPPORTED_FORMATS:
        raise MediaError(f"不支持的输出格式：{fmt}（可选 mp3 / flac / wav）")
    return fmt


# ---------------- 持久化存储 ----------------

def _load_index() -> list:
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_index(items: list) -> None:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX_FILE)


def _file_info(item: dict) -> dict:
    return {
        "id": item["id"],
        "name": item["name"],
        "url": f"/f/{item['id']}{item['ext']}",
        "size": item.get("size"),
        "duration": item.get("duration"),
        "kind": item.get("kind"),
        "createdAt": item.get("createdAt"),
    }


def _store_file(src_path: str, name: str, kind: str) -> dict:
    """把处理结果移入持久化目录并登记，返回文件信息（含链接）。

    保留文件真实扩展名（音频或视频均可），不再强制归入音频类型。
    """
    ext = _ext(name)
    if not ext:
        ext = ".bin"
    os.makedirs(STORAGE_FILES, exist_ok=True)

    items = _load_index()
    existing = {it["id"] for it in items}
    while True:
        fid = uuid.uuid4().hex[:8]
        if fid not in existing:
            break

    dst = os.path.join(STORAGE_FILES, fid + ext)
    shutil.move(src_path, dst)

    info = {
        "id": fid,
        "name": name,
        "ext": ext,
        "size": os.path.getsize(dst),
        "duration": probe_duration(dst),
        "kind": kind,
        "createdAt": int(time.time()),
    }
    items.append(info)
    _save_index(items)
    return _file_info(info)


def _find_file(fid: str):
    for it in _load_index():
        if it["id"] == fid:
            return it
    return None


def _cleanup_storage(max_age_hours: int = FILE_TTL_HOURS) -> None:
    now = time.time()
    items = _load_index()
    kept, changed = [], False
    for it in items:
        age = now - it.get("createdAt", now)
        if age > max_age_hours * 3600:
            try:
                os.remove(os.path.join(STORAGE_FILES, it["id"] + it["ext"]))
            except OSError:
                pass
            changed = True
        else:
            kept.append(it)
    if changed:
        _save_index(kept)


def _cleanup_stale_jobs(max_age_hours: int = 2) -> None:
    try:
        now = time.time()
        for name in os.listdir(WORK_DIR):
            p = os.path.join(WORK_DIR, name)
            if os.path.isdir(p) and now - os.path.getmtime(p) > max_age_hours * 3600:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


try:
    os.makedirs(STORAGE_FILES, exist_ok=True)
except OSError:
    pass


# ---------------- 页面路由 ----------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/f/<fid>")
def serve_file(fid: str):
    base, _ext_in_url = os.path.splitext(fid)
    item = _find_file(base)
    if not item:
        return "文件不存在或已过期", 404
    filename = base + item["ext"]
    path = os.path.join(STORAGE_FILES, filename)
    if not os.path.isfile(path):
        return "文件不存在或已过期", 404
    as_attachment = request.args.get("download") in ("1", "true", "yes")
    return send_from_directory(
        STORAGE_FILES, filename,
        mimetype=FILE_MIME.get(item["ext"], "application/octet-stream"),
        as_attachment=as_attachment,
        download_name=item["name"],
    )


# ---------------- 文件库 API ----------------

@app.get("/api/files")
def api_files():
    _cleanup_storage()
    items = _load_index()
    items.sort(key=lambda x: x.get("createdAt", 0), reverse=True)
    return jsonify({"ok": True, "files": [_file_info(it) for it in items]})


@app.delete("/api/files/<fid>")
def api_delete_file(fid: str):
    items = _load_index()
    item = next((it for it in items if it["id"] == fid), None)
    if not item:
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    try:
        os.remove(os.path.join(STORAGE_FILES, fid + item["ext"]))
    except OSError:
        pass
    _save_index([it for it in items if it["id"] != fid])
    return jsonify({"ok": True})


# ---------------- 视频转音频（批量 + 可选合并） ----------------

@app.post("/api/video-to-mp3")
def api_video_to_mp3():
    uploads = [f for f in (request.files.getlist("videos") + request.files.getlist("video")) if f and f.filename]
    ids = [s.strip() for s in (request.form.getlist("ids") or []) if s.strip()]
    if not uploads and not ids:
        return jsonify({"ok": False, "error": "请先上传或选择视频文件"}), 400
    for f in uploads:
        if _ext(f.filename) not in VIDEO_EXT:
            return jsonify({"ok": False, "error": f"不支持的视频格式：{f.filename}"}), 400
    for vid in ids:
        item = _find_file(vid)
        if not item:
            return jsonify({"ok": False, "error": f"文件库中不存在：{vid}"}), 400
        if item["ext"] not in VIDEO_EXT:
            return jsonify({"ok": False, "error": f"该文件不是视频：{item['name']}"}), 400

    try:
        fmt = _get_format()
    except MediaError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    merge_after = request.form.get("merge") in ("1", "true", "yes", "on")
    gaps_raw = request.form.getlist("gaps")

    os.makedirs(WORK_DIR, exist_ok=True)
    _cleanup_stale_jobs()
    workdir = tempfile.mkdtemp(prefix="vid_", dir=WORK_DIR)

    try:
        inputs = []  # (path, base_name)
        for f in uploads:
            in_path = os.path.join(workdir, f"in_{len(inputs):04d}" + _ext(f.filename))
            f.save(in_path)
            base = _safe_display_name(os.path.splitext(f.filename)[0]) or f"video_{len(inputs) + 1}"
            inputs.append((in_path, base))
        for vid in ids:
            item = _find_file(vid)
            in_path = os.path.join(STORAGE_FILES, vid + item["ext"])
            base = _safe_display_name(os.path.splitext(item["name"])[0]) or f"video_{len(inputs) + 1}"
            inputs.append((in_path, base))

        results = []
        for i, (in_path, base) in enumerate(inputs):
            tmp_out = os.path.join(workdir, f"out_{i:04d}" + FORMAT_EXT[fmt])
            video_to_audio(in_path, tmp_out, fmt)
            results.append(_store_file(tmp_out, base + FORMAT_EXT[fmt], "video"))

        merged = None
        if merge_after and len(results) > 1:
            gaps = []
            for i in range(len(results)):
                g = 0.0
                if i < len(gaps_raw) and gaps_raw[i] not in ("", None):
                    try:
                        g = max(0.0, float(gaps_raw[i]))
                    except ValueError:
                        g = 0.0
                gaps.append(g)
            merged_out = os.path.join(workdir, "merged" + FORMAT_EXT[fmt])
            stored_paths = [
                os.path.join(STORAGE_FILES, r["id"] + _stored_ext(r["id"]))
                for r in results
            ]
            merge_audio_files(stored_paths, gaps, merged_out, fmt)
            merged = _store_file(merged_out, "houqishi_merged" + FORMAT_EXT[fmt], "merge")
    except MediaError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"ok": False, "error": "服务器内部错误，请重试"}), 500
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return jsonify({"ok": True, "files": results, "merged": merged})


# ---------------- 通用上传（视频/音频 原文件落盘，返回可引用 id） ----------------

@app.post("/api/upload")
def api_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "请选择文件"}), 400

    ext = _ext(f.filename)
    custom = (request.form.get("name") or "").strip()
    if custom:
        name = _safe_display_name(custom)
        if ext and not name.lower().endswith(ext):
            name += ext
    else:
        name = _safe_display_name(f.filename) or ("file" + (ext or ""))
    if ext and not name.lower().endswith(ext):
        name += ext

    kind = "video_src" if ext in VIDEO_EXT else "audio_src"

    os.makedirs(WORK_DIR, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="up_", dir=WORK_DIR)
    try:
        tmp = os.path.join(workdir, "raw" + (ext or ""))
        f.save(tmp)
        info = _store_file(tmp, name, kind)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"ok": False, "error": "服务器内部错误，请重试"}), 500
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return jsonify({"ok": True, "file": info})


def _stored_ext(fid: str) -> str:
    item = _find_file(fid)
    return item["ext"] if item else ".mp3"


# ---------------- 多音频合成 ----------------

@app.post("/api/merge")
def api_merge():
    try:
        fmt = _get_format()
    except MediaError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    for f in uploads:
        if _ext(f.filename) not in AUDIO_EXT:
            return jsonify({"ok": False, "error": f"不支持的音频格式：{f.filename}"}), 400

    # order: JSON 数组，[{type: upload|id, ref: "0"|文件id, gap: 秒数}]
    sources = []  # (path, gap)
    order_raw = request.form.get("order")
    os.makedirs(WORK_DIR, exist_ok=True)
    _cleanup_stale_jobs()
    workdir = tempfile.mkdtemp(prefix="mrg_", dir=WORK_DIR)

    try:
        if order_raw:
            try:
                order = json.loads(order_raw)
                if not isinstance(order, list):
                    raise ValueError
            except ValueError:
                return jsonify({"ok": False, "error": "合成列表格式错误"}), 400

            for entry in order:
                if not isinstance(entry, dict):
                    return jsonify({"ok": False, "error": "合成列表格式错误"}), 400
                try:
                    gap = max(0.0, float(entry.get("gap", 0)))
                except (TypeError, ValueError):
                    gap = 0.0
                etype = entry.get("type")
                ref = str(entry.get("ref", ""))
                if etype == "upload":
                    idx = int(ref) if ref.isdigit() else -1
                    if idx < 0 or idx >= len(uploads):
                        return jsonify({"ok": False, "error": "上传文件引用错误"}), 400
                    p = os.path.join(workdir, f"up_{len(sources):04d}" + _ext(uploads[idx].filename))
                    uploads[idx].save(p)
                    sources.append((p, gap))
                elif etype == "id":
                    item = _find_file(ref)
                    if not item:
                        return jsonify({"ok": False, "error": f"文件库中不存在：{ref}"}), 400
                    sources.append((os.path.join(STORAGE_FILES, ref + item["ext"]), gap))
                else:
                    return jsonify({"ok": False, "error": "合成列表格式错误"}), 400
        else:
            # 兼容旧调用方式：files + gaps 平行列表
            gaps_raw = request.form.getlist("gaps")
            for i, f in enumerate(uploads):
                g = 0.0
                if i < len(gaps_raw) and gaps_raw[i] not in ("", None):
                    try:
                        g = max(0.0, float(gaps_raw[i]))
                    except ValueError:
                        g = 0.0
                p = os.path.join(workdir, f"up_{i:04d}" + _ext(f.filename))
                f.save(p)
                sources.append((p, g))

        if not sources:
            return jsonify({"ok": False, "error": "请至少添加一个音频"}), 400

        out_path = os.path.join(workdir, "merged" + FORMAT_EXT[fmt])
        merge_audio_files([p for p, _ in sources], [g for _, g in sources], out_path, fmt)
        info = _store_file(out_path, "houqishi_merged" + FORMAT_EXT[fmt], "merge")
    except MediaError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"ok": False, "error": "服务器内部错误，请重试"}), 500
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return jsonify({"ok": True, "file": info})


# ---------------- 导入音频 ----------------

@app.post("/api/import")
def api_import():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "请先选择音频文件"}), 400

    ext = _ext(f.filename)
    if ext not in AUDIO_EXT:
        return jsonify({"ok": False, "error": f"不支持的音频格式：{f.filename}"}), 400

    custom = (request.form.get("name") or "").strip()
    if custom:
        name = (_safe_display_name(custom) or "audio") + ext
    else:
        name = (_safe_display_name(f.filename) or "audio")
        if not name.lower().endswith(ext):
            name += ext

    os.makedirs(WORK_DIR, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="imp_", dir=WORK_DIR)
    try:
        tmp_path = os.path.join(workdir, "import" + ext)
        f.save(tmp_path)
        info = _store_file(tmp_path, name, "import")
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"ok": False, "error": "服务器内部错误，请重试"}), 500
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return jsonify({"ok": True, "file": info})


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"ok": False, "error": "文件太大，单次请求上限 200MB"}), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
