# API 参考

## 数据模型 (models.py)

### TaskStatus

任务状态枚举。

```python
class TaskStatus(str, Enum):
    PENDING = "pending"      # 等待中
    RUNNING = "running"      # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    CANCELED = "canceled"    # 已取消
```

### ProcessingParams

处理参数数据类，包含所有编码相关配置。

```python
@dataclass
class ProcessingParams:
    # 编码器设置
    video_codec: str = "libx264"      # 视频编码器
    audio_codec: str = "aac"          # 音频编码器
    pix_fmt: str = ""                # 像素格式（空=不强制）

    # 视频参数
    resolution: str = ""              # 分辨率，如 "1920x1080"
    bitrate: str = ""                 # 视频码率，如 "4000k"
    fps: str = ""                     # 帧率
    crf: str = ""                     # 恒定质量因子
    preset: str = ""                  # 编码预设
    tune: str = ""                    # 编码调优
    gop: str = ""                     # 关键帧间隔
    profile: str = ""                 # 编码档位
    level: str = ""                   # 编码等级
    threads: str = ""                 # 编码线程数

    # 音频参数
    audio_bitrate: str = ""           # 音频码率
    sample_rate: str = ""             # 采样率
    channels: str = ""                # 声道数

    # 功能开关
    faststart: bool = False           # moov 前置
    overwrite: bool = True            # 覆盖已存在文件
    generate_cover: bool = False      # 生成封面图

    # 处理策略
    processing_mode: str = "fast"     # 处理模式: "fast" | "pro"
    bit_depth_policy: str = "preserve"  # 位深策略
    force_cfr: bool = True            # 强制恒定帧率
    inherit_color_metadata: bool = True  # 继承色彩元数据
    lut_interp: str = "tetrahedral"   # LUT 插值算法
    lut_input_matrix: str = "auto"    # 启用 LUT 时的输入矩阵策略
    lut_output_tags: str = "bt709"    # 启用 LUT 时的输出色彩标记策略
```

**方法**：

| 方法 | 说明 |
|------|------|
| `to_dict() -> dict` | 序列化为字典 |
| `from_dict(data: dict) -> ProcessingParams` | 从字典反序列化 |

### Task

任务数据类。

```python
@dataclass
class Task:
    task_id: str                          # 唯一标识符
    source_path: Path                     # 源文件路径
    output_path: Path                     # 输出路径
    lut_path: Optional[Path]              # LUT 文件路径
    cover_path: Optional[Path]            # 封面图路径
    params: ProcessingParams              # 处理参数
    source_info: Optional[VideoInfo]      # 源视频信息
    intermediate_path: Optional[Path]     # 中间文件路径（专业模式）
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0                     # 进度百分比
    error: str = ""                       # 错误信息
    started_at: Optional[float] = None    # 开始时间戳
    finished_at: Optional[float] = None   # 完成时间戳
    metadata: dict = field(default_factory=dict)
```

---

## 视频信息 (media_info.py)

### VideoInfo

视频元信息数据类。

```python
@dataclass
class VideoInfo:
    width: Optional[int] = None
    height: Optional[int] = None
    bitrate: Optional[str] = None        # 如 "8000k"
    fps: Optional[float] = None
    avg_fps: Optional[float] = None
    r_fps: Optional[float] = None
    is_vfr: bool = False                 # 是否可变帧率
    duration: Optional[float] = None     # 时长（秒）
    pix_fmt: Optional[str] = None        # 像素格式
    bit_depth: Optional[int] = None      # 位深
    color_primaries: Optional[str] = None
    color_trc: Optional[str] = None
    colorspace: Optional[str] = None
    color_range: Optional[str] = None

    @property
    def resolution(self) -> Optional[str]:
        """返回 "宽x高" 格式的分辨率"""
```

### probe_video

探测视频文件信息。

```python
def probe_video(path: Path) -> VideoInfo
```

**参数**：
- `path`: 视频文件路径

**返回**：`VideoInfo` 实例

**异常**：`subprocess.CalledProcessError` (ffprobe 失败时)

**示例**：
```python
info = probe_video(Path("video.mp4"))
print(f"分辨率: {info.resolution}")
print(f"帧率: {info.fps}")
print(f"VFR: {info.is_vfr}")
```

---

## FFmpeg 命令构建 (ffmpeg.py)

### build_command

构建 FFmpeg 命令行。

```python
def build_command(
    source: Path,
    output: Path,
    params: ProcessingParams,
    lut_path: Optional[Path] = None,
    ffmpeg_bin: str = "ffmpeg",
    source_info: Optional[VideoInfo] = None,
    notes: Optional[List[str]] = None,
) -> List[str]
```

**参数**：
- `source`: 源文件路径
- `output`: 输出文件路径
- `params`: 处理参数
- `lut_path`: LUT 文件路径（可选）
- `ffmpeg_bin`: FFmpeg 可执行文件名
- `source_info`: 源视频信息（用于智能参数推断）
- `notes`: 输出参数，用于收集命令说明

