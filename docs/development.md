# 开发指南

## 环境搭建

### 系统要求

- Python >= 3.10
- FFmpeg 和 FFprobe（必须在 PATH 中）

### 安装步骤

```bash
# 克隆项目
git clone <repository-url>
cd lut-renderer

# 使用 uv（推荐）
uv sync

# 或使用 venv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
```

### 验证安装

```bash
# 检查 FFmpeg
ffmpeg -version
ffprobe -version

# 启动应用
uv run lut-renderer
# 或
python -m lut_renderer.app
```

## 项目结构

```
lut-renderer/
├── src/lut_renderer/        # 源代码
│   ├── __init__.py          # 版本定义
│   ├── app.py               # 入口
│   ├── main_window.py       # 主窗口 (~1800 行)
│   ├── task_manager.py      # 任务管理
│   ├── ffmpeg.py            # 命令构建
│   ├── media_info.py        # 视频探测
│   ├── presets.py           # 预设管理
│   ├── lut_manager.py       # LUT 管理
│   ├── thumbnails.py        # 缩略图
│   ├── settings.py          # 设置
│   └── models.py            # 数据模型
├── docs/                    # 文档
├── pyproject.toml           # 项目配置
└── readme.md                # 使用说明
```

## 依赖说明

### 运行时依赖

```toml
[project.dependencies]
PySide6 = ">=6.6"        # Qt 绑定
qt-material = ">=2.14"   # Material 主题
platformdirs = ">=4.2"   # 跨平台路径
```

### 外部依赖

- **FFmpeg**: 视频转码、缩略图生成
- **FFprobe**: 视频信息探测

## 代码风格

### 类型注解

项目使用 Python 类型注解：

```python
from __future__ import annotations
from pathlib import Path
from typing import Optional, List, Dict

def process_file(path: Path, output: Optional[Path] = None) -> List[str]:
    ...
```

### 数据类

使用 `@dataclass` 定义数据模型：

```python
from dataclasses import dataclass, field

@dataclass
class Task:
    task_id: str
    source_path: Path
    status: TaskStatus = TaskStatus.PENDING
    metadata: dict = field(default_factory=dict)
```

### 信号与槽

使用 PySide6 的 Signal/Slot 机制：

```python
from PySide6.QtCore import QObject, Signal

class TaskManager(QObject):
    task_added = Signal(str)  # 参数类型

    def add_task(self, task: Task) -> None:
        self.tasks[task.task_id] = task
        self.task_added.emit(task.task_id)
```

## 调试技巧

### 查看生成的 FFmpeg 命令

在日志面板中可以看到完整的 FFmpeg 命令行：

```
10:30:15 阶段 1/1: 快速交付
10:30:15 时间结构: 源为 CFR/未知，fps_mode=passthrough
10:30:15 命令: ffmpeg -hide_banner -y -i input.mp4 ...
```

### 手动测试 FFprobe

```bash
ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,bit_rate,avg_frame_rate \
    -of json video.mp4
```

### 日志级别

应用使用 Python 标准 logging（当前主要使用 `print` 和 UI 日志面板）。

## 扩展开发

### 添加新的编码器

1. 在 `main_window.py` 的 `video_codec_combo` 中添加选项：

```python
self.video_codec_combo.addItems([
    "libx264",
    "h264_videotoolbox",
    "libx265",
    "新编码器",  # 添加
])
```

2. 如果编码器需要特殊处理，在 `ffmpeg.py` 中添加逻辑：

```python
def _supports_10bit(codec: str) -> bool:
    return codec in {"prores_ks", "libx265", "hevc_videotoolbox", "新编码器"}
```

### 添加新的处理参数

1. 在 `models.py` 的 `ProcessingParams` 中添加字段：

```python
@dataclass
class ProcessingParams:
    # ... 现有字段
    new_param: str = ""  # 新参数
```

2. 更新 `to_dict()` 和 `from_dict()` 方法
3. 在 `main_window.py` 中添加 UI 控件
4. 在 `ffmpeg.py` 的 `build_command()` 中处理参数

### 添加新的预设

预设是 JSON 文件，可以手动创建：

```json
{
  "video_codec": "libx264",
  "audio_codec": "aac",
  "pix_fmt": "yuv420p",
  "crf": "18",
  "preset": "slow",
  "processing_mode": "fast"
}
```

保存到 `{config_dir}/lut-renderer/presets/my_preset.json`

## 测试

### 手动测试清单

- [ ] 添加文件/文件夹
- [ ] 应用 LUT
- [ ] 快速交付模式转码
- [ ] 专业母带模式转码
- [ ] 预设保存/加载/删除
- [ ] 任务取消
- [ ] 主题切换
- [ ] 窗口布局持久化

### 常见测试视频

可以使用以下方式生成测试视频：

```bash
# 生成 10 秒测试视频
ffmpeg -f lavfi -i testsrc=duration=10:size=1920x1080:rate=30 \
    -f lavfi -i sine=frequency=440:duration=10 \
    -c:v libx264 -c:a aac test.mp4

# 生成 VFR 视频（模拟手机录制）
ffmpeg -f lavfi -i testsrc=duration=10:size=1920x1080:rate=30 \
    -vsync vfr test_vfr.mp4
```

## 发布

### 版本号

版本定义在 `src/lut_renderer/__init__.py`：

```python
__version__ = "0.1.0"
```

### 构建分发包

```bash
# 使用 setuptools
pip install build
python -m build

# 生成的包在 dist/ 目录
```

### 依赖检查

```bash
# 检查依赖版本
pip list --outdated

# 生成 requirements.txt（如需要）
pip freeze > requirements.txt
```

## 已知问题与限制

1. **依赖外部 FFmpeg**: 应用不自带二进制，需要用户安装
2. **无暂停功能**: 当前仅支持取消和重新处理
3. **硬件编码检测**: 未做编码器可用性检测，需用户自行确认
4. **macOS IMK 日志**: 通过 stderr 过滤处理，可能有极少量日志丢失

## 贡献指南

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

### 代码审查要点

- [ ] 类型注解完整
- [ ] 异常处理适当
- [ ] UI 文本清晰（中文）
- [ ] 日志输出有意义
- [ ] 不阻塞主线程
