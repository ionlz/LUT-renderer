# LUT Renderer - 项目概览

## 项目简介

**LUT Renderer（LUT 渲染器）** 是一个基于 PySide6 + FFmpeg 的专业桌面视频批处理工具，面向日常调色交付与批量转码场景。

- **版本**: 0.1.0
- **许可证**: MIT
- **Python 要求**: >= 3.10

## 核心功能

### 视频处理
- 批量导入视频文件或文件夹，自动过滤常见格式（mp4/mov/mkv/avi/mxf/webm）
- 支持两种处理模式：
  - **快速交付**：单阶段转码，适合高效率批处理
  - **专业母带**：两阶段流水线（ProRes 母带 -> 分发编码），强调稳定性与可控性

### LUT 管理
- 支持 `.cube` 文件历史记录与管理面板
- 支持批量导入 LUT
- 提供**三线性/四面体插值**选项（默认四面体），更接近专业调色结果
- 自动处理 full-range 像素格式（yuvj*）规范化

### 色彩与编码
- 时间结构控制：对 VFR 源可强制 CFR；可指定输出 fps
- 色彩与位深策略：支持继承色彩元数据、10bit 保留/强制 8bit
- 编码参数：视频/音频编码器、像素格式、分辨率、码率、CRF、preset、tune、GOP、profile/level、线程数等

### 其他功能
- 预设系统：保存/加载编码参数，一键复用常用配置
- 任务队列：进度条、日志输出、取消/清理/重处理操作
- 缩略图：后台生成封面缩略图并缓存
- 主题切换：集成 qt-material 主题，支持暗夜模式

## 技术栈

| 类别 | 技术 |
|------|------|
| GUI 框架 | PySide6 (Qt for Python) |
| 主题 | qt-material |
| 配置存储 | platformdirs |
| 视频处理 | FFmpeg / FFprobe（外部依赖） |

## 项目结构

```
lut-renderer/
├── src/lut_renderer/
│   ├── __init__.py      # 包初始化，版本定义
│   ├── app.py           # 应用入口
│   ├── main_window.py   # 主窗口 UI
│   ├── task_manager.py  # 任务队列管理
│   ├── ffmpeg.py        # FFmpeg 命令构建
│   ├── media_info.py    # 视频信息探测
│   ├── presets.py       # 预设管理
│   ├── lut_manager.py   # LUT 管理对话框
│   ├── thumbnails.py    # 缩略图生成
│   ├── settings.py      # 设置持久化
│   └── models.py        # 数据模型定义
├── pyproject.toml       # 项目配置
├── readme.md            # 使用说明
└── docs/                # 文档目录
```

## 运行环境

- Python >= 3.10
- FFmpeg 和 FFprobe 需在系统 PATH 中

## 快速开始

### 使用 uv（推荐）

```bash
uv sync
uv run lut-renderer
```

### 使用 venv + pip

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
python -m lut_renderer.app
```

## 配置与缓存位置

基于 `platformdirs`，配置存储在各操作系统的标准位置：

| 系统 | 配置目录 | 缓存目录 |
|------|----------|----------|
| macOS | `~/Library/Application Support/lut-renderer` | `~/Library/Caches/lut-renderer` |
| Windows | `%AppData%\lut-renderer` | `%LocalAppData%\lut-renderer\Cache` |
| Linux | `~/.config/lut-renderer` | `~/.cache/lut-renderer` |

## 相关文档

- [架构设计](./architecture.md)
- [API 参考](./api-reference.md)
- [开发指南](./development.md)
