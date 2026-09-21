#!/usr/bin/env python3
"""
Extract frames from video with progress monitoring and detailed error reporting.
Usage:
    python extract_frames.py <video_path> <output_dir> [num_frames] [--fps FPS]
Arguments:
    video_path   : Path to the input video file
    output_dir   : Directory where frames will be saved
    num_frames   : Number of frames to extract
        1  -> extract 1 central frame (default)
        0  -> extract all frames (at --fps, default 1 fps)
        >1 -> extract up to N frames (at --fps, default 1 fps)
    --fps        : Frames per second to extract (default: 1.0)
Smart features:
    - Extracts duration from filename pattern (e.g. "18.25.00-18.25.51" -> 51s)
    - Uses ffmpeg's -t flag to limit output duration (ffmpeg stops itself!)
    - Non-blocking stdout reading with select() for accurate stall detection
    - Stops immediately when expected frame count is reached on disk
    - Absolute timeout based on expected duration
"""
import argparse
import logging
import os
import re
import select
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("frame_extractor")

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
STALL_THRESHOLD_SEC = 3.0       # Kill ffmpeg if progress stalls for this long
DURATION_MARGIN_SEC = 2.0       # Stop after duration + margin (for -t flag)
TIMEOUT_MULTIPLIER = 2.0        # Absolute timeout = duration * multiplier + buffer
TIMEOUT_BUFFER_SEC = 10.0       # Buffer added to absolute timeout
SELECT_TIMEOUT_SEC = 0.5        # Timeout for select() calls (non-blocking read)
SUCCESS_THRESHOLD_RATIO = 0.90  # Check frames on disk when reaching 90% of expected duration

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class VideoInfo:
    """Metadata about the source video."""
    duration: float = 0.0
    fps: float = 0.0
    total_frames: int = 0
    width: int = 0
    height: int = 0
    codec: str = ""
    duration_unknown: bool = False
    duration_source: str = ""

@dataclass
class ExtractionProgress:
    """Live progress reported by ffmpeg."""
    current_time: float = 0.0
    speed: str = ""
    fps: float = 0.0
    size_kb: int = 0
    percent: float = 0.0

@dataclass
class ExtractionResult:
    """Final result of the extraction process."""
    success: bool = False
    partial_success: bool = False
    frames_extracted: int = 0
    frames_expected: int = 0
    elapsed_seconds: float = 0.0
    error_message: str = ""
    error_code: Optional[int] = None
    output_files: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    stop_reason: str = ""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def require_binary(name: str) -> str:
    """Return the absolute path of a required binary or raise."""
    path = shutil.which(name)
    if not path:
        raise RuntimeError(
            f"Binary '{name}' not found in PATH. Please install it and retry."
        )
    return path

def extract_duration_from_filename(filename: str) -> Optional[float]:
    """
    Extract duration from filename pattern like:
        "18.25.00-18.25.51[M][_2180][0].h264"  -> 51.0s
        "09.00.00-09.05.30.mp4"                 -> 330.0s
    """
    pattern = (
        r"(?<!\d)"
        r"(\d{1,2})[.:](\d{1,2})[.:](\d{1,2})"
        r"\s*[-–—]\s*"
        r"(\d{1,2})[.:](\d{1,2})[.:](\d{1,2})"
        r"(?!\d)"
    )
    m = re.search(pattern, filename)
    if not m:
        return None
    h1, m1, s1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
    h2, m2, s2 = int(m.group(4)), int(m.group(5)), int(m.group(6))
    if not (0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= s1 <= 59):
        return None
    if not (0 <= h2 <= 23 and 0 <= m2 <= 59 and 0 <= s2 <= 59):
        return None
    start = h1 * 3600 + m1 * 60 + s1
    end = h2 * 3600 + m2 * 60 + s2
    if end <= start:
        return None
    return float(end - start)

