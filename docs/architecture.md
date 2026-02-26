# 架构设计

## 整体架构

LUT Renderer 采用经典的 **Model-View-Controller (MVC)** 模式，结合 Qt 的 Signal/Slot 机制实现松耦合。

```
┌─────────────────────────────────────────────────────────────┐
│                        MainWindow (View)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Task Table  │  │ Params Panel│  │    Log Panel        │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │ Signal/Slot
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     TaskManager (Controller)                 │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  TaskRunner (QRunnable) ──> FFmpeg Process              ││
│  └─────────────────────────────────────────────────────────┘│
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                        Models                                │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐   │
│  │ Task         │  │ProcessingParams│  │ VideoInfo      │   │
│  └──────────────┘  └───────────────┘  └────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 模块职责

### 1. 入口层 (app.py)

```python
def main() -> int:
    # 1. 过滤 macOS IMK 输入法噪音日志
    # 2. 创建 QApplication
    # 3. 应用 qt-material 主题
    # 4. 显示主窗口
```

**关键功能**：
- 初始化 Qt 应用
- 主题加载（暗夜/明亮模式）
- macOS 特定的 stderr 过滤（处理 IMKCFRunLoopWakeUpReliable 警告）

### 2. 视图层 (main_window.py)

主窗口是应用的核心，包含：

**UI 组件**：
- **任务表格** (`QTableWidget`)：显示缩略图、文件名、状态、进度、输出路径
- **参数面板** (`QDockWidget`)：所有编码参数设置
- **日志面板** (`QPlainTextEdit`)：FFmpeg 输出日志

**信号连接**：
```python
task_manager.task_added -> _on_task_added
task_manager.task_updated -> _on_task_updated
task_manager.task_progress -> _on_task_progress
task_manager.task_log -> _on_task_log
```

### 3. 控制层 (task_manager.py)

**TaskManager**：
- 管理任务队列 (`Dict[str, Task]`)
- 使用 `QThreadPool` 并发执行任务
- 支持任务取消、清理、重处理

**TaskRunner** (`QRunnable`)：
- 执行实际的 FFmpeg 转码
- 解析 FFmpeg 输出计算进度
- 发射进度/状态/日志信号

```python
class TaskSignals(QObject):
    progress = Signal(str, int)    # task_id, progress%
    status = Signal(str, str)       # task_id, status
    finished = Signal(str, str)     # task_id, final_status
    log = Signal(str, str)          # task_id, message
```

### 4. FFmpeg 命令构建 (ffmpeg.py)

**核心函数**：
- `build_command()`: 构建 FFmpeg 命令行
- `build_pipeline()`: 构建多阶段处理流水线

**关键策略**：

```python
# 时间结构策略
if params.fps:
    cmd.extend(["-fps_mode", "cfr", "-r", params.fps])
elif source_is_vfr and params.force_cfr:
    cmd.extend(["-fps_mode", "cfr"])
else:
    cmd.extend(["-fps_mode", "passthrough"])

# 码率稳定策略
if params.bitrate:
    cmd.extend(["-b:v", params.bitrate])
    cmd.extend(["-maxrate", maxrate, "-bufsize", 2x_bitrate])

# 位深策略
if bit_depth_policy == "force_8bit":
    pix_fmt = "yuv420p"
elif bit_depth_policy == "preserve" and source_10bit:
    pix_fmt = "yuv422p10le"  # ProRes
    # 或 yuv420p10le (其他编码器)
```

### 5. 视频信息探测 (media_info.py)

使用 `ffprobe` 获取源视频元数据：

```python
@dataclass
class VideoInfo:
    width: int
    height: int
    bitrate: str
    fps: float
    is_vfr: bool          # 可变帧率检测
    duration: float
    pix_fmt: str
    bit_depth: int
    color_primaries: str
    color_trc: str
    colorspace: str
    color_range: str
```

**VFR 检测逻辑**：
```python
is_vfr = abs(avg_fps - r_fps) > _FPS_EPSILON
```

### 6. 预设系统 (presets.py)

预设以 JSON 格式存储在用户配置目录：

```
~/Library/Application Support/lut-renderer/presets/
├── youtube.json
├── instagram.json
└── prores_master.json
```

### 7. LUT 管理 (lut_manager.py)

- 管理历史 LUT 列表
- 支持搜索、删除、清理无效路径
- LUT 选择信号传递给主窗口

### 8. 缩略图生成 (thumbnails.py)

- 使用 SHA1 哈希作为缓存键（基于路径 + 修改时间）
- 调用 `ffmpeg -frames:v 1` 截取首帧
- 缓存到 `user_cache_dir/thumbs/`

## 数据流

### 任务添加流程

```
用户点击"添加文件"
    ↓
QFileDialog 选择文件
    ↓
_add_paths()
    ↓
probe_video() 获取源信息
    ↓
创建 Task 对象
    ↓
TaskManager.add_task()
    ↓
Signal: task_added
    ↓
MainWindow: _on_task_added() 更新表格
```

### 任务执行流程

```
用户点击"开始全部"
    ↓
TaskManager.start_all()
    ↓
为每个 PENDING 任务创建 TaskRunner
    ↓
QThreadPool.start(runner)
    ↓
TaskRunner.run():
    ├─ build_pipeline() 获取命令阶段
    ├─ 执行 FFmpeg 子进程
    ├─ 解析输出计算进度
    └─ 发射信号更新 UI
    ↓
Signal: progress/status/finished
    ↓
MainWindow 更新表格和日志
```

## 处理模式详解

### 快速交付模式 (fast)

单阶段处理：
```
源视频 → [LUT + 编码] → 输出视频
```

特点：
- 一次 FFmpeg 调用
- 适合批量处理
- 默认使用硬件编码（macOS: h264_videotoolbox）

### 专业母带模式 (pro)

两阶段处理：
```
源视频 → [LUT + ProRes 422 HQ] → 中间母带
    ↓
中间母带 → [分发编码] → 输出视频
    ↓
删除中间母带
```

特点：
- 固定 ProRes 422 HQ（yuv422p10le）作为中间格式
- 第一阶段应用 LUT
- 第二阶段进行分发编码
- 中间文件自动清理

## 并发模型

```python
# TaskManager 初始化
self.thread_pool = QThreadPool()
self.thread_pool.setMaxThreadCount(max_concurrency)  # 默认 1

# 每个任务在独立线程运行
class TaskRunner(QRunnable):
    def run(self):
        # 执行 FFmpeg，不阻塞主线程
```

**注意事项**：
- 视频编码是 CPU/GPU 重负载任务
- 默认并发数为 1，最大支持 16
- 硬件编码器实例数有限制

## 错误处理

- FFmpeg 返回非零退出码 → 任务状态设为 FAILED
- 源视频探测失败 → 记录日志，继续处理（参数使用默认值）
- LUT 文件不存在 → 记录警告日志

## UI 状态持久化

```python
# 保存
settings["ui_geometry"] = self.saveGeometry().toBase64()
settings["ui_state"] = self.saveState().toBase64()

# 恢复
self.restoreGeometry(settings["ui_geometry"])
self.restoreState(settings["ui_state"])
```
