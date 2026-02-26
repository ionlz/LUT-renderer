# LUT Renderer（LUT 渲染器）

LUT Renderer 是一个基于 PySide6 + FFmpeg 的桌面视频批处理工具，面向日常调色交付与批量转码场景。它提供 LUT 管理、参数预设、批量队列、两种处理模式（快速交付/专业母带）、缩略图与日志追踪等能力。


我没有相关视频处理的知识，这个项目都是vibecode出来的，有什么问题请在issue中，会尽量改正。

>! 目前使用上还有问题

## 功能概览
- 批量导入视频文件或文件夹，自动过滤常见格式（mp4/mov/mkv/avi/mxf/webm）。
- LUT 应用：支持 `.cube` 文件历史记录与管理面板；支持批量导入；可一键应用到待处理任务。
- LUT 插值：提供三线性/四面体插值选项（默认四面体），更接近专业调色结果。
- 处理模式：
  - 快速交付：单阶段转码，适合高效率批处理。
  - 专业母带：两阶段流水线（ProRes 母带 -> 分发编码），强调稳定性与可控性。
- 专业母带资源预估：添加任务时会估算 ProRes 母带占用空间，并提示可用磁盘空间。
- 时间结构控制：对 VFR 源可强制 CFR；可指定输出 fps。
- 色彩与位深策略：支持继承色彩元数据、10bit 保留/强制 8bit。
- 编码参数：视频/音频编码器、像素格式、分辨率、码率、CRF、preset、tune、GOP、profile/level、线程数、音频码率等。
- 预设系统：保存/加载编码参数，一键复用常用配置。
- 任务队列：进度条、日志输出、取消/清理/重处理操作。
- 缩略图：后台生成封面缩略图并缓存。
- 主题与暗夜模式：集成 qt-material 主题，可在菜单中切换暗夜模式。

## 运行环境
- Python >= 3.10
- 依赖：`PySide6`, `qt-material`, `platformdirs`（安装时自动拉取）
- 外部工具：`ffmpeg` 与 `ffprobe` 需在系统 PATH 中

## 快速开始

### 使用 uv（推荐）
```bash
uv sync
uv run lut-renderer
```

### 使用 venv + pip
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e .
python -m lut_renderer.app
```

## 使用流程
1. 点击"添加文件/添加文件夹"导入视频。
2. 选择 LUT（可选），设置输出目录与参数。
3. 选择处理模式（快速交付/专业母带）。
4. 点击"开始全部"执行任务。
5. 通过右侧日志查看 FFmpeg 输出与阶段信息。
6. 在"专业母带"模式下，导入后会弹出母带占用空间估算提示。

## 输出与命名规则
- 默认输出目录：若未指定，使用源文件所在目录的 `output/`。
- 输出文件名：`原名_out.ext`（如已存在会自动追加序号）。
- 封面图：`原名_cover.jpg`（可选，输出到同目录）。
- 专业母带中间文件：输出到“母带缓存目录”（需在应用内手动选择），命名为 `原名_master.mov`。

## 处理模式说明
- 快速交付：
  - 单次 FFmpeg 处理，可直接应用 LUT。
  - 参数以当前面板设置为准。
- 专业母带：
  - 阶段 1：ProRes 422 HQ（`prores_ks`, `yuv422p10le`），应用 LUT。
  - 阶段 2：以当前设置进行分发编码（不再重复 LUT）。
  - 中间母带文件在成功后会自动清理。
  - 添加任务时会显示母带空间估算（防止磁盘被占满）。

## 关键策略（当前实现）
- 时间结构：
  - 当指定 fps 时，使用 `-fps_mode cfr -r <fps>`。
  - 未指定 fps 时：VFR 源可按"强制 CFR"开关处理，CFR 源默认 passthrough。
- 码率稳定：当设置 `-b:v` 时自动附带 `-maxrate` 与 `-bufsize`（2x）。
- 位深策略：
  - 保持 10bit（源为 10bit 且编码器支持时，自动选择 10bit pix_fmt；像素格式设为“自动（不强制）”时生效）。
  - 强制 8bit（强制 `yuv420p`）。
- 色彩元数据：
  - 可继承源视频 primaries/trc/colorspace/range。
  - 启用 LUT 时可选择输出标记策略（默认标记为 BT.709 / range=tv，更符合常见交付链路）。
- LUT 插值：默认四面体（tetrahedral），可选三线性（trilinear）。

## 近期改进（面向常用 Rec.709 交付）
- LUT 输出标记：新增“LUT 输出标记”，启用 LUT 时默认写入 `bt709/bt709/bt709 + tv`，避免“画面已变换但元数据仍继承源文件”导致播放器误解读。
- LUT 输入矩阵：新增“LUT 输入矩阵”（auto / 强制 BT.709 / 不强制），用于约束 LUT 前 YUV→RGB 的矩阵选择，减少矩阵误判引发的偏色（注意：这不是完整色彩管理，Log→709 仍应使用匹配的转换 LUT）。
- Full range 归一化：检测到 `yuvj*` / full-range(pc) 时，会按交付策略将范围归一化（BT.709 输出默认归一到 tv）。
- 防止无效组合：启用 LUT/滤镜时会阻止 `-c:v copy`（UI 会自动切换编码器，命令构建也会硬阻止），避免 FFmpeg “streamcopy 与滤镜不可同时使用”错误。
- 专业母带阶段 2：分发编码阶段会在运行前探测中间母带文件（ffprobe），避免继续沿用原始源文件的色彩/帧率信息做推断。
- 稳定性细节：Windows LUT 路径转义更稳；VFR 判定阈值调整以减少误判。

## 配置与缓存位置
基于 `platformdirs`：
- 设置/预设：`user_config_dir("lut-renderer")`
- 缩略图缓存：`user_cache_dir("lut-renderer")/thumbs/`
- 专业母带缓存：需在应用内手动选择“母带缓存目录”（不使用默认路径）；中间 ProRes 会写入该目录，成功后自动清理

常见路径示例：
- macOS: `~/Library/Application Support/lut-renderer`，`~/Library/Caches/lut-renderer`
- Windows: `%AppData%\\lut-renderer`，`%LocalAppData%\\lut-renderer\\Cache`
- Linux: `~/.config/lut-renderer`，`~/.cache/lut-renderer`

## 项目结构
- `src/lut_renderer/app.py`：应用入口（Qt 主窗口启动）。
- `src/lut_renderer/main_window.py`：主界面与业务调度（导入/任务/参数/日志）。
- `src/lut_renderer/task_manager.py`：任务队列与执行器，解析 FFmpeg 输出进度。
- `src/lut_renderer/ffmpeg.py`：命令构建与两阶段流水线。
- `src/lut_renderer/media_info.py`：ffprobe 探测源视频信息。
- `src/lut_renderer/presets.py`：预设保存/读取。
- `src/lut_renderer/lut_manager.py`：LUT 管理对话框。
- `src/lut_renderer/thumbnails.py`：缩略图生成与缓存。
- `src/lut_renderer/settings.py`：应用设置持久化。

## 已知限制
- 依赖系统 `ffmpeg/ffprobe`，应用内不自带二进制。
- 暂未提供暂停/继续；仅支持取消与重新入队。
- 硬件编码参数未做可用性检测（需用户自行确认）。

## 版本
当前版本见 `src/lut_renderer/__init__.py`。