def probe_video(video_path: Path) -> VideoInfo:
    """Use ffprobe to gather video metadata."""
    ffprobe = require_binary("ffprobe")
    info = VideoInfo()
    fname_duration = extract_duration_from_filename(video_path.name)
    if fname_duration is not None:
        info.duration = fname_duration
        info.duration_source = "filename"
        log.info(
            "Duration extracted from filename '%s': %.2fs",
            video_path.name, info.duration,
        )
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name,nb_frames",
        "-select_streams", "v:0",
        "-of", "json",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        log.warning("ffprobe timed out; using filename duration if available")
        if info.duration_source != "filename":
            info.duration_unknown = True
        return info
    except Exception as exc:
        log.warning("ffprobe failed: %s", exc)
        if info.duration_source != "filename":
            info.duration_unknown = True
        return info
    if proc.returncode != 0:
        log.warning(
            "ffprobe returned code %d; using filename duration if available",
            proc.returncode,
        )
        if info.duration_source != "filename":
            info.duration_unknown = True
        return info
    out = proc.stdout
    m = re.search(r'"duration"\s*:\s*"([^"]+)"', out)
    if m:
        try:
            probe_duration = float(m.group(1))
            if probe_duration > 0:
                info.duration = probe_duration
                info.duration_source = "ffprobe"
            elif info.duration_source != "filename":
                info.duration_unknown = True
        except ValueError:
            if info.duration_source != "filename":
                info.duration_unknown = True
    elif info.duration_source != "filename":
        info.duration_unknown = True
    m = re.search(r'"width"\s*:\s*(\d+)', out)
    if m:
        info.width = int(m.group(1))
    m = re.search(r'"height"\s*:\s*(\d+)', out)
    if m:
        info.height = int(m.group(1))
    m = re.search(r'"codec_name"\s*:\s*"([^"]+)"', out)
    if m:
        info.codec = m.group(1)
    m = re.search(r'"r_frame_rate"\s*:\s*"(\d+)/(\d+)"', out)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den:
            info.fps = num / den
    m = re.search(r'"nb_frames"\s*:\s*"(\d+)"', out)
    if m:
        info.total_frames = int(m.group(1))
    elif info.duration and info.fps:
        info.total_frames = int(info.duration * info.fps)
    return info

def build_ffmpeg_cmd(video_path: Path, output_dir: Path,
                     num_frames: int, video_info: VideoInfo, fps: float) -> list:
    """Build the ffmpeg command line according to the extraction mode."""
    ffmpeg = require_binary("ffmpeg")
    is_raw_h264 = video_path.suffix.lower() == ".h264"
    cmd = [ffmpeg, "-y", "-loglevel", "info", "-stats"]
    cmd += ["-thread_queue_size", "512"]
    cmd += ["-fflags", "+genpts"]
    if is_raw_h264:
        cmd += ["-f", "h264", "-r", "10"]
    if num_frames == 1:
        seek = max(video_info.duration / 2.0, 0.0) if video_info.duration else 5.0
        cmd += [
            "-ss", f"{seek:.3f}",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "1",
            str(output_dir / "frame_0001.jpg"),
        ]
    else:
        cmd += ["-i", str(video_path)]
        vf_filters = [f"fps={fps}"]
        if video_info.width > 1920 or video_info.height > 1080:
            vf_filters.append(
                "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease"
            )
            log.info(
                "High resolution detected (%dx%d), scaling down to reduce memory usage",
                video_info.width, video_info.height,
            )
        cmd += ["-vf", ",".join(vf_filters)]
        cmd += ["-vsync", "0"]
        cmd += ["-q:v", "1"]
        cmd += ["-max_muxing_queue_size", "512"]
        if video_info.duration > 0 and not video_info.duration_unknown:
            output_duration = video_info.duration + DURATION_MARGIN_SEC
            cmd += ["-t", f"{output_duration:.2f}"]
            log.info(
                "Using -t flag to limit output to %.2fs (ffmpeg will stop automatically)",
                output_duration,
            )
        if num_frames > 1:
            cmd += ["-vframes", str(num_frames)]
        cmd += [str(output_dir / "frame_%04d.jpg")]
    return cmd

