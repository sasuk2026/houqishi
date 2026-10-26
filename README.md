# houqishi 🎵

一个简单粗暴的音频小工具：**视频拖进去，导出音频**；**多个音频按你设置的间隔时间，合成一段**；**导入已有 MP3 也能生成链接**。所有产物都会落盘并生成可分享的 `/f/<id>` 链接。

基于 Flask + ffmpeg 构建，Docker 化部署。

## ✨ 功能

| 功能 | 说明 |
| --- | --- |
| 🎬 视频转音频（批量） | 一次选多个视频逐个提取；也可分批多次转换，之后到合成里从文件库选取再合并 |
| 🔗 转换后直接合并 | 批量转换时勾选「转换后合并」，按每个视频的间隔拼成一段 |
| 🎵 多音频合成 | 上传文件 + 文件库链接**混合排序**，每个音频单独设置间隔秒数（支持小数） |
| 📥 导入生成链接 | 导入已有 MP3 等音频，立即可分享 |
| 🎚 三种输出格式 | FLAC 无损压缩（默认）/ WAV 未压缩无损 / MP3 192k（体积小） |
| 📚 文件库 | 所有产物列表：复制链接、在线播放、下载、删除 |
| 🧹 自动清理 | 超过 48 小时的文件自动回收，也可手动删除 |

合成规则：第 N 个音频播放结束后，插入 `间隔` 秒的静音，再播放下一个；**最后一个音频的间隔不生效**（末尾不加静音）。

> 无损说明：合成全程走 PCM 中间链（解码 → 统一 44.1kHz/16bit/立体声 → 无损拼接 → FLAC/WAV 编码），除源文件本身的编码损失外，不引入任何额外音质损失。
>
> 链接说明：文件保存在 Render 临时磁盘中，**服务重新部署或超过 48 小时后链接会失效**，重要文件请及时下载到本地。

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

### `POST /api/video-to-mp3` — 批量视频转音频

| 参数 | 说明 |
| --- | --- |
| `videos` | 视频文件，可多次传入（必填） |
| `gaps` | 与 videos 对应的间隔秒数（合并时生效，默认 0） |
| `format` | `flac`（默认，无损）/ `wav`（无损）/ `mp3` |
| `merge` | `1` = 转换后合并为一段（默认 `0`） |

返回 `{ok, files: [...], merged: {...}|null}`，每个文件含 `id / name / url / size / duration`。

```bash
curl -F "videos=@a.mp4" -F "gaps=1.5" \
     -F "videos=@b.mp4" -F "gaps=2" \
     -F "format=flac" -F "merge=1" \
     http://127.0.0.1:5000/api/video-to-mp3
```

### `POST /api/merge` — 多音频合成

| 参数 | 说明 |
| --- | --- |
| `files` | 上传的音频文件（可多次） |
| `order` | JSON 数组，规定合成顺序：`[{"type":"upload","ref":"0","gap":1.5}, {"type":"id","ref":"a1b2c3d4","gap":0}]`。`upload` 的 ref 是 files 中的下标，`id` 的 ref 是文件库链接 ID |
| `format` | `flac`（默认）/ `wav` / `mp3` |

返回 `{ok, file: {...}}`。

```bash
curl -F "files=@x.mp3" -F "files=@y.mp3" \
     -F 'order=[{"type":"upload","ref":"0","gap":1},{"type":"upload","ref":"1","gap":0}]' \
     -F "format=flac" \
     http://127.0.0.1:5000/api/merge
```

### `POST /api/import` — 导入音频生成链接

| 参数 | 说明 |
| --- | --- |
| `file` | 音频文件（必填） |
| `name` | 自定义名称（可选） |

返回 `{ok, file: {...}}`。

### `GET /f/<fid>` — 访问生成的文件

浏览器可直接播放；加 `?download=1` 强制下载。

### 文件库

- `GET /api/files` — 列出所有文件
- `DELETE /api/files/<fid>` — 删除文件

### `GET /health`

健康检查，返回 `{"status": "ok"}`。

## ☁️ 部署到 Render

仓库自带 `Dockerfile` 与 `render.yaml`：

1. 把本仓库推送到 GitHub；
2. Render 控制台 → New → **Web Service** → 选择该仓库；
3. Runtime 选 **Docker**，实例类型 Free，点击 Create；
4. Render 会自动构建镜像并部署，`/health` 作为健康检查路径。

推送新提交到 `main` 分支会自动触发重新部署。

> ⚠️ Render 免费实例会在闲置约 15 分钟后休眠，下次访问需要几十秒冷启动；临时磁盘在重新部署后会清空。

## 📁 目录结构

```
houqishi/
├── app.py               # Flask 服务与接口
├── media_utils.py       # ffmpeg 媒体处理核心逻辑
├── templates/
│   └── index.html       # 单页前端（转换/合成/导入/文件库）
├── requirements.txt
├── Dockerfile
├── render.yaml
├── storage/             # 运行时生成：持久化文件 + 索引（不入库）
└── README.md
```

## 📌 限制

- 单次请求最大 **200MB**（可在 `app.py` 的 `MAX_CONTENT_LENGTH` 调整）；
- 合成输出固定 44.1kHz / 16bit / 立体声（CD 级无损标准）；
- 文件默认保留 48 小时，注意及时下载。