**返回**：命令行参数列表

### build_pipeline

构建多阶段处理流水线。

```python
def build_pipeline(
    task: Task,
    ffmpeg_bin: str = "ffmpeg"
) -> List[CommandStage]
```

**返回**：`CommandStage` 列表

**专业模式返回两个阶段**：
1. ProRes 母带阶段（应用 LUT）
2. 分发编码阶段（不重复应用 LUT）

### CommandStage

处理阶段数据类。

```python
@dataclass
class CommandStage:
    name: str                    # 阶段名称
    source_path: Path            # 输入路径
    output_path: Path            # 输出路径
    params: ProcessingParams     # 阶段参数
    lut_path: Optional[Path]     # LUT（可选）
    cleanup_on_success: bool     # 成功后是否删除
    notes: List[str]             # 阶段说明
    probe_source: bool           # 是否在运行前 probe 输入
```

---

## 任务管理 (task_manager.py)

### TaskManager

任务队列管理器。

```python
class TaskManager(QObject):
    # 信号
    task_added = Signal(str)           # task_id
    task_updated = Signal(str)         # task_id
    task_progress = Signal(str, int)   # task_id, progress
    queue_finished = Signal()          # 队列完成
    task_log = Signal(str, str)        # task_id, message
```

**方法**：

| 方法 | 说明 |
|------|------|
| `__init__(max_concurrency=2, ffmpeg_bin="ffmpeg")` | 初始化 |
| `set_max_concurrency(value: int)` | 设置最大并发数 |
| `add_task(task: Task)` | 添加单个任务 |
| `add_tasks(tasks: List[Task])` | 批量添加任务 |
| `start_all()` | 启动所有待处理任务 |
| `cancel_task(task_id: str)` | 取消指定任务 |
| `clear_completed()` | 清理已完成任务 |
| `remove_task(task_id: str)` | 移除任务 |

**属性**：
- `tasks: Dict[str, Task]` - 任务字典

### TaskRunner

任务执行器（在后台线程运行）。

```python
class TaskRunner(QRunnable):
    def __init__(self, task: Task, ffmpeg_bin: str = "ffmpeg")
    def cancel(self)  # 取消执行
```

---

## 预设管理 (presets.py)

### 预设目录

```python
def presets_dir() -> Path  # 获取预设存储目录
```

### 列表与加载

```python
def list_presets() -> List[str]  # 列出所有预设名称
def load_preset(name: str) -> ProcessingParams  # 加载预设
def load_all_presets() -> Dict[str, ProcessingParams]  # 加载全部
```

### 保存与删除

```python
def save_preset(name: str, params: ProcessingParams) -> Path
    # 保存新预设，已存在时抛出 FileExistsError

def overwrite_preset(name: str, params: ProcessingParams) -> Path
    # 覆盖已存在的预设

def delete_preset(name: str) -> None
    # 删除预设

def rename_preset(old_name: str, new_name: str) -> Path
    # 重命名预设
```

---

## 设置持久化 (settings.py)

```python
def load_settings() -> Dict[str, Any]
    # 加载应用设置

def save_settings(data: Dict[str, Any]) -> None
    # 保存应用设置
```

**设置文件位置**：`{user_config_dir}/lut-renderer/settings.json`

**常用设置键**：

| 键 | 类型 | 说明 |
|----|------|------|
| `ui_theme` | str | 主题："light" / "dark" |
| `ui_geometry` | str | 窗口几何信息（Base64） |
| `ui_state` | str | 窗口状态（Base64） |
| `lut_history` | List[str] | LUT 历史记录 |
| `last_lut` | str | 上次使用的 LUT |

---

## 缩略图生成 (thumbnails.py)

```python
def ensure_thumbnail(source: Path, width: int = 160) -> Optional[Path]
```

**参数**：
- `source`: 视频文件路径
- `width`: 缩略图宽度（高度自动计算）

**返回**：缩略图路径，失败返回 None

**缓存策略**：
- 缓存键基于 `{路径}:{修改时间}` 的 SHA1 哈希
- 缓存目录：`{user_cache_dir}/lut-renderer/thumbs/`

---

## LUT 管理 (lut_manager.py)

### LutManagerDialog

LUT 管理对话框。

```python
class LutManagerDialog(QDialog):
    # 信号
    lut_selected = Signal(str)      # LUT 被选择
    history_changed = Signal()      # 历史记录变更
```

**功能**：
- 查看 LUT 历史记录
- 添加/删除 LUT
- 设置当前 LUT
- 清理无效路径
- 复制路径到剪贴板
- 在文件管理器中显示

---

## 常量

```python
# 支持的视频扩展名 (main_window.py)
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".webm"}

# 应用名称
APP_NAME = "lut-renderer"

# 布局版本（用于判断是否恢复 UI 状态）
LAYOUT_VERSION = 2
```
