from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_FPS_EPSILON = 0.1


@dataclass
class VideoInfo:
    width: Optional[int] = None
    height: Optional[int] = None
    bitrate: Optional[str] = None
    fps: Optional[float] = None
    avg_fps: Optional[float] = None
    r_fps: Optional[float] = None
    is_vfr: bool = False
    duration: Optional[float] = None
    pix_fmt: Optional[str] = None
    bit_depth: Optional[int] = None
    color_primaries: Optional[str] = None
    color_trc: Optional[str] = None
    colorspace: Optional[str] = None
    color_range: Optional[str] = None

    @property
    def resolution(self) -> Optional[str]:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return None


def _parse_fraction(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = value.strip()
    if not text or text == "0/0":
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


def _normalize_color(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = str(value).strip()
    if not cleaned or cleaned.lower() in {"unknown", "unspecified", "unknown/unknown"}:
        return None
    return cleaned


def _infer_bit_depth(pix_fmt: Optional[str], bits_per_raw_sample: Optional[str]) -> Optional[int]:
    if bits_per_raw_sample:
        try:
            bits = int(float(bits_per_raw_sample))
            if bits > 0:
                return bits
        except ValueError:
            pass
    if not pix_fmt:
        return None
    for token in pix_fmt.split(":"):
        if "p" in token:
            idx = token.find("p")
            digits = ""
            for ch in token[idx + 1 :]:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits:
                try:
                    return int(digits)
                except ValueError:
                    return None
    return None


def probe_video(path: Path) -> VideoInfo:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,bit_rate,avg_frame_rate,r_frame_rate,pix_fmt,bits_per_raw_sample,color_primaries,color_transfer,color_space,color_range,duration",
        "-show_entries",
        "format=bit_rate,duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    stream = streams[0] if streams else {}
    width = stream.get("width")
    height = stream.get("height")
    fmt = data.get("format") or {}
    bit_rate = stream.get("bit_rate") or fmt.get("bit_rate")
    avg_fps = _parse_fraction(stream.get("avg_frame_rate"))
    r_fps = _parse_fraction(stream.get("r_frame_rate"))
    fps = avg_fps or r_fps
    is_vfr = bool(avg_fps and r_fps and abs(avg_fps - r_fps) > _FPS_EPSILON)
    pix_fmt = stream.get("pix_fmt")
    bit_depth = _infer_bit_depth(pix_fmt, stream.get("bits_per_raw_sample"))
    color_primaries = _normalize_color(stream.get("color_primaries"))
    color_trc = _normalize_color(stream.get("color_trc") or stream.get("color_transfer"))
    colorspace = _normalize_color(stream.get("colorspace") or stream.get("color_space"))
    color_range = _normalize_color(stream.get("color_range"))
    if not color_range and pix_fmt and str(pix_fmt).startswith("yuvj"):
        # yuvj* is legacy full-range YUV (effectively yuv* + full range).
        color_range = "pc"

    duration = None
    for raw in (stream.get("duration"), fmt.get("duration")):
        if raw:
            try:
                duration = float(raw)
                break
            except ValueError:
                continue

    bitrate = None
    if bit_rate:
        try:
            bits = int(float(bit_rate))
            if bits > 0:
                bitrate = f"{max(1, round(bits / 1000))}k"
        except ValueError:
            bitrate = None

    return VideoInfo(
        width=width,
        height=height,
        bitrate=bitrate,
        fps=fps,
        avg_fps=avg_fps,
        r_fps=r_fps,
        is_vfr=is_vfr,
        duration=duration,
        pix_fmt=pix_fmt,
        bit_depth=bit_depth,
        color_primaries=color_primaries,
        color_trc=color_trc,
        colorspace=colorspace,
        color_range=color_range,
    )
