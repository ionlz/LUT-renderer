from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .ffmpeg import build_command, build_pipeline
from .media_info import probe_video
from .models import ProcessingParams, Task, TaskStatus

_DURATION_RE = re.compile(r"Duration: (?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)")
_TIME_RE = re.compile(r"time=(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)")


def _time_to_seconds(hours: str, minutes: str, seconds: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


class TaskSignals(QObject):
    progress = Signal(str, int)
    status = Signal(str, str)
    finished = Signal(str, str)
    log = Signal(str, str)


class TaskRunner(QRunnable):
    def __init__(self, task: Task, ffmpeg_bin: str = "ffmpeg") -> None:
        super().__init__()
        self.task = task
        self.ffmpeg_bin = ffmpeg_bin
        self.signals = TaskSignals()
        self._process = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
            except Exception:
                pass

    def run(self) -> None:
        import subprocess
        import shlex

        self.signals.status.emit(self.task.task_id, TaskStatus.RUNNING.value)
        self._log("开始")
        self.task.started_at = time.time()

        try:
            stages = build_pipeline(self.task, ffmpeg_bin=self.ffmpeg_bin)
            if not stages:
                raise RuntimeError("No ffmpeg stages built.")

            for index, stage in enumerate(stages):
                if self._cancelled:
                    break
                stage_label = f"阶段 {index + 1}/{len(stages)}: {stage.name}"
                self._log(stage_label)

                stage_info = self.task.source_info
                if stage.probe_source:
                    try:
                        stage_info = probe_video(stage.source_path)
                    except Exception as exc:
                        stage_info = None
                        self._log(f"提示: 阶段输入探测失败（将按未知处理）: {exc}")

                stage_cmd = build_command(
                    source=stage.source_path,
                    output=stage.output_path,
                    params=stage.params,
                    lut_path=stage.lut_path,
                    ffmpeg_bin=self.ffmpeg_bin,
                    source_info=stage_info,
                    notes=stage.notes,
                )
                for note in stage.notes:
                    self._log(note)
                self._log(f"命令: {shlex.join(stage_cmd)}")

                progress_base = 0
                progress_span = 100
                if len(stages) > 1:
                    progress_span = 50
                    progress_base = 0 if index == 0 else 50
                is_final = index == len(stages) - 1

                result = self._run_stage(
                    stage_cmd,
                    subprocess,
                    progress_base,
                    progress_span,
                    is_final,
                )
                if result is None:
                    self.signals.status.emit(self.task.task_id, TaskStatus.CANCELED.value)
                    self._log("已取消")
                    self.signals.finished.emit(self.task.task_id, TaskStatus.CANCELED.value)
                    return
                if not result:
                    retcode = self._process.returncode if self._process else -1
                    self.signals.status.emit(
                        self.task.task_id, f"{TaskStatus.FAILED.value}: exit {retcode}"
                    )
                    self._log(f"失败：退出码 {retcode}")
                    self.signals.finished.emit(self.task.task_id, TaskStatus.FAILED.value)
                    return

            if self.task.cover_path:
                self._extract_cover(self.task, subprocess)

            for stage in stages:
                if stage.cleanup_on_success and stage.output_path.exists():
                    try:
                        stage.output_path.unlink()
                    except Exception:
                        pass

            self.signals.progress.emit(self.task.task_id, 100)
            self.signals.status.emit(self.task.task_id, TaskStatus.COMPLETED.value)
            self._log("完成")
            self.signals.finished.emit(self.task.task_id, TaskStatus.COMPLETED.value)

        except Exception as exc:
            self.signals.status.emit(self.task.task_id, f"{TaskStatus.FAILED.value}: {exc}")
            self._log(f"失败：{exc}")
            self.signals.finished.emit(self.task.task_id, TaskStatus.FAILED.value)

    def _run_stage(
        self,
        cmd: List[str],
        subprocess_module,
        progress_base: int,
        progress_span: int,
        is_final: bool,
    ) -> Optional[bool]:
        duration = None
        last_progress = -1

        self._process = subprocess_module.Popen(
            cmd,
            stdout=subprocess_module.PIPE,
            stderr=subprocess_module.STDOUT,
            text=True,
            bufsize=1,
        )

        if not self._process.stdout:
            raise RuntimeError("Failed to capture FFmpeg output.")

        for line in self._process.stdout:
            if self._cancelled:
                break
            message = line.strip()
            if message:
                self._log(message)

            if duration is None:
                match = _DURATION_RE.search(line)
                if match:
                    duration = _time_to_seconds(match.group("h"), match.group("m"), match.group("s"))
                    continue

            match = _TIME_RE.search(line)
            if match and duration:
                elapsed = _time_to_seconds(match.group("h"), match.group("m"), match.group("s"))
                stage_progress = int((elapsed / duration) * progress_span)
                if not is_final:
                    stage_progress = min(stage_progress, max(0, progress_span - 1))
                progress = min(progress_base + stage_progress, 99 if not is_final else 100)
                if progress != last_progress:
                    last_progress = progress
                    self.signals.progress.emit(self.task.task_id, progress)

        if self._cancelled:
            if self._process and self._process.poll() is None:
                self._process.kill()
            return None

        retcode = self._process.wait()
        if retcode == 0:
            if not is_final:
                self.signals.progress.emit(self.task.task_id, progress_base + progress_span)
            return True
        return False

    def _log(self, message: str) -> None:
        self.signals.log.emit(self.task.task_id, message)

    def _extract_cover(self, task: Task, subprocess_module) -> None:
        self._log("生成封面图")
        source = task.output_path if task.output_path.exists() else task.source_path
        cmd = [
            self.ffmpeg_bin,
            "-hide_banner",
            "-y",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(task.cover_path),
        ]
        subprocess_module.run(
            cmd,
            stdout=subprocess_module.DEVNULL,
            stderr=subprocess_module.DEVNULL,
            check=True,
        )
        self._log(f"封面已保存：{task.cover_path}")


class TaskManager(QObject):
    task_added = Signal(str)
    task_updated = Signal(str)
    task_progress = Signal(str, int)
    queue_finished = Signal()
    task_log = Signal(str, str)

    def __init__(self, max_concurrency: int = 2, ffmpeg_bin: str = "ffmpeg") -> None:
        super().__init__()
        self.ffmpeg_bin = ffmpeg_bin
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(max_concurrency)
        self.tasks: Dict[str, Task] = {}
        self.runners: Dict[str, TaskRunner] = {}

    def set_max_concurrency(self, value: int) -> None:
        self.thread_pool.setMaxThreadCount(max(1, value))

    def add_task(self, task: Task) -> None:
        self.tasks[task.task_id] = task
        self.task_added.emit(task.task_id)

    def add_tasks(self, tasks: List[Task]) -> None:
        for task in tasks:
            self.add_task(task)

    def start_all(self) -> None:
        for task_id, task in list(self.tasks.items()):
            if task.status != TaskStatus.PENDING:
                continue
            runner = TaskRunner(task, ffmpeg_bin=self.ffmpeg_bin)
            runner.signals.progress.connect(self._on_progress)
            runner.signals.status.connect(self._on_status)
            runner.signals.finished.connect(self._on_finished)
            runner.signals.log.connect(self._on_log)
            self.runners[task_id] = runner
            task.status = TaskStatus.RUNNING
            self.task_updated.emit(task_id)
            self.thread_pool.start(runner)

    def cancel_task(self, task_id: str) -> None:
        runner = self.runners.get(task_id)
        if runner:
            runner.cancel()
        task = self.tasks.get(task_id)
        if task:
            task.status = TaskStatus.CANCELED
            self.task_updated.emit(task_id)

    def clear_completed(self) -> None:
        remove_ids = [
            task_id
            for task_id, task in self.tasks.items()
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
        ]
        for task_id in remove_ids:
            self.tasks.pop(task_id, None)
            self.runners.pop(task_id, None)
            self.task_updated.emit(task_id)

    def remove_task(self, task_id: str) -> None:
        runner = self.runners.get(task_id)
        if runner:
            runner.cancel()
        self.runners.pop(task_id, None)
        if task_id in self.tasks:
            self.tasks.pop(task_id, None)
            self.task_updated.emit(task_id)

    def _on_progress(self, task_id: str, progress: int) -> None:
        task = self.tasks.get(task_id)
        if not task:
            return
        task.progress = progress
        self.task_progress.emit(task_id, progress)

    def _on_status(self, task_id: str, status: str) -> None:
        task = self.tasks.get(task_id)
        if not task:
            return
        if status.startswith(TaskStatus.FAILED.value):
            task.status = TaskStatus.FAILED
            task.error = status
        elif status in TaskStatus._value2member_map_:
            task.status = TaskStatus(status)
        self.task_updated.emit(task_id)

    def _on_finished(self, task_id: str, status: str) -> None:
        task = self.tasks.get(task_id)
        if task:
            task.finished_at = time.time()
        self.runners.pop(task_id, None)
        if not self.runners:
            self.queue_finished.emit()

    def _on_log(self, task_id: str, message: str) -> None:
        self.task_log.emit(task_id, message)
