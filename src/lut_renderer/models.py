from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from .media_info import VideoInfo


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class ProcessingParams:
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    # Empty means "don't force"; let bit-depth policy / encoder defaults decide.
    pix_fmt: str = ""
    resolution: str = ""  # empty means keep source
    bitrate: str = ""
    fps: str = ""  # empty means keep source
    crf: str = ""
    preset: str = ""
    tune: str = ""
    gop: str = ""
    profile: str = ""
    level: str = ""
    threads: str = ""
    audio_bitrate: str = ""
    sample_rate: str = ""
    channels: str = ""
    faststart: bool = False
    overwrite: bool = True
    generate_cover: bool = False
    processing_mode: str = "fast"
    bit_depth_policy: str = "preserve"
    force_cfr: bool = True
    inherit_color_metadata: bool = True
    lut_interp: str = "tetrahedral"
    zscale_dither: str = "none"
    # LUT input interpretation for YUV<->RGB matrix selection (not a full color-managed pipeline).
    # - "auto": use ffprobe colorspace when available; otherwise let FFmpeg decide
    # - "bt709": force bt709 matrix for YUV->RGB conversion before lut3d
    # - "none": do not force matrix selection
    lut_input_matrix: str = "auto"
    # When LUT is applied, decide what color tags to write on the output file.
    # - "bt709": mark output as Rec.709 / limited range (delivery-friendly default)
    # - "inherit": write back source tags (may be wrong after creative/transform LUTs)
    # - "none": do not write any tags
    lut_output_tags: str = "bt709"

    def to_dict(self) -> dict:
        return {
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "pix_fmt": self.pix_fmt,
            "resolution": self.resolution,
            "bitrate": self.bitrate,
            "fps": self.fps,
            "crf": self.crf,
            "preset": self.preset,
            "tune": self.tune,
            "gop": self.gop,
            "profile": self.profile,
            "level": self.level,
            "threads": self.threads,
            "audio_bitrate": self.audio_bitrate,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "faststart": self.faststart,
            "overwrite": self.overwrite,
            "generate_cover": self.generate_cover,
            "processing_mode": self.processing_mode,
            "bit_depth_policy": self.bit_depth_policy,
            "force_cfr": self.force_cfr,
            "inherit_color_metadata": self.inherit_color_metadata,
            "lut_interp": self.lut_interp,
            "zscale_dither": self.zscale_dither,
            "lut_input_matrix": self.lut_input_matrix,
            "lut_output_tags": self.lut_output_tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessingParams":
        defaults = cls()
        return cls(
            video_codec=data.get("video_codec", defaults.video_codec),
            audio_codec=data.get("audio_codec", defaults.audio_codec),
            pix_fmt=data.get("pix_fmt", defaults.pix_fmt),
            resolution=data.get("resolution", defaults.resolution),
            bitrate=data.get("bitrate", defaults.bitrate),
            fps=data.get("fps", defaults.fps),
            crf=data.get("crf", defaults.crf),
            preset=data.get("preset", defaults.preset),
            tune=data.get("tune", defaults.tune),
            gop=data.get("gop", defaults.gop),
            profile=data.get("profile", defaults.profile),
            level=data.get("level", defaults.level),
            threads=data.get("threads", defaults.threads),
            audio_bitrate=data.get("audio_bitrate", defaults.audio_bitrate),
            sample_rate=data.get("sample_rate", defaults.sample_rate),
            channels=data.get("channels", defaults.channels),
            faststart=bool(data.get("faststart", defaults.faststart)),
            overwrite=bool(data.get("overwrite", defaults.overwrite)),
            generate_cover=bool(data.get("generate_cover", defaults.generate_cover)),
            processing_mode=data.get("processing_mode", defaults.processing_mode),
            bit_depth_policy=data.get("bit_depth_policy", defaults.bit_depth_policy),
            force_cfr=bool(data.get("force_cfr", defaults.force_cfr)),
            inherit_color_metadata=bool(
                data.get("inherit_color_metadata", defaults.inherit_color_metadata)
            ),
            lut_interp=data.get("lut_interp", defaults.lut_interp),
            zscale_dither=data.get("zscale_dither", defaults.zscale_dither),
            lut_input_matrix=data.get("lut_input_matrix", defaults.lut_input_matrix),
            lut_output_tags=data.get("lut_output_tags", defaults.lut_output_tags),
        )


@dataclass
class Task:
    task_id: str
    source_path: Path
    output_path: Path
    lut_path: Optional[Path]
    cover_path: Optional[Path]
    params: ProcessingParams
    source_info: Optional[VideoInfo] = None
    intermediate_path: Optional[Path] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    error: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def display_name(self) -> str:
        return self.source_path.name
