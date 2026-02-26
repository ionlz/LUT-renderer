from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import List, Optional, Tuple

from .media_info import VideoInfo
from .models import ProcessingParams, Task

_BITRATE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)([kKmMgG]?)\s*$")


@dataclass
class CommandStage:
    name: str
    source_path: Path
    output_path: Path
    params: ProcessingParams
    lut_path: Optional[Path] = None
    cleanup_on_success: bool = False
    notes: List[str] = field(default_factory=list)
    # When True, probe the stage input (ffprobe) right before building the command.
    # This matters for the "pro" pipeline where stage 2 reads an intermediate file.
    probe_source: bool = False


def _escape_filter_path(path: Path) -> str:
    # Use single quotes in ffmpeg filter args and escape single quotes and backslashes.
    value = str(path)
    # Important: escape backslashes first, then single quotes.
    # - We pass args as a list (no shell), but FFmpeg's filtergraph parser still treats "\" as escape.
    value = value.replace("\\", "\\\\")
    value = value.replace("'", "\\'")
    return value


def _format_float(value: float) -> str:
    text = f"{value:.3f}"
    return text.rstrip("0").rstrip(".")


def _parse_fraction(value: str) -> Optional[float]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if "/" in text:
        parts = text.split("/", 1)
        try:
            numerator = float(parts[0])
            denominator = float(parts[1])
        except ValueError:
            return None
        if denominator == 0:
            return None
        return numerator / denominator
    try:
        return float(text)
    except ValueError:
        return None


def _parse_bitrate(value: str) -> Optional[Tuple[float, str]]:
    if not value:
        return None
    match = _BITRATE_RE.match(value)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or ""
    if number <= 0:
        return None
    return number, unit


def _format_bitrate(number: float, unit: str) -> str:
    if abs(number - round(number)) < 1e-6:
        return f"{int(round(number))}{unit}"
    return f"{number:g}{unit}"


def _scale_bitrate(value: str, scale: float) -> Optional[str]:
    parsed = _parse_bitrate(value)
    if not parsed:
        return None
    number, unit = parsed
    return _format_bitrate(number * scale, unit)


