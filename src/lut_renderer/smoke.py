from __future__ import annotations

from pathlib import Path

from .ffmpeg import build_command
from .media_info import VideoInfo
from .models import ProcessingParams


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    dummy_in = Path("input.mov")
    dummy_out = Path("output.mp4")
    dummy_lut = Path("look.cube")

    # 1) streamcopy + filters must be rejected.
    params = ProcessingParams(video_codec="copy", pix_fmt="", bit_depth_policy="preserve")
    try:
        build_command(dummy_in, dummy_out, params, lut_path=dummy_lut)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for video_codec=copy with lut_path")

    # 2) preserve/auto with 10-bit input should pick a 10-bit pix_fmt when possible.
    ten_bit = VideoInfo(bit_depth=10, pix_fmt="yuv420p10le")
    params = ProcessingParams(video_codec="libx265", pix_fmt="", bit_depth_policy="preserve")
    cmd = build_command(dummy_in, dummy_out, params, lut_path=None, source_info=ten_bit)
    joined = " ".join(cmd)
    _assert("-pix_fmt yuv420p10le" in joined, f"expected 10-bit pix_fmt, got: {joined}")

    # 3) When LUT is applied, default output tags must be Rec.709 / tv range.
    params = ProcessingParams(video_codec="libx264", pix_fmt="", bit_depth_policy="force_8bit")
    cmd = build_command(dummy_in, dummy_out, params, lut_path=dummy_lut, source_info=ten_bit)
    joined = " ".join(cmd)
    _assert("-color_primaries bt709" in joined, "missing -color_primaries bt709")
    _assert("-color_trc bt709" in joined, "missing -color_trc bt709")
    _assert("-colorspace bt709" in joined, "missing -colorspace bt709")
    _assert("-color_range tv" in joined, "missing -color_range tv")

    print("smoke ok")


if __name__ == "__main__":
    run()