def expected_frame_count(num_frames: int, video_info: VideoInfo, fps: float) -> int:
    """How many frames we should end up with."""
    if num_frames == 1:
        return 1
    if num_frames > 1:
        return num_frames
    if video_info.duration_unknown or video_info.duration == 0:
        return 0
    return max(int(video_info.duration * fps), 1)

_PROGRESS_RE = re.compile(
    r"time=(\d+):(\d+):(\d+\.\d+).*?speed=\s*([0-9.]+)x",
    re.IGNORECASE,
)

def parse_progress(line: str, total_duration: float) -> Optional[ExtractionProgress]:
    """Parse a single ffmpeg stderr line into an ExtractionProgress."""
    m = _PROGRESS_RE.search(line)
    if not m:
        return None
    h, mn, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
    current = h * 3600 + mn * 60 + s
    speed = m.group(4)
    p = ExtractionProgress(current_time=current, speed=f"{speed}x")
    if total_duration > 0:
        p.percent = min(100.0, current / total_duration * 100.0)
    return p

def count_generated_frames(output_dir: Path) -> list:
    """Return a sorted list of frame files actually present on disk."""
    if not output_dir.exists():
        return []
    return sorted(
        p for p in output_dir.glob("frame_*.jpg")
        if p.is_file() and p.stat().st_size > 0
    )

def quick_frame_count(output_dir: Path) -> int:
    """Fast count of frame files on disk (no sorting, no size check)."""
    if not output_dir.exists():
        return 0
    count = 0
    for p in output_dir.iterdir():
        if p.name.startswith("frame_") and p.name.endswith(".jpg"):
            count += 1
    return count

def is_likely_successful_extraction(
    result: ExtractionResult,
    video_info: VideoInfo,
    last_progress: Optional[ExtractionProgress],
    error_lines: list,
) -> bool:
    """
    Determine if extraction was actually successful despite non-zero exit code.
    """
    if result.frames_expected > 0:
        if result.frames_extracted >= result.frames_expected:
            return True
    if video_info.duration > 0 and last_progress:
        progress_ratio = last_progress.current_time / video_info.duration
        if progress_ratio >= 0.90 and result.frames_extracted > 0:
            return True
    if video_info.duration_unknown and result.frames_extracted > 0:
        critical_errors = [
            "Error opening",
            "Invalid data found",
            "Could not open",
            "No such file",
        ]
        has_critical = any(
            any(crit in line for crit in critical_errors)
            for line in error_lines
        )
        if not has_critical:
            return True
    return False