def _bitrate_to_kbps(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    parsed = _parse_bitrate(value)
    if not parsed:
        return None
    number, unit = parsed
    unit_norm = unit.lower()
    if unit_norm == "k" or unit_norm == "":
        return number if unit_norm == "k" else None
    if unit_norm == "m":
        return number * 1000.0
    if unit_norm == "g":
        return number * 1000.0 * 1000.0
    return None


def _supports_10bit(codec: str) -> bool:
    return codec in {"prores_ks", "libx265", "hevc_videotoolbox"}


def _normalize_scale_matrix(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    mapping = {
        "bt709": "bt709",
        "smpte170m": "smpte170m",
        "bt470bg": "bt470bg",
        "bt2020nc": "bt2020nc",
        "bt2020c": "bt2020c",
    }
    return mapping.get(text)


def _needs_full_range_normalization(info: Optional[VideoInfo]) -> bool:
    if not info:
        return False
    if info.pix_fmt and str(info.pix_fmt).startswith("yuvj"):
        return True
    return bool(info.color_range and str(info.color_range).lower() == "pc")


def _full_range_intermediate_pix_fmt(info: Optional[VideoInfo]) -> str:
    pix_fmt = str(info.pix_fmt) if info and info.pix_fmt else ""
    if "444" in pix_fmt:
        return "yuv444p"
    if "422" in pix_fmt:
        return "yuv422p"
    return "yuv420p"


def _resolve_fps(params: ProcessingParams, source_info: Optional[VideoInfo]) -> Tuple[Optional[float], Optional[str]]:
    if params.fps:
        fps_value = _parse_fraction(params.fps)
        return fps_value, params.fps
    if source_info and source_info.fps:
        return source_info.fps, _format_float(source_info.fps)
    return None, None


def _append_color_metadata(
    cmd: List[str],
    source_info: Optional[VideoInfo],
    notes: List[str],
) -> None:
    if not source_info:
        return
    items = []
    if source_info.color_primaries:
        cmd.extend(["-color_primaries", source_info.color_primaries])
        items.append(f"primaries={source_info.color_primaries}")
    if source_info.color_trc:
        cmd.extend(["-color_trc", source_info.color_trc])
        items.append(f"trc={source_info.color_trc}")
    if source_info.colorspace:
        cmd.extend(["-colorspace", source_info.colorspace])
        items.append(f"colorspace={source_info.colorspace}")
    if source_info.color_range:
        cmd.extend(["-color_range", source_info.color_range])
        items.append(f"range={source_info.color_range}")
    if items:
        notes.append(f"继承色彩元数据: {', '.join(items)}")


def build_command(
    source: Path,
    output: Path,
    params: ProcessingParams,
    lut_path: Optional[Path] = None,
    ffmpeg_bin: str = "ffmpeg",
    source_info: Optional[VideoInfo] = None,
    notes: Optional[List[str]] = None,
) -> List[str]:
    notes = notes if notes is not None else []
    cmd = [ffmpeg_bin, "-hide_banner"]
    if params.overwrite:
        cmd.append("-y")

    cmd.extend(["-i", str(source)])

    filters: List[str] = []
    if lut_path:
        escaped = _escape_filter_path(lut_path)

        lut_output_policy = (getattr(params, "lut_output_tags", "") or "bt709").strip().lower()
        lut_matrix_policy = (getattr(params, "lut_input_matrix", "") or "auto").strip().lower()
        matrix = None
        if lut_matrix_policy == "bt709":
            matrix = "bt709"
        elif lut_matrix_policy == "auto":
            matrix = _normalize_scale_matrix(source_info.colorspace if source_info else None)
        elif lut_matrix_policy == "none":
            matrix = None
        else:
            matrix = _normalize_scale_matrix(lut_matrix_policy)

        scale_parts: List[str] = []
        if _needs_full_range_normalization(source_info):
            out_range = "pc"
            if lut_output_policy == "bt709":
                out_range = "tv"
            elif lut_output_policy == "inherit":
                out_range = (
                    str(source_info.color_range).lower().strip()
                    if source_info and source_info.color_range
                    else "pc"
                )
            elif lut_output_policy == "none":
                out_range = "pc"
            intermediate = _full_range_intermediate_pix_fmt(source_info)
            scale_parts.extend([f"in_range=pc", f"out_range={out_range}"])
            notes.append(
                f"Range: 检测到 full-range(pc)，已按 out_range={out_range} 规范化，避免 yuvj* 旧像素格式（format={intermediate}）"
            )
            if matrix:
                scale_parts.extend([f"in_color_matrix={matrix}", f"out_color_matrix={matrix}"])
                notes.append(f"LUT 输入矩阵: {matrix}（{lut_matrix_policy}）")
            filters.append("scale=" + ":".join(scale_parts))
            filters.append(f"format={intermediate}")
        elif matrix:
            filters.append(f"scale=in_color_matrix={matrix}:out_color_matrix={matrix}")
            notes.append(f"LUT 输入矩阵: {matrix}（{lut_matrix_policy}）")
        else:
            notes.append(
                "LUT 输入矩阵: 未强制（auto/none 或无法识别源 colorspace）"
            )

        interp = params.lut_interp or "tetrahedral"
        if interp not in {"nearest", "trilinear", "tetrahedral", "pyramid", "prism", "cubic"}:
            interp = "tetrahedral"

        filters.append(f"lut3d=file='{escaped}':interp={interp}")
        notes.append(f"LUT: 使用 lut3d（interp={interp}）")

    if params.video_codec:
        cmd.extend(["-c:v", params.video_codec])

    if params.audio_codec:
        cmd.extend(["-c:a", params.audio_codec])

    if filters and params.video_codec == "copy":
        raise ValueError("启用 LUT/滤镜时不能使用视频 copy（streamcopy 与滤镜不可同时使用）。")

    if params.video_codec and params.video_codec != "copy":
        fps_value, source_fps_text = _resolve_fps(params, source_info)

        # Time-structure defaults:
        # - CFR source + no explicit fps: passthrough (avoid timestamp rewrite).
        # - VFR source: CFR only when user explicitly requests it (force_cfr=True) or sets fps.
        if params.fps:
            cmd.extend(["-fps_mode", "cfr", "-r", params.fps])
            notes.append(f"时间结构: fps_mode=cfr, 输出帧率={params.fps}")
        else:
            source_is_vfr = bool(source_info and source_info.is_vfr)
            if source_is_vfr and params.force_cfr:
                cmd.extend(["-fps_mode", "cfr"])
                if source_fps_text:
                    cmd.extend(["-r", source_fps_text])
                    notes.append(f"时间结构: 源为 VFR，已强制 CFR，输出帧率={source_fps_text}")
                else:
                    notes.append("时间结构: 源为 VFR，已强制 CFR（未检测到帧率）")
            elif params.force_cfr and source_info is None:
                # Keep previous conservative behavior when we can't inspect the source.
                cmd.extend(["-fps_mode", "cfr"])
                notes.append("时间结构: fps_mode=cfr（未读取源信息）")
            else:
                cmd.extend(["-fps_mode", "passthrough"])
                if source_is_vfr:
                    notes.append("时间结构: 源为 VFR，fps_mode=passthrough（不重写时间戳）")
                else:
                    notes.append("时间结构: 源为 CFR/未知，fps_mode=passthrough（避免时间戳重写）")

        pix_fmt = params.pix_fmt
        if params.bit_depth_policy == "force_8bit":
            if pix_fmt != "yuv420p":
                notes.append("位深策略=强制8bit: pix_fmt=yuv420p")
            pix_fmt = "yuv420p"
        elif params.bit_depth_policy in {"preserve", "auto"} and not pix_fmt:
            if source_info and source_info.bit_depth and source_info.bit_depth >= 10:
                if _supports_10bit(params.video_codec):
                    if params.video_codec == "prores_ks":
                        pix_fmt = "yuv422p10le"
                    else:
                        pix_fmt = "yuv420p10le"
                    notes.append(f"位深策略=保持10bit: pix_fmt={pix_fmt}")
                else:
                    pix_fmt = "yuv420p"
                    notes.append("位深策略=保持10bit: 编码器不支持10bit，回退 yuv420p")

        if pix_fmt:
            if lut_path:
                filters.append(f"format={pix_fmt}")
            cmd.extend(["-pix_fmt", pix_fmt])

        if params.resolution:
            cmd.extend(["-s", params.resolution])

        if params.bitrate:
            cmd.extend(["-b:v", params.bitrate])
            maxrate = params.bitrate
            bufsize = _scale_bitrate(params.bitrate, 2)
            if bufsize:
                cmd.extend(["-maxrate", maxrate, "-bufsize", bufsize])
                notes.append(f"码率稳定: maxrate={maxrate}, bufsize={bufsize}")

        if params.crf:
            cmd.extend(["-crf", params.crf])

        if params.preset:
            cmd.extend(["-preset", params.preset])

        if params.tune:
            cmd.extend(["-tune", params.tune])

        if params.gop:
            cmd.extend(["-g", params.gop])
        elif fps_value:
            gop_value = max(1, round(fps_value))
            cmd.extend(["-g", str(gop_value)])
            notes.append(f"自动 GOP={gop_value} (fps={_format_float(fps_value)})")

        if params.profile:
            cmd.extend(["-profile:v", params.profile])

        if params.level:
            cmd.extend(["-level", params.level])

        if params.threads:
            cmd.extend(["-threads", params.threads])

        if lut_path:
            policy = (getattr(params, "lut_output_tags", "") or "bt709").strip().lower()
            if policy == "bt709":
                cmd.extend(
                    [
                        "-color_primaries",
                        "bt709",
                        "-color_trc",
                        "bt709",
                        "-colorspace",
                        "bt709",
                        "-color_range",
                        "tv",
                    ]
                )
                notes.append("LUT 输出标记: bt709/bt709/bt709, range=tv")
            elif policy == "inherit":
                if params.inherit_color_metadata:
                    _append_color_metadata(cmd, source_info, notes)
            elif policy == "none":
                notes.append("LUT 输出标记: none（不写色彩元数据）")
            else:
                # Fall back to the safest, delivery-friendly default.
                cmd.extend(
                    [
                        "-color_primaries",
                        "bt709",
                        "-color_trc",
                        "bt709",
                        "-colorspace",
                        "bt709",
                        "-color_range",
                        "tv",
                    ]
                )
                notes.append("LUT 输出标记: bt709/bt709/bt709, range=tv（回退）")
        else:
            if params.inherit_color_metadata:
                _append_color_metadata(cmd, source_info, notes)

        if params.video_codec and "videotoolbox" in params.video_codec:
            candidate = params.bitrate or (source_info.bitrate if source_info else "")
            kbps = _bitrate_to_kbps(candidate)
            if kbps and kbps >= 50_000:
                notes.append(
                    "提示: h264_videotoolbox 在高码率/重负载时可能出现 PTS 重建/帧重排的节奏错觉；"
                    "如需更稳定建议用 libx264 或切到“专业母带”。"
                )

    if filters:
        cmd.extend(["-vf", ",".join(filters)])

    if params.audio_codec and params.audio_codec != "copy":
        if params.audio_bitrate:
            cmd.extend(["-b:a", params.audio_bitrate])

        if params.sample_rate:
            cmd.extend(["-ar", params.sample_rate])

        if params.channels:
            cmd.extend(["-ac", params.channels])

    if params.faststart:
        cmd.extend(["-movflags", "+faststart"])

    cmd.append(str(output))
    return cmd


def _build_master_params(params: ProcessingParams) -> ProcessingParams:
    master = ProcessingParams(**params.to_dict())
    master.video_codec = "prores_ks"
    master.audio_codec = "copy"
    master.pix_fmt = "yuv422p10le"
    master.profile = "3"
    master.level = ""
    master.crf = ""
    master.preset = ""
    master.tune = ""
    master.bitrate = ""
    master.audio_bitrate = ""
    master.sample_rate = ""
    master.channels = ""
    master.faststart = False
    master.bit_depth_policy = "preserve"
    return master


def build_pipeline(task: Task, ffmpeg_bin: str = "ffmpeg") -> List[CommandStage]:
    params = task.params
    stages: List[CommandStage] = []

    if params.processing_mode == "pro":
        if not task.intermediate_path:
            raise ValueError("专业母带模式需要显式设置中间文件路径（请在界面中设置母带缓存目录）。")
        intermediate = task.intermediate_path
        master_params = _build_master_params(params)
        master_notes: List[str] = ["母带固定为 ProRes 422 HQ (yuv422p10le)"]
        stages.append(
            CommandStage(
                name="ProRes 母带",
                source_path=task.source_path,
                output_path=intermediate,
                params=master_params,
                lut_path=task.lut_path,
                cleanup_on_success=True,
                notes=master_notes,
                probe_source=False,
            )
        )

        dist_notes: List[str] = []
        stages.append(
            CommandStage(
                name="分发编码",
                source_path=intermediate,
                output_path=task.output_path,
                params=params,
                lut_path=None,
                cleanup_on_success=False,
                notes=dist_notes,
                probe_source=True,
            )
        )
        return stages

    notes: List[str] = []
    stages.append(
        CommandStage(
            name="快速交付",
            source_path=task.source_path,
            output_path=task.output_path,
            params=params,
            lut_path=task.lut_path,
            cleanup_on_success=False,
            notes=notes,
            probe_source=False,
        )
    )
    return stages
