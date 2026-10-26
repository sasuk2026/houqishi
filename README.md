# houqishi 🎵

一个简单粗暴的音频小工具：**视频拖进去，导出音频**；**多个音频按你设置的间隔时间，合成一段**。支持 MP3 / FLAC / WAV 三种输出格式，**默认 FLAC 无损**。

基于 Flask + ffmpeg 构建，Docker 化部署。

## ✨ 功能

| 功能 | 说明 |
| --- | --- |
| 🎬 视频转音频 | 上传视频（MP4 / MOV / MKV / AVI / WebM 等），提取音轨。FLAC / WAV 为无损输出，保留源采样率与声道 |
| 🎵 多音频合成 | 按顺序上传多个音频，可为**每一个**音频单独设置「间隔秒数」（支持小数，如 1.5） |
| 🎚 三种输出格式 | FLAC 无损压缩（默认）/ WAV 未压缩无损 / MP3 192k（体积小） |
| 🧹 自动清理 | 处理完成后自动删除临时文件，过期任务目录定期回收 |

合成规则：第 N 个音频播放结束后，插入 `间隔` 秒的静音，再播放下一个；**最后一个音频的间隔不生效**（末尾不会附加静音）。

> 无损说明：合成全程走 PCM 中间链（解码 → 统一 44.1kHz/16bit/立体声 → 无损拼接 → FLAC/WAV 编码），除源文件本身的编码损失外，不引入任何额外音质损失。

## 🛠 技术栈

- Python 3.11 + Flask 3
- ffmpeg / ffprobe（libmp3lame + flac 编码）
- gunicorn 作为生产服务器
- Docker（Debian slim + 官方源 ffmpeg）

## 🚀 本地运行

```bash
# 1. 安装依赖（需预先安装 ffmpeg：https://ffmpeg.org/download.html）
pip install -r requirements.txt

# 2. 启动
python3 app.py
# 打开 http://127.0.0.1:5000
```

## 📡 API

### `POST /api/video-to-mp3`

上传视频，返回提取出的音频文件（`multipart/form-data`）。

| 参数 | 说明 |
| --- | --- |
| `video` | 视频文件（必填） |
| `format` | 输出格式：`flac`（默认，无损）/ `wav`（无损）/ `mp3` |

```bash
# 无损 FLAC（默认）
curl -F "video=@movie.mp4" http://127.0.0.1:5000/api/video-to-mp3 -o audio.flac

# MP3
curl -F "video=@movie.mp4" -F "format=mp3" http://127.0.0.1:5000/api/video-to-mp3 -o audio.mp3
```

### `POST /api/merge-mp3`

上传多个音频 + 每个音频的间隔秒数，返回合成后的音频。
`files` 与 `gaps` 按下标一一对应，`gaps[i]` 是第 `i` 个音频之后的静音秒数。

| 参数 | 说明 |
| --- | --- |
| `files` | 音频文件，可多次传入（必填） |
| `gaps` | 与 files 对应的间隔秒数（默认 0） |
| `format` | 输出格式：`flac`（默认，无损）/ `wav`（无损）/ `mp3` |

```bash
curl -F "files=@a.mp3" -F "gaps=1.5" \
     -F "files=@b.mp3" -F "gaps=2" \
     -F "files=@c.mp3" -F "gaps=0" \
     -F "format=flac" \
     http://127.0.0.1:5000/api/merge-mp3 -o merged.flac
```

### `GET /health`

健康检查，返回 `{"status": "ok"}`。

## ☁️ 部署到 Render

仓库自带 `Dockerfile` 与 `render.yaml`：

1. 把本仓库推送到 GitHub；
2. Render 控制台 → New → **Web Service** → 选择该仓库；
3. Runtime 选 **Docker**，实例类型 Free，点击 Create；
4. Render 会自动构建镜像并部署，`/health` 作为健康检查路径。

推送新提交到 `main` 分支会自动触发重新部署（`autoDeployTrigger: commit`）。

> ⚠️ Render 免费实例会在闲置约 15 分钟后休眠，下次访问需要几十秒冷启动，属正常现象。

## 📁 目录结构

```
houqishi/
├── app.py               # Flask 服务与接口
├── media_utils.py       # ffmpeg 媒体处理核心逻辑
├── templates/
│   └── index.html       # 单页前端（上传 / 合成 / 格式选择）
├── requirements.txt
├── Dockerfile
├── render.yaml
└── README.md
```

## 📌 限制

- 单次请求最大 **200MB**（可在 `app.py` 的 `MAX_CONTENT_LENGTH` 调整）；
- 合成输出固定 44.1kHz / 16bit / 立体声（CD 级无损标准）；
- 免费实例内存有限，超大视频可能处理失败，建议 100MB 以内的素材。