# ---------------------------------------------------------------------------
# Core extractor
# ---------------------------------------------------------------------------
class FrameExtractor:
    """Resilient frame extractor with live progress monitoring."""
    def __init__(self, video_path: str, output_dir: str, num_frames: int = 1, fps: float = 1.0):
        self.video_path = Path(video_path).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.num_frames = num_frames
        self.fps = fps if fps and fps > 0 else 1.0
        self.video_info: Optional[VideoInfo] = None

    def _validate_inputs(self) -> None:
        if not self.video_path.exists():
            raise FileNotFoundError(f"Video file not found: {self.video_path}")
        if not self.video_path.is_file():
            raise ValueError(f"Path is not a regular file: {self.video_path}")
        if self.num_frames < 0:
            raise ValueError(f"num_frames must be >= 0, got {self.num_frames}")
        if self.fps <= 0:
            raise ValueError(f"fps must be > 0, got {self.fps}")

    def run(self) -> ExtractionResult:
        """Execute the extraction and return a detailed result."""
        result = ExtractionResult()
        start = time.monotonic()
        try:
            self._validate_inputs()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            log.info("Probing video: %s", self.video_path)
            self.video_info = probe_video(self.video_path)
            if self.video_info.duration_unknown:
                log.warning(
                    "Video duration unknown. Progress will show elapsed time only. "
                    "Stall detection (%.0fs) will be used to stop extraction.",
                    STALL_THRESHOLD_SEC,
                )
            else:
                log.info(
                    "Duration: %.2fs (source: %s).",
                    self.video_info.duration,
                    self.video_info.duration_source,
                )
            absolute_timeout = (
                self.video_info.duration * TIMEOUT_MULTIPLIER + TIMEOUT_BUFFER_SEC
            )
            log.info(
                "Absolute timeout: %.1fs (%.1f * %.1f + %.1f)",
                absolute_timeout,
                self.video_info.duration,
                TIMEOUT_MULTIPLIER,
                TIMEOUT_BUFFER_SEC,
            )
            log.info(
                "Video info -> fps=%.2f, resolution=%dx%d, codec=%s",
                self.video_info.fps,
                self.video_info.width, self.video_info.height,
                self.video_info.codec or "unknown",
            )
            result.frames_expected = expected_frame_count(
                self.num_frames, self.video_info, self.fps
            )
            if result.frames_expected == 0 and self.num_frames == 0:
                log.info(
                    "Extraction mode: all frames @%gfps (duration unknown, "
                    "will extract until EOF or stall)", self.fps,
                )
            else:
                log.info(
                    "Extraction mode: %s (expected ~%d frames)",
                    "single central frame" if self.num_frames == 1
                    else f"all frames @{self.fps:g}fps" if self.num_frames == 0
                    else f"up to {self.num_frames} frames @{self.fps:g}fps",
                    result.frames_expected,
                )
            cmd = build_ffmpeg_cmd(
                self.video_path, self.output_dir,
                self.num_frames, self.video_info, self.fps,
            )
            log.info("Running: %s", " ".join(cmd))
            self._run_ffmpeg(cmd, result)
        except FileNotFoundError as exc:
            result.error_message = f"Missing file: {exc}"
            log.error(result.error_message)
            result.stop_reason = "error"
        except ValueError as exc:
            result.error_message = f"Invalid argument: {exc}"
            log.error(result.error_message)
            result.stop_reason = "error"
        except RuntimeError as exc:
            result.error_message = f"Runtime error: {exc}"
            log.error(result.error_message)
            result.stop_reason = "error"
        except subprocess.TimeoutExpired as exc:
            result.error_message = f"ffmpeg timed out: {exc}"
            log.error(result.error_message)
            result.stop_reason = "error"
        except KeyboardInterrupt:
            result.error_message = "Interrupted by user (Ctrl+C)"
            log.warning(result.error_message)
            result.stop_reason = "interrupted"
        except Exception as exc:  # noqa: BLE001
            result.error_message = f"Unexpected error: {type(exc).__name__}: {exc}"
            log.exception("Unexpected error during extraction")
            result.stop_reason = "error"
        finally:
            result.elapsed_seconds = time.monotonic() - start
            result.output_files = count_generated_frames(self.output_dir)
            result.frames_extracted = len(result.output_files)
        return result

    def _run_ffmpeg(self, cmd: list, result: ExtractionResult) -> None:
        """Spawn ffmpeg, stream its stderr with non-blocking reads, track progress."""
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=0,
        )
        last_progress: Optional[ExtractionProgress] = None
        error_lines: list = []
        total_duration = self.video_info.duration if self.video_info else 0.0
        frames_expected = result.frames_expected if hasattr(result, 'frames_expected') else 0
        # Early-stop tracking
        last_reported_time = 0.0
        last_advance_wall = time.monotonic()
        extraction_start = time.monotonic()
        stopped_early = False
        stop_reason = ""
        last_disk_check = 0.0
        disk_check_interval = 2.0  # Check disk every 2s
        # Calculate absolute timeout if duration is known
        absolute_timeout = None
        if total_duration > 0:
            absolute_timeout = (
                total_duration * TIMEOUT_MULTIPLIER + TIMEOUT_BUFFER_SEC
            )
        # Non-blocking read using select()
        line_buffer = b""
        try:
            assert proc.stdout is not None
            stdout_fd = proc.stdout.fileno()
            while True:
                if proc.poll() is not None:
                    remaining = proc.stdout.read()
                    if remaining:
                        line_buffer += remaining
                    while b'\n' in line_buffer or b'\r' in line_buffer:
                        line_end = min(
                            (line_buffer.index(b'\n') if b'\n' in line_buffer else len(line_buffer)),
                            (line_buffer.index(b'\r') if b'\r' in line_buffer else len(line_buffer))
                        )
                        raw_line = line_buffer[:line_end].decode('utf-8', errors='ignore').rstrip()
                        line_buffer = line_buffer[line_end+1:]
                        if raw_line:
                            prog = parse_progress(raw_line, total_duration)
                            if prog:
                                last_progress = prog
                                self._print_progress(prog, total_duration)
                            else:
                                error_lines.append(raw_line)
                                if len(error_lines) > 50:
                                    error_lines.pop(0)
                    break
                ready, _, _ = select.select([stdout_fd], [], [], SELECT_TIMEOUT_SEC)
                if ready:
                    chunk = os.read(stdout_fd, 4096)
                    if not chunk:
                        break
                    line_buffer += chunk
                    while b'\n' in line_buffer or b'\r' in line_buffer:
                        line_end = min(
                            (line_buffer.index(b'\n') if b'\n' in line_buffer else len(line_buffer)),
                            (line_buffer.index(b'\r') if b'\r' in line_buffer else len(line_buffer))
                        )
                        raw_line = line_buffer[:line_end].decode('utf-8', errors='ignore').rstrip()
                        line_buffer = line_buffer[line_end+1:]
                        if not raw_line:
                            continue
                        prog = parse_progress(raw_line, total_duration)
                        if prog:
                            now = time.monotonic()
                            # 1) FRAMES ON DISK CHECK: Already have enough?
                            if frames_expected > 0 and (now - last_disk_check) > disk_check_interval:
                                last_disk_check = now
                                on_disk = quick_frame_count(self.output_dir)
                                if on_disk >= frames_expected:
                                    log.info(
                                        "All %d expected frames already on disk. "
                                        "Stopping ffmpeg immediately.",
                                        frames_expected,
                                    )
                                    stop_reason = "frames_complete"
                                    stopped_early = True
                                    break
                            # 2) Duration threshold reached? Check frames on disk.
                            if total_duration > 0 and prog.current_time >= total_duration * SUCCESS_THRESHOLD_RATIO:
                                on_disk = quick_frame_count(self.output_dir)
                                if frames_expected > 0 and on_disk >= frames_expected:
                                    log.info(
                                        "Success: %d frames on disk >= %d expected "
                                        "(time=%.2fs, %.0f%% of %.2fs). Stopping.",
                                        on_disk, frames_expected,
                                        prog.current_time,
                                        prog.current_time / total_duration * 100,
                                        total_duration,
                                    )
                                    stop_reason = "frames_complete"
                                    stopped_early = True
                                    break
                            # If we don't have all frames yet, continue until -t flag stops ffmpeg
                            # 3) Stall detection
                            if prog.current_time > last_reported_time:
                                last_reported_time = prog.current_time
                                last_advance_wall = now
                            elif (now - last_advance_wall) > STALL_THRESHOLD_SEC:
                                log.info(
                                    "ffmpeg stalled: no progress for %.1fs (last time=%.2fs). "
                                    "Stopping early.",
                                    now - last_advance_wall, last_reported_time,
                                )
                                stop_reason = "stalled"
                                stopped_early = True
                                break
                            # 4) Absolute timeout
                            if absolute_timeout and (now - extraction_start) > absolute_timeout:
                                log.info(
                                    "Absolute timeout reached (%.1fs > %.1fs). Stopping.",
                                    now - extraction_start, absolute_timeout,
                                )
                                stop_reason = "timeout"
                                stopped_early = True
                                break
                            last_progress = prog
                            self._print_progress(prog, total_duration)
                        else:
                            error_lines.append(raw_line)
                            if len(error_lines) > 50:
                                error_lines.pop(0)
                else:
                    now = time.monotonic()
                    # Stall detection (even without new progress lines)
                    if (now - last_advance_wall) > STALL_THRESHOLD_SEC and last_reported_time > 0:
                        # Before killing, check if frames are already complete
                        if frames_expected > 0:
                            on_disk = quick_frame_count(self.output_dir)
                            if on_disk >= frames_expected:
                                log.info(
                                    "ffmpeg stalled but all %d frames already on disk. "
                                    "Stopping successfully.",
                                    frames_expected,
                                )
                                stop_reason = "frames_complete"
                                stopped_early = True
                                break
                        log.info(
                            "ffmpeg stalled: no output for %.1fs (last time=%.2fs). "
                            "Stopping early.",
                            now - last_advance_wall, last_reported_time,
                        )
                        stop_reason = "stalled"
                        stopped_early = True
                        break
                    if absolute_timeout and (now - extraction_start) > absolute_timeout:
                        log.info(
                            "Absolute timeout reached (%.1fs > %.1fs). Stopping.",
                            now - extraction_start, absolute_timeout,
                        )
                        stop_reason = "timeout"
                        stopped_early = True
                        break
                if stopped_early:
                    break
        finally:
            if stopped_early:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        log.warning("ffmpeg did not exit gracefully, forcing kill")
                        proc.kill()
                        proc.wait(timeout=2)
                except Exception as exc:
                    log.warning("Error terminating ffmpeg: %s", exc)
            else:
                proc.wait()
            print()
            result.output_files = count_generated_frames(self.output_dir)
            result.frames_extracted = len(result.output_files)
            if stopped_early:
                result.stop_reason = stop_reason
                if result.frames_extracted > 0:
                    result.success = True
                    if stop_reason == "frames_complete":
                        result.warnings.append(
                            f"Stopped early: all {result.frames_expected} expected frames "
                            f"already on disk. Extracted {result.frames_extracted} frames."
                        )
                    elif stop_reason == "stalled":
                        result.warnings.append(
                            f"Stopped early: ffmpeg stalled (no progress for {STALL_THRESHOLD_SEC:.0f}s). "
                            f"Extracted {result.frames_extracted} frames."
                        )
                    elif stop_reason == "timeout":
                        result.warnings.append(
                            f"Stopped early: absolute timeout reached. "
                            f"Extracted {result.frames_extracted} frames."
                        )
                    log.info(
                        "Early stop (%s). Extracted %d frames.",
                        stop_reason, result.frames_extracted,
                    )
                else:
                    result.error_message = (
                        f"Stopped early ({stop_reason}) but no frames were extracted."
                    )
                    log.error(result.error_message)
                    return
            if proc.returncode != 0:
                result.error_code = proc.returncode
                result.stop_reason = "error"
                if is_likely_successful_extraction(
                    result, self.video_info, last_progress, error_lines
                ):
                    result.success = True
                    result.stop_reason = "completed_with_warnings"
                    result.warnings.append(
                        f"ffmpeg exited with code {proc.returncode}, but all expected frames were extracted."
                    )
                    if error_lines:
                        unique_warnings = list(set(
                            line for line in error_lines
                            if "Invalid NAL" in line or "buffers queued" in line
                        ))[:5]
                        for warn in unique_warnings:
                            result.warnings.append(warn)
                    log.info(
                        "Extraction completed successfully (with warnings). "
                        "Extracted %d frames.",
                        result.frames_extracted,
                    )
                else:
                    tail = "\n".join(error_lines[-20:]) if error_lines else "<no output>"
                    if proc.returncode == -9:
                        result.error_message = (
                            f"ffmpeg was killed by signal SIGKILL (code -9).\n"
                            f"This usually means the system ran out of memory (OOM Killer).\n"
                            f"Check with: dmesg | tail -50 | grep -i oom\n"
                            f"--- ffmpeg tail ---\n{tail}\n--- end ---"
                        )
                    else:
                        result.error_message = (
                            f"ffmpeg exited with code {proc.returncode}.\n"
                            f"--- ffmpeg tail ---\n{tail}\n--- end ---"
                        )
                    log.error(result.error_message)
                    return
            else:
                result.stop_reason = "completed"
                if last_progress:
                    log.info(
                        "ffmpeg finished. Last reported time=%.2fs, speed=%s",
                        last_progress.current_time, last_progress.speed,
                    )

    def _print_progress(self, p: ExtractionProgress, total: float) -> None:
        """Render a single-line progress indicator."""
        if total > 0:
            bar_w = 30
            filled = int(bar_w * p.percent / 100)
            bar = "#" * filled + "-" * (bar_w - filled)
            msg = (
                f"\r[{bar}] {p.percent:5.1f}%  "
                f"time={p.current_time:7.2f}/{total:.2f}s  "
                f"speed={p.speed}"
            )
        else:
            msg = (
                f"\rtime={p.current_time:7.2f}s  speed={p.speed}  "
                f"(duration unknown)"
            )
        sys.stdout.write(msg)
        sys.stdout.flush()

# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def print_summary(result: ExtractionResult) -> None:
    """Print a human-friendly summary of what happened."""
    print("\n" + "=" * 60)
    print(" EXTRACTION SUMMARY")
    print("=" * 60)
    if result.success:
        print(f" Status           : OK")
    elif result.partial_success:
        print(f" Status           : PARTIAL SUCCESS")
    else:
        print(f" Status           : FAILED")
    print(f" Frames extracted : {result.frames_extracted}")
    print(f" Frames expected  : {result.frames_expected if result.frames_expected > 0 else 'unknown'}")
    print(f" Elapsed time     : {result.elapsed_seconds:.2f}s")
    print(f" Stop reason      : {result.stop_reason or 'n/a'}")
    if result.error_code is not None:
        if result.success:
            print(f" ffmpeg exit code : {result.error_code} (ignored - extraction successful)")
        elif result.error_code == -9:
            print(f" ffmpeg exit code : -9 (SIGKILL - likely OOM)")
        else:
            print(f" ffmpeg exit code : {result.error_code}")
    if result.warnings:
        print("-" * 60)
        print(" Warnings (non-fatal):")
        for warn in result.warnings[:10]:
            print(f"   {warn}")
    if result.error_message:
        print("-" * 60)
        print(" Error details:")
        for line in result.error_message.splitlines():
            print(f"   {line}")
    if result.output_files:
        print("-" * 60)
        print(f" Generated files ({len(result.output_files)}):")
        for f in result.output_files[:10]:
            print(f"   {f}")
        if len(result.output_files) > 10:
            print(f"   ... and {len(result.output_files) - 10} more")
    print("=" * 60)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract frames from a video with live progress monitoring.",
    )
    parser.add_argument("video_path", help="Path to the input video")
    parser.add_argument("output_dir", help="Directory to save frames into")
    parser.add_argument(
        "num_frames", nargs="?", type=int, default=1,
        help="1 = central frame (default), 0 = all @fps, >1 = up to N @fps",
    )
    parser.add_argument(
        "--fps", type=float, default=1.0,
        help="Frames per second to extract when num_frames is 0 or >1 (default: 1.0)",
    )
    args = parser.parse_args()
    extractor = FrameExtractor(args.video_path, args.output_dir, args.num_frames, args.fps)
    result = extractor.run()
    print_summary(result)
    return 0 if result.success else 1

if __name__ == "__main__":
    sys.exit(main())