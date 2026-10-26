# houqishi 🎵

一个简单粗暴的音频小工具：**视频拖进去，导出 MP3**；**多个 MP3 按你设置的间隔时间，合成一段 MP3**。

基于 Flask + ffmpeg 构建，Docker 化部署，一条命令即可上线。

## ✨ 功能

| 功能 | 说明 |
| --- | --- |
| 🎬 视频转 MP3 | 上传视频（MP4 / MOV / MKV / AVI / WebM 等），提取音轨并编码为 192kbps MP3 |
| 🎵 多音频合成 | 按顺序上传多个音频，可为**每一个**音频单独设置「间隔秒数」（支持小数，如 1.5） |
| 🧹 自动清理 | 处理完成后自动删除临时文件，过期任务目录定期回收 |

合成规则：第 N 个音频播放结束后，插入 `间隔` 秒的静音，再播放下一个；**最后一个音频的间隔不生效**（末尾不会附加静音）。

## 🛠 技术栈

- Python 3.11 + Flask 3
- ffmpeg / ffprobe（libmp3lame 编码）
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

上传视频，返回提取出的 MP3 文件（`multipart/form-data`）。

```bash
curl -F "video=@movie.mp4" http://127.0.0.1:5000/api/video-to-mp3 -o audio.mp3
```

### `POST /api/merge-mp3`

上传多个音频 + 每个音频的间隔秒数，返回合成后的 MP3。
`files` 与 `gaps` 按下标一一对应，`gaps[i]` 是第 `i` 个音频之后的静音秒数。

```bash
curl -F "files=@a.mp3" -F "gaps=1.5" \
     -F "files=@b.mp3" -F "gaps=2" \
     -F "files=@c.mp3" -F "gaps=0" \
     http://127.0.0.1:5000/api/merge-mp3 -o merged.mp3
```

### `GET /health`

健康检查，返回 `{"status": "ok"}`。

## ☁️ 部署到 Render

仓库自带 `Dockerfile` 与 `render.yaml`：

1. 把本仓库推送到 GitHub；
2. Render 控制台 → New → **Web Service** → 选择该仓库；
3. Runtime 选 **Docker**，实例类型 Free，点击 Create；
4. Render 会自动构建镜像并部署，`/health` 作为健康检查路径。

或用 Render API（蓝本方式）：

```bash
curl -X POST https://api.render.com/v1/blueprints \
  -H "Authorization: Bearer <RENDER_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"repo": "https://github.com/<你的用户名>/houqishi", "name": "houqishi"}'
```

> ⚠️ Render 免费实例会在闲置约 15 分钟后休眠，下次访问需要几十秒冷启动，属正常现象。

## 📁 目录结构

```
houqishi/
├── app.py               # Flask 服务与接口
├── media_utils.py       # ffmpeg 媒体处理核心逻辑
├── templates/
│   └── index.html       # 单页前端（上传 / 合成）
├── requirements.txt
├── Dockerfile
├── render.yaml
└── README.md
```

## 📌 限制

- 单次请求最大 **200MB**（可在 `app.py` 的 `MAX_CONTENT_LENGTH` 调整）；
- 免费实例内存有限，超大视频可能处理失败，建议 100MB 以内的素材。
