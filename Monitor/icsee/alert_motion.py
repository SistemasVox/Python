from __future__ import annotations

import errno
import fcntl
import hashlib
import heapq
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, IO, Iterable, Optional, Sequence, TypeVar

try:
    import cv2
except ImportError:
    cv2 = None


def resolve_project_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    if script_dir.name in {"gravacoes", "utils", "scripts", "bin", "processamento_img"}:
        return script_dir.parent
    return script_dir


PROJECT_ROOT = resolve_project_root()


class Config:
    BASE_DIR: Path = (PROJECT_ROOT / "gravacoes").resolve()
    WHATSAPP_NUMBERS: tuple[str, ...] = (
        "000000000000",
        "000000000000",
    )
    CAMERA_LINK: str = "https://svox.com.br/"
    WHATSAPP_SCRIPT: Path = Path(
        "/var/www/opt/scripts/msg/send_whatsapp_media.py"
    ).expanduser().resolve()
    EXTRACT_FRAMES_SCRIPT: Path = (
        PROJECT_ROOT / "processamento_img" / "extract_frames.py"
    ).resolve()
    MELHOR_FOTO_SCRIPT: Path = (
        PROJECT_ROOT / "processamento_img" / "melhor_foto.py"
    ).resolve()
    FFMPEG_CMD: str = "/usr/bin/ffmpeg"
    FFPROBE_CMD: str = "/usr/bin/ffprobe"
    MELHOR_FOTO_ENGINE: str = "yolo"
    MELHOR_FOTO_LOGS: bool = False
    MAX_ALERT_RETRIES: int = 3
    WHATSAPP_TIMEOUT: int = 60
    WHATSAPP_VIDEO_TIMEOUT: int = 180
    FRAME_EXTRACT_TIMEOUT: int = 60
    FRAME_EXTRACT_TIMEOUT_FAST: int = 15
    EXTRACT_ALL_TIMEOUT: int = 600
    MELHOR_FOTO_ENHANCED_TIMEOUT: int = 300
    MELHOR_FOTO_LATE_TIMEOUT: int = 300
    MELHOR_FOTO_LATE_REBUILD_TIMEOUT: int = 600
    MELHOR_FOTO_IMMEDIATE_TIMEOUT: int = 180
    MELHOR_FOTO_MAX_CANDIDATES: int = 24
    WINDOW_IDLE_CLOSE: int = 30
    # Arquivos cujo início ocorre poucos segundos após o fim anterior ainda
    # pertencem ao mesmo evento de movimento.
    WINDOW_TOLERANCE_SECONDS: int = int(
        os.getenv("WINDOW_TOLERANCE_SECONDS", "5")
    )
    IMMEDIATE_DEBOUNCE: int = 0
    ENHANCED_START_DELAY: int = 0
    LATE_COMPARE_DELAY: int = 15
    ENHANCED_MAX_PHOTOS: int = 3
    ENHANCED_ANALYSIS_MAX_FRAMES: int = 24
    LATE_MAX_PHOTOS: int = 3
    LATE_MAX_VIDEOS: int = 1
    LATE_FRAMES_PER_VIDEO: int = 2
    LATE_REBUILD_MOTION: bool = True
    LATE_REBUILD_MAX_VIDEOS: int = 1
    DB_PATH: Path = BASE_DIR / ".alert_cache.db"
    LOG_FILE: Path = BASE_DIR / "alert_motion.log"
    TMP_DIR: Path = BASE_DIR / ".tmp_frames"
    LOCK_FILE: Path = BASE_DIR / ".alert_motion.lock"
    WORKERS_ENHANCED: int = 1
    WORKERS_LATE: int = 1
    ENGINE_CONCURRENCY: int = 1
    TMP_FILE_TTL: int = 900
    RETENTION_DAYS: int = 30
    MIN_IMAGE_BYTES: int = 1024
    MIN_VIDEO_BYTES: int = 1024
    ENHANCED_MOTION_ENABLED: bool = True
    # Mantém a precisão temporal do recorte; a redução de custo ocorre na
    # amostra enviada ao YOLO, não no material usado para montar o vídeo.
    MOTION_FRAME_COUNT: int = 45
    MOTION_MIN_SECONDS: int = 3
    MOTION_MAX_SECONDS: int = 15
    MOTION_FPS: int = 3
    MOTION_VIDEO_WIDTH: int = 1280
    MOTION_DEDUP_THRESHOLD: float = 8.0
    MOTION_SALIENCE_THRESHOLD: float = 15.0
    MOTION_SALIENCE_FLOOR: float = 0.01
    MOTION_ANCHOR_SMOOTH: int = 3
    MOTION_POSITION_PENALTY: float = 0.50
    # Movimento fraco ou quase empatado não é evidência suficiente para
    # descartar uma das câmeras; nesses casos, preserva-se a imagem completa.
    MOTION_HALF_MIN_SALIENCE: float = 0.20
    MOTION_HALF_MIN_MARGIN: float = 0.05
    MOTION_VIDEO_TIMEOUT: int = 120
    # Qualidade dos frames temporários e do MP4 reconstruído. Valores menores
    # significam menos perda; ficam configuráveis para ajustar custo/tamanho.
    MOTION_JPEG_QUALITY: int = int(os.getenv("MOTION_JPEG_QUALITY", "1"))
    MOTION_VIDEO_CRF: int = int(os.getenv("MOTION_VIDEO_CRF", "18"))
    MOTION_VIDEO_PRESET: str = os.getenv("MOTION_VIDEO_PRESET", "fast")
    # Resolução suficiente para preservar o pico de movimento, com menor
    # custo de redimensionamento e memória que 320x240.
    MOTION_ANALYSIS_WIDTH: int = 240
    MOTION_ANALYSIS_HEIGHT: int = 180
    ACTIVE_CAMERA_ONLY: bool = True
    LOOP_IDLE_TIMEOUT: int = 120
    LOOP_POLL_INTERVAL: int = 10
    SHARPNESS_MAX_PIXELS: int = 500_000


Config.BASE_DIR.mkdir(parents=True, exist_ok=True)
Config.TMP_DIR.mkdir(parents=True, exist_ok=True)


def fix_permissions(path: Path, is_dir: bool = False) -> None:
    try:
        import grp
        import pwd
        uid = pwd.getpwnam("webmaster").pw_uid
        gid = grp.getgrnam("cam-app").gr_gid
        os.chown(path, uid, gid)
    except (ImportError, KeyError, PermissionError, OSError):
        pass
    try:
        mode = 0o2770 if (is_dir or path.is_dir()) else 0o660
        path.chmod(mode)
    except (PermissionError, OSError):
        pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [Thread:%(threadName)s] %(message)s",
    handlers=[
        RotatingFileHandler(
            Config.LOG_FILE,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("alert_motion")


class PriorityEngineGate:
    def __init__(self, concurrency: int) -> None:
        self._concurrency = max(1, concurrency)
        self._active = 0
        self._cond = threading.Condition()
        self._waiters: list[tuple[tuple[int, int], int, threading.Event, str]] = []
        self._seq = 0

    def _schedule_locked(self) -> None:
        while self._waiters and self._active < self._concurrency:
            _, _, event, _ = heapq.heappop(self._waiters)
            self._active += 1
            event.set()

    def acquire(self, priority: tuple[int, int], label: str) -> None:
        event = threading.Event()
        with self._cond:
            self._seq += 1
            heapq.heappush(self._waiters, (priority, self._seq, event, label))
            self._schedule_locked()
        event.wait()

    def release(self) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            self._schedule_locked()


ENGINE_GATE = PriorityEngineGate(Config.ENGINE_CONCURRENCY)


@contextmanager
def engine_slot(priority: tuple[int, int], label: str):
    start = time.monotonic()
    ENGINE_GATE.acquire(priority, label)
    waited = time.monotonic() - start
    if waited >= 1.0:
        logger.debug("motor fila=%.1fs prioridade=%s label=%s", waited, priority, label)
    try:
        yield
    finally:
        ENGINE_GATE.release()


@dataclass(slots=True)
class CandidateImage:
    path: Path
    source: str
    origin_path: Optional[Path] = None
    frame_sec: Optional[float] = None
    md5: Optional[str] = None
    sharpness: Optional[float] = None
    engine_score: Optional[float] = None
    category: Optional[str] = None
    object_count: int = 0
    person_count: int = 0
    animal_count: int = 0
    detected: tuple[str, ...] = ()
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    file_ts: int = 0
    rank_group: int = 0
    upper_person_count: int = 0
    lower_person_count: int = 0

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def rank_key(self) -> tuple[int, int, float, float, int, int, float]:
        if self.person_count > 0:
            group = 2
            count = self.person_count
        elif self.animal_count > 0:
            group = 1
            count = self.animal_count
        else:
            group = 0
            count = 0
        timestamp = self.frame_sec if self.frame_sec is not None else float(self.file_ts)
        score = float(self.engine_score) if self.engine_score is not None else -1.0
        return (
            group,
            count,
            -timestamp,
            score,
            self.area,
            self.size_bytes,
            float(self.sharpness) if self.sharpness is not None else -1.0,
        )


@dataclass(slots=True)
class MotionFrameSet:
    video_path: Path
    frames: tuple[Path, ...]


@dataclass(slots=True)
class ChampionInfo:
    path: Optional[Path] = None
    origin_path: Optional[Path] = None
    source: Optional[str] = None
    frame_sec: Optional[float] = None
    md5: Optional[str] = None
    category: Optional[str] = None
    object_count: int = 0
    person_count: int = 0
    animal_count: int = 0
    sharpness: Optional[float] = None
    engine_score: Optional[float] = None
    detected: tuple[str, ...] = ()
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    file_ts: int = 0
    rank_group: int = 0

    @property
    def rank_key(self) -> tuple[int, int, float, float, int, int, float]:
        temp = CandidateImage(
            path=self.path or Path(""),
            source=self.source or "unknown",
            origin_path=self.origin_path,
            frame_sec=self.frame_sec,
            md5=self.md5,
            sharpness=self.sharpness,
            engine_score=self.engine_score,
            category=self.category,
            object_count=self.object_count,
            person_count=self.person_count,
            animal_count=self.animal_count,
            detected=self.detected,
            width=self.width,
            height=self.height,
            size_bytes=self.size_bytes,
            file_ts=self.file_ts,
            rank_group=self.rank_group,
        )
        return temp.rank_key


@dataclass(slots=True)
class WindowMeta:
    window_id: int
    window_start: int
    window_end: int
    seq: int
    event_code: str
    state: str
    file_count: int
    video_count: int
    photo_count: int
    first_seen_at: Optional[int]
    last_file_at: Optional[int]
    enhanced_started_at: Optional[int] = None
    motion_frames: int = 0
    late_motion_sent: int = 0


@dataclass(slots=True)
class CycleStats:
    windows: int = 0
    immediate_pending: int = 0
    immediate_sent: int = 0
    immediate_failed: int = 0
    enhanced_pending: int = 0
    enhanced_sent: int = 0
    enhanced_skipped: int = 0
    enhanced_failed: int = 0
    late_pending: int = 0
    late_sent: int = 0
    late_skipped: int = 0
    late_failed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            current = getattr(self, name)
            setattr(self, name, current + amount)

    def set(self, name: str, value: int) -> None:
        with self._lock:
            setattr(self, name, value)


PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}
VIDEO_SUFFIXES = {".h264", ".mp4", ".avi", ".mov"}
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DATE_RE = re.compile(r"(?:^|/)(\d{4}-\d{2}-\d{2})(?:/|$)")
MOTION_FILE_RE = re.compile(
    r"(?P<start>\d{2}.\d{2}.\d{2})-(?P<end>\d{2}.\d{2}.\d{2})"
    r"\[M\]\[(?P<id>[^\]]+)\](?:\((?P<channel>[^)]+)\))?",
    re.IGNORECASE,
)
PERSON_TERMS = {
    "person", "people", "human", "humans",
    "man", "woman", "child", "person_0",
}
ANIMAL_TERMS = {
    "animal", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "chicken", "duck",
    "rabbit", "goat", "pig",
}


def is_photo(path: Path) -> bool:
    return path.suffix.lower() in PHOTO_SUFFIXES


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def is_valid_media_file(path: Path) -> bool:
    if is_valid_image_file(path):
        return True
    stat = safe_stat(path)
    return bool(
        stat
        and stat.st_size >= Config.MIN_VIDEO_BYTES
        and path.suffix.lower() in VIDEO_SUFFIXES
    )


def safe_rel_path(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(Config.BASE_DIR))
    except (ValueError, OSError):
        return str(path)


def path_from_db(value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = Config.BASE_DIR / path
    return path


def safe_stat(path: Path) -> Optional[os.stat_result]:
    try:
        return path.stat()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def safe_stat_size(path: Path) -> int:
    stat = safe_stat(path)
    return stat.st_size if stat else -1


def is_valid_image_file(path: Path) -> bool:
    stat = safe_stat(path)
    if stat is None or stat.st_size < Config.MIN_IMAGE_BYTES:
        return False
    try:
        with path.open("rb") as handle:
            header = handle.read(8)
    except (OSError, PermissionError):
        return False
    return header.startswith(JPEG_MAGIC) or header.startswith(PNG_MAGIC)


def is_valid_video_file(path: Path) -> bool:
    stat = safe_stat(path)
    return bool(stat and stat.st_size >= Config.MIN_VIDEO_BYTES)


def compute_md5(path: Path) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        digest = hashlib.md5()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, PermissionError):
        return None


def image_dimensions(path: Path) -> tuple[int, int]:
    if cv2 is None:
        return (0, 0)
    try:
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            return (0, 0)
        height, width = image.shape[:2]
        return int(width), int(height)
    except Exception:
        return (0, 0)


def active_camera_image(image: Any, camera_half: str = "lower") -> Any:
    if (
        not Config.ACTIVE_CAMERA_ONLY
        or image is None
        or camera_half == "full"
    ):
        return image
    try:
        height = image.shape[0]
    except (AttributeError, ValueError):
        return image
    if height < 2:
        return image
    midpoint = height // 2
    if camera_half == "upper":
        return image[:midpoint, :]
    return image[midpoint:, :]


def compute_sharpness(path: Path) -> Optional[float]:
    if cv2 is None:
        return None
    try:
        image = active_camera_image(
            cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        )
        if image is None:
            return None
        pixels = int(image.shape[0]) * int(image.shape[1])
        if pixels <= 0:
            return None
        if pixels > Config.SHARPNESS_MAX_PIXELS:
            scale = math.sqrt(Config.SHARPNESS_MAX_PIXELS / float(pixels))
            width = max(1, int(image.shape[1] * scale))
            height = max(1, int(image.shape[0] * scale))
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        value = float(cv2.Laplacian(image, cv2.CV_64F).var())
        if math.isnan(value) or math.isinf(value):
            return None
        return max(0.0, value)
    except Exception:
        return None


def recording_key_from_name(path: Path) -> Optional[str]:
    match = MOTION_FILE_RE.search(path.name)
    if not match:
        return None
    rec_id = str(match.group("id") or "").strip()
    channel = str(match.group("channel") or "").strip()
    if not rec_id:
        return None
    if channel:
        return f"{rec_id}[{channel}]"
    return rec_id


@dataclass(slots=True)
class MotionSpan:
    start: datetime
    end: datetime
    rec_id: Optional[str] = None
    channel: Optional[str] = None
    rec_key: Optional[str] = None


def extract_motion_span(path: Path) -> Optional[MotionSpan]:
    date_match = DATE_RE.search(str(path))
    if not date_match:
        return None
    motion_match = MOTION_FILE_RE.search(path.name)
    if not motion_match:
        return None
    date_str = date_match.group(1)
    start_str = motion_match.group("start").replace(".", ":")
    end_str = motion_match.group("end").replace(".", ":")
    try:
        start = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(f"{date_str} {end_str}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if end < start:
        end += timedelta(days=1)
    rec_key = recording_key_from_name(path)
    return MotionSpan(
        start=start,
        end=end,
        rec_id=str(motion_match.group("id") or "").strip() or None,
        channel=str(motion_match.group("channel") or "").strip() or None,
        rec_key=rec_key,
    )


def scan_files() -> list[tuple[Path, MotionSpan]]:
    results: list[tuple[Path, MotionSpan]] = []
    for root, dirs, files in os.walk(Config.BASE_DIR):
        dirs[:] = [item for item in dirs if not item.startswith(".")]
        for filename in files:
            if "[M]" not in filename:
                continue
            path = Path(root) / filename
            if path.is_symlink() or not (is_photo(path) or is_video(path)):
                continue
            stat = safe_stat(path)
            if stat is None or stat.st_size <= 0:
                continue
            span = extract_motion_span(path)
            if span is not None:
                results.append((path, span))
    results.sort(key=lambda item: (item[1].start, str(item[0])))
    return results


def normalize_label(value: object) -> str:
    return str(value).strip().lower().replace("-", "").replace(" ", "")


def label_is_person(label: str) -> bool:
    return any(term == label or term in label.split("_") for term in PERSON_TERMS)


def label_is_animal(label: str) -> bool:
    return any(term == label or term in label.split("_") for term in ANIMAL_TERMS)


def parse_detected(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(normalize_label(item) for item in value if str(item).strip())
    if isinstance(value, tuple):
        return tuple(normalize_label(item) for item in value if str(item).strip())
    if isinstance(value, str):
        raw = [item.strip() for item in value.split(",") if item.strip()]
        return tuple(normalize_label(item) for item in raw)
    return ()


def clean_score(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        score = float(value)
        if math.isnan(score) or math.isinf(score):
            return None
        return score
    except (TypeError, ValueError):
        return None


def clean_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def infer_counts(
    category: Optional[str],
    object_count: int,
    detected: Sequence[str],
    person_count_value: object = None,
    animal_count_value: object = None,
) -> tuple[int, int]:
    explicit_persons = clean_nonnegative_int(person_count_value)
    explicit_animals = clean_nonnegative_int(animal_count_value)
    detected_people = sum(1 for label in detected if label_is_person(label))
    detected_animals = sum(1 for label in detected if label_is_animal(label))
    person_count = max(explicit_persons, detected_people)
    animal_count = max(explicit_animals, detected_animals)
    normalized_category = normalize_label(category) if category else ""
    if "multiple_people" in normalized_category:
        person_count = max(person_count, 2)
    elif normalized_category in {"person", "people"} and person_count == 0:
        person_count = max(1, object_count)
    elif normalized_category == "animal" and animal_count == 0:
        animal_count = object_count
    return person_count, animal_count


def classify_rank_group(person_count: int, animal_count: int) -> int:
    if person_count > 0:
        return 2
    if animal_count > 0:
        return 1
    return 0


def apply_candidate_metrics(candidate: CandidateImage) -> CandidateImage:
    stat = safe_stat(candidate.path)
    if stat:
        candidate.size_bytes = int(stat.st_size)
        try:
            candidate.file_ts = int(stat.st_mtime)
        except OSError:
            candidate.file_ts = 0
    if candidate.md5 is None:
        candidate.md5 = compute_md5(candidate.path)
    if candidate.width <= 0 or candidate.height <= 0:
        candidate.width, candidate.height = image_dimensions(candidate.path)
    candidate.rank_group = classify_rank_group(
        candidate.person_count, candidate.animal_count
    )
    return candidate


def parse_engine_candidate(
    base: Optional[CandidateImage], data: dict[str, Any]
) -> CandidateImage:
    selected_path = Path(
        str(data.get("path", base.path if base else ""))
    ).expanduser()
    if not selected_path.is_absolute():
        selected_path = (Path.cwd() / selected_path).resolve()
    detected = parse_detected(data.get("detected"))
    category_raw = data.get("category")
    category = str(category_raw).strip() if category_raw is not None else None
    if category == "":
        category = None
    object_count = clean_nonnegative_int(data.get("object_count"))
    person_count, animal_count = infer_counts(
        category,
        object_count,
        detected,
        data.get("person_count", data.get("people_count")),
        data.get("animal_count", data.get("animals_count")),
    )
    candidate = CandidateImage(
        path=base.path if base else selected_path,
        source=base.source if base else (
            "photo" if is_photo(selected_path) else "frame"
        ),
        origin_path=base.origin_path if base else (
            selected_path if is_photo(selected_path) else None
        ),
        frame_sec=base.frame_sec if base else None,
        md5=base.md5 if base else None,
        sharpness=clean_score(data.get("sharpness", data.get("score"))),
        engine_score=clean_score(data.get("score")),
        category=category,
        object_count=object_count,
        person_count=person_count,
        animal_count=animal_count,
        detected=detected,
        width=clean_nonnegative_int(data.get("width")),
        height=clean_nonnegative_int(data.get("height")),
        size_bytes=clean_nonnegative_int(data.get("size_bytes")),
        file_ts=clean_nonnegative_int(data.get("file_ts")),
        upper_person_count=clean_nonnegative_int(
            data.get("upper_person_count")
        ),
        lower_person_count=clean_nonnegative_int(
            data.get("lower_person_count")
        ),
    )
    if base:
        candidate.path = base.path
        candidate.source = base.source
        candidate.origin_path = base.origin_path
        candidate.frame_sec = base.frame_sec
        candidate.md5 = base.md5
    return apply_candidate_metrics(candidate)


def compare_candidates(
    candidate: Optional[CandidateImage],
    champion: Optional[ChampionInfo],
) -> tuple[bool, str]:
    if candidate is None or not is_valid_image_file(candidate.path):
        return False, "candidato_invalido"
    if champion is None:
        return True, "sem_campeao"
    if candidate.md5 and champion.md5 and candidate.md5 == champion.md5:
        return False, "mesmo_arquivo"
    if candidate.rank_key <= champion.rank_key:
        return False, "ranking_nao_melhor"
    if candidate.person_count > champion.person_count:
        return True, "mais_pessoas"
    if candidate.person_count > 0 and candidate.person_count == champion.person_count:
        return True, "mesmas_pessoas_mais_nitidez"
    if candidate.animal_count > champion.animal_count:
        return True, "mais_animais"
    if candidate.animal_count > 0 and candidate.animal_count == champion.animal_count:
        return True, "mesmos_animais_mais_nitidez"
    return True, "ranking_melhor"


def summarize_candidate(candidate: CandidateImage) -> str:
    nitidez = (
        f"{candidate.sharpness:.2f}"
        if candidate.sharpness is not None
        else "N/D"
    )
    return (
        f"arquivo={candidate.path.name} "
        f"pessoas={candidate.person_count} "
        f"animais={candidate.animal_count} "
        f"nitidez={nitidez}"
    )


def summarize_champion(champion: Optional[ChampionInfo]) -> str:
    if champion is None:
        return "campeao=N/D"
    sharpness = (
        f"{champion.sharpness:.2f}"
        if champion.sharpness is not None
        else "N/D"
    )
    return (
        f"campeao={champion.path.name if champion.path else 'N/D'} "
        f"pessoas={champion.person_count} "
        f"animais={champion.animal_count} "
        f"nitidez={sharpness}"
    )


def _decode_champion_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


class FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Optional[IO[str]] = None

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+")
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self.handle.close()
        except OSError:
            pass
        self.handle = None

    def __enter__(self) -> "FileLock":
        if not self.acquire():
            raise RuntimeError("Lock não adquirido")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


class Database:
    _lock = threading.RLock()

    def __init__(self) -> None:
        Config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            Config.DB_PATH, timeout=15, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self._configure()
        self._init_schema()
        self._backfill_source_ids()

    def _configure(self) -> None:
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=15000")
            self.conn.execute("PRAGMA foreign_keys=ON")

    def _table_exists(self, table: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def _column_exists(self, table: str, column: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == column for row in rows)

    def _init_schema(self) -> None:
        with self._lock:
            cursor = self.conn.cursor()
            if self._table_exists("windows") and not self._column_exists("windows", "window_id"):
                legacy = "windows_legacy"
                if self._table_exists(legacy):
                    cursor.execute(f"DROP TABLE {legacy}")
                cursor.execute(f"ALTER TABLE windows RENAME TO {legacy}")
            if self._table_exists("files") and not self._column_exists("files", "window_id"):
                legacy = "files_legacy"
                if self._table_exists(legacy):
                    cursor.execute(f"DROP TABLE {legacy}")
                cursor.execute(f"ALTER TABLE files RENAME TO {legacy}")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    file_path TEXT PRIMARY KEY,
                    window_id INTEGER NOT NULL,
                    file_ts INTEGER NOT NULL,
                    file_end_ts INTEGER NOT NULL DEFAULT 0,
                    is_video INTEGER NOT NULL DEFAULT 0,
                    discovered_at INTEGER NOT NULL DEFAULT 0,
                    evaluated INTEGER NOT NULL DEFAULT 1,
                    assignment TEXT NOT NULL DEFAULT 'normal',
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source_start INTEGER NOT NULL DEFAULT 0,
                    source_id TEXT NOT NULL DEFAULT '',
                    rebuild_motion INTEGER NOT NULL DEFAULT 0
                )
            """)
            if self._table_exists("files") and not self._column_exists("files", "file_end_ts"):
                cursor.execute("ALTER TABLE files ADD COLUMN file_end_ts INTEGER NOT NULL DEFAULT 0")
            if self._table_exists("files") and not self._column_exists("files", "source_start"):
                cursor.execute("ALTER TABLE files ADD COLUMN source_start INTEGER NOT NULL DEFAULT 0")
            if self._table_exists("files") and not self._column_exists("files", "source_id"):
                cursor.execute("ALTER TABLE files ADD COLUMN source_id TEXT NOT NULL DEFAULT ''")
            if self._table_exists("files") and not self._column_exists("files", "rebuild_motion"):
                cursor.execute("ALTER TABLE files ADD COLUMN rebuild_motion INTEGER NOT NULL DEFAULT 0")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS windows (
                    window_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    window_start INTEGER NOT NULL,
                    window_end INTEGER NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 1,
                    event_code TEXT,
                    state TEXT NOT NULL DEFAULT 'open',
                    close_reason TEXT,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    video_count INTEGER NOT NULL DEFAULT 0,
                    photo_count INTEGER NOT NULL DEFAULT 0,
                    first_seen_at INTEGER,
                    last_file_at INTEGER,
                    closed_at INTEGER,
                    immediate_done INTEGER NOT NULL DEFAULT 0,
                    immediate_sent INTEGER NOT NULL DEFAULT 0,
                    immediate_event_code TEXT,
                    immediate_image_path TEXT,
                    enhanced_done INTEGER NOT NULL DEFAULT 0,
                    enhanced_sent INTEGER NOT NULL DEFAULT 0,
                    enhanced_started_at INTEGER,
                    motion_frames INTEGER NOT NULL DEFAULT 0,
                    late_motion_sent INTEGER NOT NULL DEFAULT 0,
                    event_hash TEXT,
                    best_image_path TEXT,
                    champion_score REAL,
                    champion_md5 TEXT,
                    champion_origin_path TEXT,
                    champion_frame_sec REAL,
                    champion_source TEXT,
                    champion_category TEXT,
                    champion_object_count INTEGER,
                    champion_level INTEGER,
                    champion_path TEXT,
                    champion_kind TEXT,
                    champion_detected TEXT,
                    champion_updated_at INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    enhanced_attempts INTEGER NOT NULL DEFAULT 0,
                    late_attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            if self._table_exists("windows") and not self._column_exists("windows", "enhanced_started_at"):
                cursor.execute("ALTER TABLE windows ADD COLUMN enhanced_started_at INTEGER")
            if self._table_exists("windows") and not self._column_exists("windows", "motion_frames"):
                cursor.execute("ALTER TABLE windows ADD COLUMN motion_frames INTEGER NOT NULL DEFAULT 0")
            if self._table_exists("windows") and not self._column_exists("windows", "late_motion_sent"):
                cursor.execute("ALTER TABLE windows ADD COLUMN late_motion_sent INTEGER NOT NULL DEFAULT 0")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_window ON files(window_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_assignment_eval ON files(assignment, evaluated)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_ts ON files(file_ts)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_source ON files(source_start, source_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_windows_state ON windows(state)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_windows_immediate ON windows(immediate_done)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_windows_enhanced ON windows(enhanced_done)")
            self.conn.commit()
            fix_permissions(Config.DB_PATH)

    def _backfill_source_ids(self) -> None:
        with self._lock:
            rows = self.conn.execute(
                "SELECT file_path, file_ts FROM files "
                "WHERE COALESCE(source_id, '') = '' OR source_start = 0"
            ).fetchall()
            for row in rows:
                rec_key = recording_key_from_name(Path(row["file_path"])) or ""
                self.conn.execute(
                    "UPDATE files SET source_start = ?, source_id = ? WHERE file_path = ?",
                    (int(row["file_ts"]), rec_key, str(row["file_path"])),
                )
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass

    def count_windows(self) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS count FROM windows"
            ).fetchone()
        return int(row["count"])

    def known_file_paths(self) -> set[str]:
        with self._lock:
            rows = self.conn.execute("SELECT file_path FROM files").fetchall()
        return {str(row[0]) for row in rows}

    def allocate_file(
        self,
        path: Path,
        file_ts: int,
        file_end_ts: int,
        video: bool,
        discovered_at: int,
        source_id: Optional[str] = None,
    ) -> None:
        rel = safe_rel_path(path) or str(path)
        source_key = (source_id or "").strip()
        video_int = 1 if video else 0
        with self._lock:
            cursor = self.conn.cursor()
            if cursor.execute(
                "SELECT 1 FROM files WHERE file_path=?", (rel,)
            ).fetchone():
                return
            if source_key:
                duplicate_rows = cursor.execute(
                    "SELECT f.file_path, f.window_id, f.file_end_ts, "
                    "f.discovered_at, w.state, w.enhanced_done, "
                    "w.enhanced_started_at "
                    "FROM files f LEFT JOIN windows w ON w.window_id = f.window_id "
                    "WHERE f.source_start = ? AND f.source_id = ? "
                    "AND f.is_video = ? AND f.file_path != ? "
                    "AND f.assignment NOT IN ('ignored', 'superseded')",
                    (file_ts, source_key, video_int, rel),
                ).fetchall()
            else:
                duplicate_rows = []
            if duplicate_rows:
                new_size = safe_stat_size(path)

                def duplicate_score(row: sqlite3.Row) -> tuple[int, int, int]:
                    old_path = path_from_db(str(row["file_path"]))
                    old_size = safe_stat_size(old_path) if old_path else -1
                    return (
                        old_size,
                        int(row["file_end_ts"]),
                        int(row["discovered_at"]),
                    )

                best_duplicate = max(duplicate_rows, key=duplicate_score)
                best_score = duplicate_score(best_duplicate)
                new_score = (new_size, file_end_ts, discovered_at)
                if new_score <= best_score:
                    self._insert_file(
                        cursor, rel, 0, file_ts, file_end_ts, video,
                        discovered_at, "ignored", 1, file_ts, source_key, 0,
                    )
                    self.conn.commit()
                    return
                cursor.executemany(
                    "UPDATE files SET assignment = 'superseded', evaluated = 1 "
                    "WHERE file_path = ?",
                    [(str(row["file_path"]),) for row in duplicate_rows],
                )
                old_window = int(best_duplicate["window_id"])
                if old_window > 0:
                    enhanced_done = int(best_duplicate["enhanced_done"] or 0)
                    enhanced_started = (
                        int(best_duplicate["enhanced_started_at"] or 0) > 0
                    )
                    if enhanced_done == 1 or enhanced_started:
                        assignment, evaluated = "late", 0
                        rebuild = video_int
                    else:
                        assignment, evaluated = "normal", 1
                        rebuild = 0
                    self._insert_file(
                        cursor, rel, old_window, file_ts, file_end_ts, video,
                        discovered_at, assignment, evaluated,
                        file_ts, source_key, rebuild,
                    )
                    cursor.execute(
                        "UPDATE windows SET window_end = MAX(window_end, ?), "
                        "updated_at = CURRENT_TIMESTAMP WHERE window_id = ?",
                        (file_end_ts, old_window),
                    )
                    if assignment == "normal":
                        cursor.execute(
                            "UPDATE windows SET last_file_at = "
                            "MAX(COALESCE(last_file_at, 0), ?), "
                            "updated_at = CURRENT_TIMESTAMP WHERE window_id = ?",
                            (discovered_at, old_window),
                        )
                    else:
                        cursor.execute(
                            "UPDATE windows SET late_attempts = 0, "
                            "late_motion_sent = ?, "
                            "updated_at = CURRENT_TIMESTAMP WHERE window_id = ?",
                            (0 if rebuild else 0, old_window),
                        )
                self.conn.commit()
                return
            open_windows = cursor.execute(
                "SELECT window_id, window_start, window_end FROM windows "
                "WHERE state='open' ORDER BY window_end DESC, window_id DESC"
            ).fetchall()
            target_id: Optional[int] = None
            stale_ids: list[int] = []
            for row in open_windows:
                wid = int(row["window_id"])
                w_start = int(row["window_start"])
                w_end = int(row["window_end"])
                overlaps = file_ts <= w_end and file_end_ts >= w_start
                continues = (
                    w_end < file_ts <=
                    w_end + Config.WINDOW_TOLERANCE_SECONDS
                )
                if target_id is None and (overlaps or continues):
                    target_id = wid
                elif file_ts > w_end:
                    stale_ids.append(wid)
            if stale_ids:
                self.conn.executemany(
                    "UPDATE windows SET state='closed', "
                    "close_reason='motion_gap', closed_at=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                    [(discovered_at, wid) for wid in stale_ids],
                )
            if target_id is not None:
                self._insert_file(
                    cursor, rel, target_id, file_ts, file_end_ts, video,
                    discovered_at, "normal", 1, file_ts, source_key, 0,
                )
                cursor.execute(
                    "UPDATE windows SET file_count = file_count + 1, "
                    "video_count = video_count + ?, photo_count = photo_count + ?, "
                    "window_start = MIN(window_start, ?), "
                    "window_end = MAX(window_end, ?), "
                    "last_file_at = MAX(COALESCE(last_file_at, 0), ?), "
                    "updated_at = CURRENT_TIMESTAMP WHERE window_id = ?",
                    (
                        1 if video else 0, 0 if video else 1,
                        file_ts, file_end_ts, discovered_at, target_id,
                    ),
                )
                self.conn.commit()
                return
            closed_target = cursor.execute(
                "SELECT window_id, enhanced_done, enhanced_started_at "
                "FROM windows "
                "WHERE state='closed' AND "
                "((? <= window_end AND ? >= window_start) OR "
                "(? > window_end AND ? <= window_end + ?)) "
                "ORDER BY window_start DESC, window_id DESC LIMIT 1",
                (
                    file_ts, file_end_ts, file_ts, file_ts,
                    Config.WINDOW_TOLERANCE_SECONDS,
                ),
            ).fetchone()
            if closed_target is not None:
                wid = int(closed_target["window_id"])
                enhanced_done = int(closed_target["enhanced_done"] or 0)
                enhanced_started = (
                    int(closed_target["enhanced_started_at"] or 0) > 0
                )
                if enhanced_done == 1 or enhanced_started:
                    assignment, evaluated = "late", 0
                    rebuild = video_int
                else:
                    assignment, evaluated = "normal", 1
                    rebuild = 0
                self._insert_file(
                    cursor, rel, wid, file_ts, file_end_ts, video,
                    discovered_at, assignment, evaluated,
                    file_ts, source_key, rebuild,
                )
                if assignment == "normal":
                    cursor.execute(
                        "UPDATE windows SET file_count = file_count + 1, "
                        "video_count = video_count + ?, "
                        "photo_count = photo_count + ?, "
                        "window_start = MIN(window_start, ?), "
                        "window_end = MAX(window_end, ?), "
                        "last_file_at = MAX(COALESCE(last_file_at, 0), ?), "
                        "updated_at = CURRENT_TIMESTAMP WHERE window_id = ?",
                        (
                            1 if video else 0, 0 if video else 1,
                            file_ts, file_end_ts, discovered_at, wid,
                        ),
                    )
                else:
                    cursor.execute(
                        "UPDATE windows SET window_end = MAX(window_end, ?), "
                        "late_attempts = 0, late_motion_sent = ?, "
                        "updated_at = CURRENT_TIMESTAMP "
                        "WHERE window_id = ?",
                        (file_end_ts, 0, wid),
                    )
                self.conn.commit()
                return
            seq_row = cursor.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS seq "
                "FROM windows WHERE window_start=?",
                (file_ts,),
            ).fetchone()
            seq = int(seq_row["seq"])
            event_code = f"{file_ts}-{seq}"
            cursor.execute(
                "INSERT INTO windows(window_start, window_end, seq, "
                "event_code, state, file_count, video_count, photo_count, "
                "first_seen_at, last_file_at) "
                "VALUES(?,?,?,?,'open',1,?,?,?,?)",
                (
                    file_ts, file_end_ts, seq, event_code,
                    1 if video else 0, 0 if video else 1,
                    discovered_at, discovered_at,
                ),
            )
            new_window_id = int(cursor.lastrowid)
            self._insert_file(
                cursor, rel, new_window_id, file_ts, file_end_ts, video,
                discovered_at, "normal", 1, file_ts, source_key, 0,
            )
            self.conn.commit()

    @staticmethod
    def _insert_file(
        cursor: sqlite3.Cursor,
        rel: str,
        window_id: int,
        file_ts: int,
        file_end_ts: int,
        video: bool,
        discovered_at: int,
        assignment: str,
        evaluated: int,
        source_start: int = 0,
        source_id: str = "",
        rebuild_motion: int = 0,
    ) -> None:
        cursor.execute(
            "INSERT OR IGNORE INTO files(file_path, window_id, file_ts, "
            "file_end_ts, is_video, discovered_at, evaluated, assignment, "
            "source_start, source_id, rebuild_motion) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                rel, window_id, file_ts, file_end_ts,
                1 if video else 0, discovered_at, evaluated,
                assignment, source_start, source_id, rebuild_motion,
            ),
        )

    def close_ready_windows(self) -> None:
        now = int(time.time())
        with self._lock:
            rows = self.conn.execute(
                "SELECT window_id, last_file_at FROM windows WHERE state='open'"
            ).fetchall()
            for row in rows:
                if now - int(row["last_file_at"] or now) >= Config.WINDOW_IDLE_CLOSE:
                    self.conn.execute(
                        "UPDATE windows SET state='closed', close_reason='idle', "
                        "closed_at=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE window_id=?",
                        (now, int(row["window_id"])),
                    )
            self.conn.commit()

    def get_window_meta(self, window_id: int) -> Optional[WindowMeta]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM windows WHERE window_id=?", (window_id,)
            ).fetchone()
        if row is None:
            return None
        return WindowMeta(
            window_id=int(row["window_id"]),
            window_start=int(row["window_start"]),
            window_end=int(row["window_end"]),
            seq=int(row["seq"]),
            event_code=str(
                row["event_code"] or f"{row['window_start']}-{row['seq']}"
            ),
            state=str(row["state"]),
            file_count=int(row["file_count"]),
            video_count=int(row["video_count"]),
            photo_count=int(row["photo_count"]),
            first_seen_at=(
                int(row["first_seen_at"])
                if row["first_seen_at"] is not None
                else None
            ),
            last_file_at=(
                int(row["last_file_at"])
                if row["last_file_at"] is not None
                else None
            ),
            enhanced_started_at=(
                int(row["enhanced_started_at"])
                if row["enhanced_started_at"] is not None
                else None
            ),
            motion_frames=int(row["motion_frames"] or 0),
            late_motion_sent=int(row["late_motion_sent"] or 0),
        )

    def pending_immediate(self) -> list[int]:
        threshold = int(time.time()) - Config.IMMEDIATE_DEBOUNCE
        with self._lock:
            rows = self.conn.execute(
                "SELECT window_id FROM windows "
                "WHERE immediate_done=0 AND file_count>0 "
                "AND COALESCE(first_seen_at,0)<=? "
                "ORDER BY window_start, window_id",
                (threshold,),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def pending_enhanced(self) -> list[int]:
        threshold = int(time.time()) - Config.ENHANCED_START_DELAY
        with self._lock:
            rows = self.conn.execute(
                "SELECT window_id FROM windows "
                "WHERE state='closed' AND immediate_done=1 "
                "AND enhanced_done=0 AND enhanced_attempts<? "
                "AND COALESCE(closed_at,0)<=? "
                "ORDER BY window_start, window_id",
                (Config.MAX_ALERT_RETRIES, threshold),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def pending_late(self) -> list[int]:
        threshold = int(time.time()) - Config.LATE_COMPARE_DELAY
        with self._lock:
            rows = self.conn.execute(
                "SELECT f.window_id FROM files f "
                "JOIN windows w ON w.window_id=f.window_id "
                "WHERE f.assignment='late' AND f.evaluated=0 "
                "AND f.discovered_at<=? AND w.enhanced_done=1 "
                "AND w.late_attempts<? AND w.window_id != 0 "
                "GROUP BY f.window_id ORDER BY MIN(f.discovered_at)",
                (threshold, Config.MAX_ALERT_RETRIES),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def _paths_for_window(
        self,
        window_id: int,
        assignment: str,
        evaluated: Optional[int] = None,
    ) -> list[Path]:
        query = "SELECT file_path FROM files WHERE window_id=? AND assignment=?"
        args: list[object] = [window_id, assignment]
        if evaluated is not None:
            query += " AND evaluated=?"
            args.append(evaluated)
        query += " ORDER BY file_ts, file_path"
        with self._lock:
            rows = self.conn.execute(query, tuple(args)).fetchall()
        result: list[Path] = []
        for row in rows:
            path = path_from_db(str(row[0]))
            if path is not None and path.exists():
                result.append(path)
        return result

    def get_normal_files(self, window_id: int) -> list[Path]:
        return self._paths_for_window(window_id, "normal")

    def get_late_files(self, window_id: int) -> list[Path]:
        return self._paths_for_window(window_id, "late", evaluated=0)

    def get_late_rebuild_videos(self, window_id: int) -> list[Path]:
        query = (
            "SELECT file_path FROM files "
            "WHERE window_id=? AND assignment='late' AND evaluated=0 "
            "AND is_video=1 AND rebuild_motion=1 "
            "ORDER BY file_ts, file_path"
        )
        with self._lock:
            rows = self.conn.execute(query, (window_id,)).fetchall()
        result: list[Path] = []
        for row in rows:
            path = path_from_db(str(row[0]))
            if path is not None and path.exists():
                result.append(path)
        return result

    def discovery_times(
        self, window_id: int
    ) -> tuple[Optional[int], Optional[int]]:
        with self._lock:
            row = self.conn.execute(
                "SELECT MIN(discovered_at), MAX(discovered_at) "
                "FROM files WHERE window_id=? AND assignment='normal'",
                (window_id,),
            ).fetchone()
        if not row:
            return None, None
        first = int(row[0]) if row[0] is not None else None
        last = int(row[1]) if row[1] is not None else None
        return first, last

    def increment_attempt(self, window_id: int, column: str) -> int:
        allowed = {"attempts", "enhanced_attempts", "late_attempts"}
        if column not in allowed:
            raise ValueError(f"Coluna de tentativa inválida: {column}")
        with self._lock:
            self.conn.execute(
                f"UPDATE windows SET {column}={column}+1, "
                f"updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (window_id,),
            )
            row = self.conn.execute(
                f"SELECT {column} FROM windows WHERE window_id=?",
                (window_id,),
            ).fetchone()
            self.conn.commit()
        return int(row[0]) if row else 0

    def mark_immediate_done(
        self,
        window_id: int,
        image_path: Optional[Path],
        sent: bool,
        event_code: Optional[str],
        error: Optional[str],
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET immediate_done=1, immediate_sent=?, "
                "immediate_image_path=?, "
                "immediate_event_code=COALESCE(?, immediate_event_code), "
                "last_error=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE window_id=?",
                (
                    1 if sent else 0,
                    safe_rel_path(image_path) if image_path else "",
                    event_code, error, window_id,
                ),
            )
            self.conn.commit()

    def mark_enhanced_started(self, window_id: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET enhanced_started_at=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (int(time.time()), window_id),
            )
            self.conn.commit()

    def set_motion_frames(self, window_id: int, frames: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET motion_frames=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (max(0, int(frames)), window_id),
            )
            self.conn.commit()

    def mark_late_motion_sent(self, window_id: int, frames: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET late_motion_sent=1, "
                "motion_frames=MAX(motion_frames, ?), "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (max(0, int(frames)), window_id),
            )
            self.conn.commit()

    def mark_enhanced_done(
        self,
        window_id: int,
        best_path: Optional[Path],
        event_code: str,
        sent: bool,
        error: Optional[str],
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET enhanced_done=1, enhanced_sent=?, "
                "best_image_path=?, event_hash=?, last_error=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (
                    1 if sent else 0,
                    safe_rel_path(best_path) if best_path else "",
                    event_code, error, window_id,
                ),
            )
            self.conn.commit()

    def mark_late_files_evaluated(
        self, window_id: int, paths: Iterable[Path]
    ) -> None:
        rels = [safe_rel_path(path) for path in paths]
        rels = [rel for rel in rels if rel]
        if not rels:
            return
        with self._lock:
            self.conn.executemany(
                "UPDATE files SET evaluated=1 "
                "WHERE window_id=? AND assignment='late' AND file_path=?",
                [(window_id, rel) for rel in rels],
            )
            self.conn.commit()

    def get_champion(self, window_id: int) -> Optional[ChampionInfo]:
        with self._lock:
            row = self.conn.execute(
                "SELECT champion_score, champion_md5, champion_origin_path, "
                "champion_frame_sec, champion_source, champion_category, "
                "champion_object_count, champion_level, champion_path, "
                "champion_kind, champion_detected "
                "FROM windows WHERE window_id=?",
                (window_id,),
            ).fetchone()
        if row is None or all(value is None for value in row):
            return None
        metadata = _decode_champion_metadata(row["champion_detected"])
        category = row["champion_category"]
        detected = parse_detected(
            metadata.get("detected", row["champion_detected"])
        )
        object_count = clean_nonnegative_int(
            metadata.get("object_count", row["champion_object_count"])
        )
        person_count, animal_count = infer_counts(
            category, object_count, detected,
            metadata.get("person_count"),
            metadata.get("animal_count"),
        )
        path = path_from_db(row["champion_path"])
        width = clean_nonnegative_int(metadata.get("width"))
        height = clean_nonnegative_int(metadata.get("height"))
        size_bytes = clean_nonnegative_int(metadata.get("size_bytes"))
        file_ts = clean_nonnegative_int(metadata.get("file_ts"))
        sharpness = clean_score(metadata.get("sharpness"))
        if "engine_score" in metadata:
            engine_score = clean_score(metadata.get("engine_score"))
        else:
            engine_score = clean_score(row["champion_score"])
        if path is not None:
            if size_bytes <= 0:
                size_bytes = max(0, safe_stat_size(path))
            if width <= 0 or height <= 0:
                width, height = image_dimensions(path)
            if sharpness is None and path.exists():
                sharpness = compute_sharpness(path)
            if file_ts <= 0:
                stat = safe_stat(path)
                if stat:
                    file_ts = int(stat.st_mtime)
        return ChampionInfo(
            path=path,
            origin_path=path_from_db(row["champion_origin_path"]),
            source=row["champion_source"],
            frame_sec=(
                float(row["champion_frame_sec"])
                if row["champion_frame_sec"] is not None
                else None
            ),
            md5=row["champion_md5"],
            category=category,
            object_count=object_count,
            person_count=person_count,
            animal_count=animal_count,
            sharpness=sharpness,
            engine_score=engine_score,
            detected=detected,
            width=width,
            height=height,
            size_bytes=size_bytes,
            file_ts=file_ts,
            rank_group=classify_rank_group(person_count, animal_count),
        )

    def update_champion(
        self, window_id: int, candidate: CandidateImage, kind: str
    ) -> None:
        metadata = {
            "version": 2,
            "person_count": candidate.person_count,
            "animal_count": candidate.animal_count,
            "object_count": candidate.object_count,
            "sharpness": candidate.sharpness,
            "engine_score": candidate.engine_score,
            "width": candidate.width,
            "height": candidate.height,
            "size_bytes": candidate.size_bytes,
            "file_ts": candidate.file_ts,
            "detected": list(candidate.detected),
        }
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET champion_score=?, champion_md5=?, "
                "champion_origin_path=?, champion_frame_sec=?, "
                "champion_source=?, champion_category=?, "
                "champion_object_count=?, champion_level=?, "
                "champion_path=?, champion_kind=?, champion_detected=?, "
                "champion_updated_at=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE window_id=?",
                (
                    candidate.engine_score, candidate.md5,
                    safe_rel_path(candidate.origin_path),
                    candidate.frame_sec, candidate.source,
                    candidate.category, candidate.object_count,
                    candidate.rank_group, safe_rel_path(candidate.path),
                    kind,
                    json.dumps(
                        metadata, ensure_ascii=False, separators=(",", ":")
                    ),
                    int(time.time()), window_id,
                ),
            )
            self.conn.commit()

    def cleanup_old_records(self) -> None:
        if Config.RETENTION_DAYS <= 0:
            return
        cutoff = int(time.time()) - Config.RETENTION_DAYS * 86400
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "DELETE FROM files WHERE window_id IN "
                "(SELECT window_id FROM windows "
                "WHERE window_end < ? AND window_id != 0)",
                (cutoff,),
            )
            cursor.execute(
                "DELETE FROM windows WHERE window_end < ? AND window_id != 0",
                (cutoff,),
            )
            cursor.execute(
                "DELETE FROM files WHERE window_id=0 AND discovered_at < ?",
                (cutoff,),
            )
            self.conn.commit()


def make_candidate(
    path: Path,
    source: str,
    origin_path: Optional[Path] = None,
    frame_sec: Optional[float] = None,
) -> Optional[CandidateImage]:
    if not is_valid_image_file(path):
        return None
    candidate = CandidateImage(
        path=path,
        source=source,
        origin_path=origin_path or (path if source == "photo" else None),
        frame_sec=frame_sec,
    )
    return apply_candidate_metrics(candidate)


def choose_photo_camera_half(candidate: CandidateImage) -> str:
    if candidate.upper_person_count > candidate.lower_person_count:
        return "upper"
    if candidate.lower_person_count > candidate.upper_person_count:
        return "lower"
    if candidate.upper_person_count > 0 and candidate.lower_person_count > 0:
        return "full"
    return "lower"


def prepare_photo_for_send(
    candidate: Optional[CandidateImage],
) -> tuple[Optional[Path], Optional[Path]]:
    if (
        candidate is None
        or candidate.source != "photo"
        or not is_valid_image_file(candidate.path)
        or cv2 is None
        or not Config.ACTIVE_CAMERA_ONLY
    ):
        return (candidate.path if candidate is not None else None, None)
    camera_half = choose_photo_camera_half(candidate)
    if camera_half == "full":
        return candidate.path, None
    try:
        image = cv2.imread(str(candidate.path), cv2.IMREAD_COLOR)
    except Exception:
        image = None
    if image is None or getattr(image, "size", 0) == 0:
        return candidate.path, None
    cropped = active_camera_image(image, camera_half)
    if cropped is None or cropped is image:
        return candidate.path, None
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", candidate.path.stem)
    send_dir = Config.TMP_DIR / (
        f"send_{safe_stem}_{camera_half}_{int(time.time_ns())}_"
        f"{threading.get_ident()}"
    )
    send_dir.mkdir(parents=True, exist_ok=True)
    fix_permissions(send_dir, is_dir=True)
    output = send_dir / candidate.path.name
    try:
        if not cv2.imwrite(
            str(output), cropped,
            [int(cv2.IMWRITE_JPEG_QUALITY), 100],
        ):
            raise OSError(f"não foi possível gravar foto recortada: {output}")
        fix_permissions(output)
        logger.info(
            "foto_recortada_enviada origem=%s recorte=%s arquivo=%s",
            candidate.path.name, camera_half, output.name,
        )
        return output, output
    except Exception as exc:
        logger.warning(
            "Falha ao recortar foto para envio (%s, metade=%s): %s",
            candidate.path.name, camera_half, exc,
        )
        shutil.rmtree(send_dir, ignore_errors=True)
        return candidate.path, None


def choose_photo_camera_half(candidate: CandidateImage) -> str:
    if candidate.upper_person_count > candidate.lower_person_count:
        return "upper"
    if candidate.lower_person_count > candidate.upper_person_count:
        return "lower"
    if candidate.upper_person_count > 0 and candidate.lower_person_count > 0:
        return "full"
    return "lower"


def prepare_photo_for_send(
    candidate: Optional[CandidateImage],
) -> tuple[Optional[Path], Optional[Path]]:
    if (
        candidate is None
        or candidate.source != "photo"
        or not is_valid_image_file(candidate.path)
        or cv2 is None
        or not Config.ACTIVE_CAMERA_ONLY
    ):
        return candidate.path if candidate is not None else None, None
    camera_half = choose_photo_camera_half(candidate)
    if camera_half == "full":
        return candidate.path, None
    try:
        image = cv2.imread(str(candidate.path), cv2.IMREAD_COLOR)
    except Exception:
        image = None
    if image is None or getattr(image, "size", 0) == 0:
        return candidate.path, None
    cropped = active_camera_image(image, camera_half)
    if cropped is None or cropped is image:
        return candidate.path, None
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", candidate.path.stem)
    send_dir = Config.TMP_DIR / (
        f"send_{safe_stem}_{camera_half}_{int(time.time_ns())}_"
        f"{threading.get_ident()}"
    )
    send_dir.mkdir(parents=True, exist_ok=True)
    fix_permissions(send_dir, is_dir=True)
    output = send_dir / candidate.path.name
    try:
        if not cv2.imwrite(
            str(output), cropped,
            [int(cv2.IMWRITE_JPEG_QUALITY), 100],
        ):
            raise OSError(f"não foi possível gravar foto recortada: {output}")
        fix_permissions(output)
        logger.info(
            "foto_recortada_enviada origem=%s recorte=%s arquivo=%s",
            candidate.path.name, camera_half, output.name,
        )
        return output, output
    except Exception as exc:
        logger.warning(
            "Falha ao recortar foto para envio (%s, metade=%s): %s",
            candidate.path.name, camera_half, exc,
        )
        shutil.rmtree(send_dir, ignore_errors=True)
        return candidate.path, None


T = TypeVar("T")


def sample_evenly(items: Sequence[T], limit: int) -> list[T]:
    if limit <= 0 or not items:
        return []
    ordered = list(items)
    if len(ordered) <= limit:
        return ordered
    if limit == 1:
        return [ordered[len(ordered) // 2]]
    step = (len(ordered) - 1) / float(limit - 1)
    indexes = sorted({
        min(len(ordered) - 1, int(round(i * step)))
        for i in range(limit)
    })
    return [ordered[index] for index in indexes]


def choose_evenly(paths: Sequence[Path], limit: int) -> list[Path]:
    valid = [path for path in paths if is_valid_image_file(path)]
    return sample_evenly(valid, limit)


def dedupe_media_paths(paths: Sequence[Path]) -> list[Path]:
    best: dict[tuple[int, str, bool], tuple[Path, tuple[int, float, float]]] = {}
    passthrough: list[Path] = []
    for path in paths:
        span = extract_motion_span(path)
        if span is None or not span.rec_key:
            passthrough.append(path)
            continue
        stat = safe_stat(path)
        score = (
            stat.st_size if stat else -1,
            span.end.timestamp(),
            stat.st_mtime if stat else 0.0,
        )
        key = (int(span.start.timestamp()), span.rec_key, is_video(path))
        current = best.get(key)
        if current is None or score > current[1]:
            best[key] = (path, score)
    return passthrough + [item[0] for item in best.values()]


def choose_immediate_media(files: Sequence[Path]) -> Optional[CandidateImage]:
    files = dedupe_media_paths(files)
    photos = sorted(
        (p for p in files if is_photo(p) and is_valid_image_file(p)),
        key=lambda p: p.name,
    )
    if photos:
        candidates = [
            candidate for photo in choose_evenly(
                photos, Config.MELHOR_FOTO_MAX_CANDIDATES
            )
            if (candidate := make_candidate(photo, "photo", photo)) is not None
        ]
        _, winner = run_melhor_foto(
            candidates, 0, "immediate", Config.MELHOR_FOTO_IMMEDIATE_TIMEOUT,
            (0, 0),
        )
        return winner
    videos = sorted(
        (p for p in files if is_video(p) and is_valid_video_file(p)),
        key=lambda p: p.name,
    )
    for video in videos[:1]:
        candidates = [
            candidate
            for frame_index, frame in enumerate(extract_first_frames(video, 5))
            for candidate in [make_candidate(
                frame, "frame", video, float(frame_index)
            )]
            if candidate is not None
        ]
        _, winner = run_melhor_foto(
            candidates, 0, "immediate", Config.MELHOR_FOTO_IMMEDIATE_TIMEOUT,
            (0, 0),
        )
        if winner is not None:
            return winner
    return None


def extract_single_frame(
    video_path: Path, time_sec: float = 5.0, fast: bool = False
) -> Optional[Path]:
    if not is_valid_video_file(video_path):
        return None
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", video_path.stem)
    frame_dir = Config.TMP_DIR / (
        f"single_{safe_stem}_{int(time.time_ns())}_{threading.get_ident()}"
    )
    frame_dir.mkdir(parents=True, exist_ok=True)
    fix_permissions(frame_dir, is_dir=True)
    output = frame_dir / "frame.jpg"
    raw = video_path.suffix.lower() == ".h264"
    fmt_args = ["-f", "h264"] if raw else []
    probe_args = (
        ["-analyzeduration", "100M", "-probesize", "100M"]
        if raw and not fast
        else []
    )
    timeout = (
        Config.FRAME_EXTRACT_TIMEOUT_FAST if fast
        else Config.FRAME_EXTRACT_TIMEOUT
    )
    base_cmd = [Config.FFMPEG_CMD, *probe_args, *fmt_args]
    if fast:
        seconds_to_try: list[float] = []
        for sec in (time_sec, 1.0, 0.0):
            if sec not in seconds_to_try:
                seconds_to_try.append(sec)
        # H.264 bruto pode não produzir frame quando o seek vem antes da
        # entrada; manter a tentativa rápida e fazer fallback pós-entrada.
        commands: list[list[str]] = [[
            *base_cmd, "-ss", str(seconds_to_try[0]), "-i",
            str(video_path),             "-vframes", "1", "-q:v", str(Config.MOTION_JPEG_QUALITY),
            str(output), "-y",
        ]]
        commands.extend([
            [
                *base_cmd, "-i", str(video_path), "-ss", str(sec),
                "-vframes", "1", "-q:v", str(Config.MOTION_JPEG_QUALITY),
                str(output), "-y",
            ]
            for sec in seconds_to_try
        ])
    else:
        commands = [
            [
                *base_cmd, "-ss", str(time_sec), "-i", str(video_path),
                "-vframes", "1", "-q:v", str(Config.MOTION_JPEG_QUALITY),
                str(output), "-y",
            ],
            [
                *base_cmd, "-i", str(video_path), "-ss", str(time_sec),
                "-vframes", "1", "-q:v", str(Config.MOTION_JPEG_QUALITY),
                str(output), "-y",
            ],
        ]
    for command in commands:
        try:
            subprocess.run(
                command, check=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=timeout,
            )
            if is_valid_image_file(output):
                fix_permissions(output)
                return output
        except (subprocess.SubprocessError, OSError):
            pass
    if time_sec != 0.0 and not fast:
        shutil.rmtree(frame_dir, ignore_errors=True)
        return extract_single_frame(video_path, 0.0, fast=False)
    shutil.rmtree(frame_dir, ignore_errors=True)
    return None


def extract_first_frames(video_path: Path, count: int) -> list[Path]:
    if count <= 0 or not is_valid_video_file(video_path):
        return []
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", video_path.stem)
    frame_dir = Config.TMP_DIR / (
        f"first_{safe_stem}_{int(time.time_ns())}_{threading.get_ident()}"
    )
    frame_dir.mkdir(parents=True, exist_ok=True)
    fix_permissions(frame_dir, is_dir=True)
    raw = video_path.suffix.lower() == ".h264"
    command = [
        Config.FFMPEG_CMD,
        *(["-f", "h264"] if raw else []),
        "-i", str(video_path),
        "-frames:v", str(count),
        "-q:v", str(Config.MOTION_JPEG_QUALITY),
        str(frame_dir / "frame_%02d.jpg"),
        "-y",
    ]
    try:
        subprocess.run(
            command, check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=Config.FRAME_EXTRACT_TIMEOUT_FAST,
        )
    except (subprocess.SubprocessError, OSError):
        shutil.rmtree(frame_dir, ignore_errors=True)
        return []
    frames = sorted(
        (path for path in frame_dir.glob("frame_*.jpg")
         if is_valid_image_file(path)),
        key=_frame_sequence_key,
    )[:count]
    if not frames:
        shutil.rmtree(frame_dir, ignore_errors=True)
    return frames


def parse_engine_json(stdout: str) -> Optional[dict[str, Any]]:
    text = stdout.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def engine_candidate_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candidates", "results", "images"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(data.get("path"), str):
        return [data]
    return []


def _limit_candidates_for_analysis(
    candidates: Sequence[CandidateImage], max_candidates: int = 0
) -> list[CandidateImage]:
    if max_candidates <= 0 or len(candidates) <= max_candidates:
        return list(candidates)
    logger.info(
        "Limitando candidatos de %d para %d",
        len(candidates), max_candidates,
    )
    candidates_with_sharpness: list[tuple[CandidateImage, float]] = []
    for candidate in candidates:
        sharpness = compute_sharpness(candidate.path)
        candidates_with_sharpness.append(
            (candidate, sharpness if sharpness is not None else 0.0)
        )
    candidates_with_sharpness.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in candidates_with_sharpness[:max_candidates]]


def run_melhor_foto(
    candidates: Sequence[CandidateImage],
    window_id: int,
    tag: str,
    timeout: int,
    priority: tuple[int, int],
) -> tuple[list[CandidateImage], Optional[CandidateImage]]:
    valid = [c for c in candidates if is_valid_image_file(c.path)]
    if not valid or not Config.MELHOR_FOTO_SCRIPT.exists():
        if not Config.MELHOR_FOTO_SCRIPT.exists():
            logger.warning(
                "melhor_foto.py não encontrado: %s", Config.MELHOR_FOTO_SCRIPT
            )
        return [], None
    valid = _limit_candidates_for_analysis(
        valid, max_candidates=Config.MELHOR_FOTO_MAX_CANDIDATES
    )
    command = [
        sys.executable, str(Config.MELHOR_FOTO_SCRIPT),
        "--engine", Config.MELHOR_FOTO_ENGINE,
        "--output", "json", "--quiet",
    ]
    if Config.MELHOR_FOTO_LOGS:
        command += [
            "--log-file",
            str(Config.TMP_DIR / f"melhor_foto_{tag}_{window_id}.log"),
        ]
    command += [str(candidate.path) for candidate in valid]
    with engine_slot(priority, f"{tag}:{window_id}"):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning(
                "Janela %s melhor_foto.py falhou (%s): %s",
                window_id, tag, exc,
            )
            return [], None
    if result.returncode != 0 or not result.stdout:
        logger.warning(
            "Janela %s melhor_foto.py código=%s tag=%s stderr=%s",
            window_id, result.returncode, tag,
            (result.stderr or "")[-500:],
        )
        return [], None
    data = parse_engine_json(result.stdout)
    if not data:
        logger.warning(
            "Janela %s melhor_foto.py retornou JSON inválido", window_id
        )
        return [], None
    by_path: dict[Path, CandidateImage] = {}
    for candidate in valid:
        try:
            by_path[candidate.path.resolve()] = candidate
        except OSError:
            by_path[candidate.path] = candidate
    scored: list[CandidateImage] = []
    for record in engine_candidate_records(data):
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        selected = Path(raw_path).expanduser()
        if not selected.is_absolute():
            selected = (Path.cwd() / selected).resolve()
        try:
            key = selected.resolve()
        except OSError:
            key = selected
        base = by_path.get(key)
        if base is None:
            continue
        parsed = parse_engine_candidate(base, record)
        scored.append(parsed)
    if not scored:
        return [], None
    winner = max(scored, key=lambda candidate: candidate.rank_key)
    logger.info(
        "Janela %s engine=%s candidatos=%d vencedor=%s",
        window_id, tag, len(scored), summarize_candidate(winner),
    )
    return scored, winner


def _frame_sequence_key(path: Path) -> tuple[int, str]:
    match = re.search(r"(?:frame[_-])?(\d+)", path.stem, re.IGNORECASE)
    if match:
        return int(match.group(1)), path.name
    return (10**9, path.name)


def _frame_number(path: Path) -> Optional[int]:
    match = re.search(r"frame[_-]([0-9]+)", path.stem, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _compute_motion_scores(
    frame_paths: Sequence[Path], camera_half: str = "lower"
) -> list[float]:
    n = len(frame_paths)
    if n < 2 or cv2 is None:
        return [0.0] * n
    w = max(16, int(Config.MOTION_ANALYSIS_WIDTH))
    h = max(12, int(Config.MOTION_ANALYSIS_HEIGHT))
    scores: list[float] = [0.0] * n
    prev_small: Optional[Any] = None
    for i, path in enumerate(frame_paths):
        try:
            img = active_camera_image(
                cv2.imread(str(path), cv2.IMREAD_GRAYSCALE), camera_half
            )
        except Exception:
            img = None
        if img is None or getattr(img, "size", 0) == 0:
            continue
        try:
            small = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        except Exception:
            continue
        if prev_small is not None:
            try:
                diff = cv2.absdiff(prev_small, small)
                scores[i] = float(cv2.mean(diff)[0])
            except Exception:
                scores[i] = 0.0
        prev_small = small
    return scores


def _compute_salience_scores_pair(
    frame_paths: Sequence[Path],
) -> dict[str, list[float]]:
    """Pontua cada frame pela fração de pixels que se destacam do fundo
    temporal de referência.

    Pessoas/animais geram *blobs* espacialmente coerentes (muitos pixels
    acima do limiar simultaneamente). Ruído ambiente (vento, folhagem,
    sombra) produz alterações esparsas e de baixa magnitude — poucos
    pixels acima do limiar por frame. Isso separa movimento real de
    agitação difusa, ao contrário da média simples de |diff| que trata
    um blob pequeno e forte igual a uma textura fraca e ampla.

    O fundo é estimado pelo percentil 20 (não pela mediana): assim,
    mesmo que o objeto permaneça presente em até ~80% dos frames, o
    fundo continua sendo estimado corretamente. Mediana falharia a
    partir de 50% de ocupação.
    """
    n = len(frame_paths)
    empty = {"upper": [0.0] * n, "lower": [0.0] * n}
    if n < 2 or cv2 is None:
        return empty
    try:
        import numpy as np
    except ImportError:
        return {
            half: _compute_motion_scores(frame_paths, half)
            for half in ("upper", "lower")
        }
    w = max(16, int(Config.MOTION_ANALYSIS_WIDTH))
    h = max(12, int(Config.MOTION_ANALYSIS_HEIGHT))
    stacks: dict[str, list[Any]] = {"upper": [], "lower": []}
    valid_indices: dict[str, list[int]] = {"upper": [], "lower": []}
    for i, path in enumerate(frame_paths):
        try:
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        except Exception:
            image = None
        if image is None or getattr(image, "size", 0) == 0:
            continue
        for camera_half in ("upper", "lower"):
            img = active_camera_image(image, camera_half)
            if img is None or getattr(img, "size", 0) == 0:
                continue
            try:
                small = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            except Exception:
                continue
            stacks[camera_half].append(small.astype(np.float32))
            valid_indices[camera_half].append(i)
    scores = {"upper": [0.0] * n, "lower": [0.0] * n}
    thr = float(Config.MOTION_SALIENCE_THRESHOLD)
    for camera_half in ("upper", "lower"):
        stack = stacks[camera_half]
        if len(stack) < 2:
            continue
        arr = np.stack(stack, axis=0)       # (N, h, w)
        bg = np.percentile(arr, 20, axis=0) # (h, w) fundo robusto
        diffs = np.abs(arr - bg)            # (N, h, w)
        for k, i in enumerate(valid_indices[camera_half]):
            scores[camera_half][i] = float((diffs[k] > thr).mean())
    return scores


def _compute_salience_scores(
    frame_paths: Sequence[Path], camera_half: str = "lower"
) -> list[float]:
    """Compatibilidade para chamadas que precisam de apenas uma metade."""
    return _compute_salience_scores_pair(frame_paths).get(
        camera_half, [0.0] * len(frame_paths)
    )


def _build_salience_cache(
    frame_sets: Sequence[MotionFrameSet],
) -> dict[Path, dict[str, list[float]]]:
    return {
        fs.video_path.resolve(): _compute_salience_scores_pair(fs.frames)
        for fs in frame_sets
    }


def _smooth_scores(scores: Sequence[float], window: int) -> list[float]:
    if not scores or window <= 1:
        return list(scores)
    try:
        import numpy as np
    except ImportError:
        return list(scores)
    w = max(1, int(window))
    kernel = np.ones(w, dtype=np.float32) / w
    arr = np.asarray(scores, dtype=np.float32)
    return np.convolve(arr, kernel, mode="same").tolist()


def _pick_camera_half(
    frame_sets: Sequence[MotionFrameSet],
    salience_cache: Optional[dict[Path, dict[str, list[float]]]] = None,
) -> str:
    half_scores: dict[str, float] = {}
    penalty = max(0.0, float(Config.MOTION_POSITION_PENALTY))
    for camera_half in ("upper", "lower"):
        half_best = -1.0
        for fs in frame_sets:
            if len(fs.frames) < 2:
                continue
            cached = (
                salience_cache.get(fs.video_path.resolve(), {})
                if salience_cache is not None
                else {}
            )
            scores = _smooth_scores(
                cached[camera_half]
                if camera_half in cached
                else _compute_salience_scores(fs.frames, camera_half),
                Config.MOTION_ANCHOR_SMOOTH,
            )
            denom = max(1, len(fs.frames) - 1)
            score = max(
                (
                    float(value) * (1.0 - penalty * (idx / denom))
                    for idx, value in enumerate(scores)
                ),
                default=0.0,
            )
            half_best = max(half_best, score)
        half_scores[camera_half] = half_best

    upper_score = half_scores.get("upper", -1.0)
    lower_score = half_scores.get("lower", -1.0)
    best_half = "upper" if upper_score > lower_score else "lower"
    best_score = max(upper_score, lower_score)
    margin = abs(upper_score - lower_score)
    min_salience = max(0.0, float(Config.MOTION_HALF_MIN_SALIENCE))
    min_margin = max(0.0, float(Config.MOTION_HALF_MIN_MARGIN))
    if best_score < min_salience:
        logger.info(
            "camera_metade escolhida=full motivo=saliencia_baixa "
            "superior=%.4f inferior=%.4f limite=%.4f",
            upper_score, lower_score, min_salience,
        )
        return "full"
    if margin < min_margin:
        logger.info(
            "camera_metade escolhida=full motivo=saliencia_ambigua "
            "superior=%.4f inferior=%.4f diferenca=%.4f limite=%.4f",
            upper_score, lower_score, margin, min_margin,
        )
        return "full"
    logger.info(
        "camera_metade escolhida=%s saliencia_ajustada=%.4f "
        "superior=%.4f inferior=%.4f",
        best_half, best_score, upper_score, lower_score,
    )
    return best_half


def _pick_motion_based_anchor(
    frame_sets: Sequence[MotionFrameSet],
    window_id: int,
    camera_half: str = "lower",
    salience_cache: Optional[dict[Path, dict[str, list[float]]]] = None,
) -> Optional[Path]:
    """Escolhe a âncora de movimento pelo *pico de saliência* sobre o
    fundo temporal, com prior temporal forte a favor do início do clipe
    (clipes [M] são gravados porque o movimento começou — a ação está
    nos primeiros segundos).

    Piso de confiabilidade: se o pico ajustado for inferior a
    `MOTION_SALIENCE_FLOOR`, considera-se que o clipe não tem movimento
    real (foi gravado por trigger externo e a cena é estática). Nesse
    caso, a âncora cai deterministicamente sobre o início do clipe,
    onde o gatilho ocorreu — nunca sobre ruído arbitrário.
    """
    if cv2 is None or not frame_sets:
        return None
    best_anchor: Optional[Path] = None
    best_adjusted = -1.0
    best_raw = 0.0
    best_index = -1
    best_total = 0
    best_video_name: Optional[str] = None
    penalty = max(0.0, float(Config.MOTION_POSITION_PENALTY))
    for fs in frame_sets:
        n = len(fs.frames)
        if n < 2:
            continue
        cached = (
            salience_cache.get(fs.video_path.resolve(), {})
            if salience_cache is not None
            else {}
        )
        scores = (
            cached[camera_half]
            if camera_half in cached
            else _compute_salience_scores(fs.frames, camera_half)
        )
        smoothed = _smooth_scores(scores, Config.MOTION_ANCHOR_SMOOTH)
        denom = max(1, n - 1)
        for idx, value in enumerate(smoothed):
            adjusted = float(value) * (1.0 - penalty * (idx / denom))
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_raw = float(value)
                best_index = idx
                best_anchor = fs.frames[idx]
                best_video_name = fs.video_path.name
                best_total = n
    if best_anchor is None:
        return None
    floor = max(0.0, float(Config.MOTION_SALIENCE_FLOOR))
    if best_raw < floor:
        fs = frame_sets[0]
        n = len(fs.frames)
        # Gatilho do [M] fica tipicamente ~1-2s dentro do clipe;
        # a 3 fps isso corresponde aos frames 3..6.
        idx = min(n - 1, 4)
        logger.info(
            "janela=%s ancora_movimento fallback_inicio motivo="
            "saliencia_baixa(%.4f<%.4f) frame=%s indice=%d/%d",
            window_id, best_raw, floor,
            fs.frames[idx].name, idx, n - 1,
        )
        return fs.frames[idx]
    logger.info(
        "janela=%s ancora_movimento escolhida frame=%s video=%s "
        "saliencia=%.4f ajustada=%.4f indice=%d/%d total_frames=%d",
        window_id, best_anchor.name, best_video_name or "?",
        best_raw, best_adjusted, best_index,
        max(0, best_total - 1), len(frame_sets),
    )
    return best_anchor


def pick_motion_anchor(
    candidates: Sequence[CandidateImage],
    window_id: int,
    frame_sets: Sequence[MotionFrameSet] = (),
    camera_half: str = "lower",
    salience_cache: Optional[dict[Path, dict[str, list[float]]]] = None,
) -> Optional[Path]:
    frame_candidates = [
        c for c in candidates if c.source == "frame" and c.origin_path
    ]
    if not frame_candidates:
        return None
    detected = [
        c for c in frame_candidates
        if c.person_count > 0 or c.animal_count > 0
    ]
    if detected:
        def rank_detected(c: CandidateImage) -> tuple[int, int, float, float]:
            timestamp = (
                c.frame_sec if c.frame_sec is not None else float(c.file_ts)
            )
            quality = (
                c.engine_score
                if c.engine_score is not None
                else (c.sharpness or -1.0)
            )
            return (c.person_count, c.animal_count, timestamp, quality)
        best_detected = max(detected, key=rank_detected)
        logger.debug(
            "janela=%s ancora_escolhida modo=deteccao frame=%s "
            "pessoas=%d animais=%d",
            window_id, best_detected.path.name,
            best_detected.person_count, best_detected.animal_count,
        )
        return best_detected.path
    motion_anchor = _pick_motion_based_anchor(
        frame_sets, window_id, camera_half, salience_cache
    )
    if motion_anchor is not None:
        return motion_anchor

    def rank_time(c: CandidateImage) -> float:
        return (
            c.frame_sec if c.frame_sec is not None else float(c.file_ts)
        )
    best_time = max(frame_candidates, key=rank_time)
    logger.debug(
        "janela=%s ancora_escolhida modo=fallback_tempo frame=%s",
        window_id, best_time.path.name,
    )
    return best_time.path


def _check_video_duration(video_path: Path) -> bool:
    if not Config.FFPROBE_CMD:
        return True
    try:
        result = subprocess.run(
            [
                Config.FFPROBE_CMD, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip()) > 0.1
    except Exception:
        pass
    return False


def deduplicate_frames(
    frame_paths: Sequence[Path], threshold: float
) -> list[Path]:
    if cv2 is None or not frame_paths:
        return list(frame_paths)
    kept = [frame_paths[0]]
    prev_img = cv2.imread(str(frame_paths[0]), cv2.IMREAD_GRAYSCALE)
    if prev_img is None:
        return list(frame_paths)
    h, w = 120, 160
    prev_small = cv2.resize(prev_img, (w, h), interpolation=cv2.INTER_AREA)
    for path in frame_paths[1:]:
        curr_img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if curr_img is None:
            continue
        curr_small = cv2.resize(curr_img, (w, h), interpolation=cv2.INTER_AREA)
        diff = cv2.absdiff(prev_small, curr_small)
        mean_diff = cv2.mean(diff)[0]
        if mean_diff >= threshold:
            kept.append(path)
            prev_small = curr_small
    return kept


def create_motion_mp4(
    frame_paths: Sequence[Path],
    output_path: Path,
    camera_half: str = "lower",
) -> tuple[Optional[Path], int]:
    MIN_FRAMES_FOR_VIDEO = 30
    original_count = len(frame_paths)
    if original_count < 2:
        return None, 0
    deduplicated = deduplicate_frames(
        list(frame_paths), Config.MOTION_DEDUP_THRESHOLD
    )
    if len(deduplicated) >= MIN_FRAMES_FOR_VIDEO:
        frame_paths = deduplicated
        logger.info(
            "Deduplicação: %d -> %d frames", original_count, len(frame_paths)
        )
    else:
        logger.info(
            "Deduplicação removeu frames demais (%d -> %d), "
            "usando %d frames originais",
            original_count, len(deduplicated), original_count,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pattern_dir = output_path.parent / "frames"
    pattern_dir.mkdir(parents=True, exist_ok=True)
    try:
        for idx, frame in enumerate(frame_paths, start=1):
            target = pattern_dir / f"frame_{idx:04d}.jpg"
            if cv2 is None or not Config.ACTIVE_CAMERA_ONLY:
                shutil.copy2(frame, target)
                continue
            image = cv2.imread(str(frame), cv2.IMREAD_COLOR)
            active = active_camera_image(image, camera_half)
            if active is image or active is None:
                shutil.copy2(frame, target)
                continue
            if not cv2.imwrite(
                str(target),
                active,
                [int(cv2.IMWRITE_JPEG_QUALITY), 100],
            ):
                raise OSError(f"não foi possível gravar frame recortado: {target}")
        vf = (
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
            "drawtext=text='%{pts\\:hms}':x=10:y=10:"
            "fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5,"
            "format=yuv420p"
        )
        strategies = [
            {
                "codec": [
                    "-c:v", "libx264",
                    "-profile:v", "baseline", "-level", "3.0",
                ],
                "extra": [
                    "-x264-params",
                    "keyint=10:min-keyint=10:no-scenecut=1",
                ],
                "label": "H.264 Baseline",
            },
            {
                "codec": [
                    "-c:v", "libx264",
                    "-profile:v", "main", "-level", "3.1",
                ],
                "extra": ["-x264-params", "bframes=0"],
                "label": "H.264 Main",
            },
        ]
        for strat in strategies:
            command = [
                Config.FFMPEG_CMD, "-hide_banner", "-loglevel", "error",
                "-framerate", str(Config.MOTION_FPS),
                "-i", str(pattern_dir / "frame_%04d.jpg"),
                *strat["codec"],
                "-pix_fmt", "yuv420p",
                "-vf", vf,
                "-r", str(Config.MOTION_FPS), "-vsync", "cfr",
                "-crf", str(Config.MOTION_VIDEO_CRF),
                "-preset", Config.MOTION_VIDEO_PRESET,
                *strat["extra"],
                "-movflags", "+faststart", "-y", str(output_path),
            ]
            try:
                subprocess.run(
                    command, check=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=Config.MOTION_VIDEO_TIMEOUT,
                )
                if _check_video_duration(output_path):
                    duration = len(frame_paths) / Config.MOTION_FPS
                    logger.info(
                        "Vídeo gerado com sucesso (%s): %d frames, %.1fs",
                        strat["label"], len(frame_paths), duration,
                    )
                    fix_permissions(output_path)
                    return output_path, len(frame_paths)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning(
                    "Estratégia %s falhou: %s", strat["label"], exc
                )
                continue
        logger.error("Todas as estratégias de geração de vídeo falharam.")
        return None, 0
    except Exception as exc:
        logger.warning("Falha geral ao gerar MP4 de movimento: %s", exc)
        return None, 0
    finally:
        shutil.rmtree(pattern_dir, ignore_errors=True)


def _dynamic_motion_frame_count(
    frame_paths: Sequence[Path],
    anchor_num: int,
    candidates: Sequence[CandidateImage],
    salience_scores: Optional[Sequence[float]] = None,
) -> int:
    fps = max(1, int(Config.MOTION_FPS))
    min_frames = max(2, int(Config.MOTION_MIN_SECONDS) * fps)
    max_frames = max(min_frames, int(Config.MOTION_MAX_SECONDS) * fps)
    limit = min(max_frames, len(frame_paths))
    if limit <= min_frames:
        return limit

    activity: set[int] = set()
    scores = (
        list(salience_scores)
        if salience_scores is not None
        else _compute_salience_scores(frame_paths)
    )
    if scores:
        peak = max(scores)
        threshold = max(
            float(Config.MOTION_SALIENCE_FLOOR),
            peak * 0.5,
        )
        activity.update(
            index for index, score in enumerate(scores)
            if score >= threshold and score >= float(Config.MOTION_SALIENCE_FLOOR)
        )

    origin = frame_paths[0].parent
    for candidate in candidates:
        if candidate.source != "frame" or candidate.origin_path is None:
            continue
        if candidate.path.parent != origin:
            continue
        if candidate.person_count > 0 or candidate.animal_count > 0:
            number = _frame_number(candidate.path)
            if number is not None:
                activity.add(number - 1)

    if not activity:
        return min_frames

    first = max(0, min(activity))
    last = min(len(frame_paths) - 1, max(activity))
    active_span = last - first + 1
    padding = max(1, fps)
    desired = max(min_frames, active_span + 2 * padding)
    if any(
        candidate.source == "frame"
        and candidate.origin_path is not None
        and candidate.path.parent == origin
        and (candidate.person_count > 0 or candidate.animal_count > 0)
        for candidate in candidates
    ):
        desired = max(desired, min_frames + fps * 2)
    return min(limit, desired)


def _resolve_anchor_for_motion(
    window_id: int,
    anchor: Path,
    candidates: Sequence[CandidateImage],
) -> Optional[CandidateImage]:
    usable = [
        c for c in candidates
        if c.path is not None and c.origin_path is not None and c.source == "frame"
    ]
    if not usable:
        logger.info(
            "janela=%s movimento_nao_gerado motivo=sem_origem_anchor",
            window_id,
        )
        return None
    exact = next(
        (c for c in usable if c.path == anchor),
        None,
    )
    if exact is not None:
        return exact
    same_parent = [
        c for c in usable
        if c.path.parent == anchor.parent
    ]
    pool = same_parent if same_parent else usable
    anchor_num = _frame_number(anchor)
    selected: Optional[CandidateImage] = None
    if anchor_num is not None:
        numbered: list[tuple[CandidateImage, int]] = []
        for c in pool:
            c_num = _frame_number(c.path)
            if c_num is not None:
                numbered.append((c, c_num))
        if numbered:
            selected = min(
                numbered,
                key=lambda item: (
                    abs(item[1] - anchor_num),
                    item[1],
                    str(item[0].path),
                ),
            )[0]
    if selected is None:
        def quality_score(c: CandidateImage) -> tuple[int, int, float, str]:
            try:
                pc = int(c.person_count or 0)
            except Exception:
                pc = 0
            try:
                ac = int(c.animal_count or 0)
            except Exception:
                ac = 0
            try:
                es = float(c.engine_score or 0.0)
            except Exception:
                es = 0.0
            try:
                sh = float(c.sharpness or 0.0)
            except Exception:
                sh = 0.0
            return (pc, ac, max(es, sh), str(c.path))
        selected = max(pool, key=quality_score)
    logger.info(
        "janela=%s ancora_substituida original=%s substituta=%s motivo=anchor_fora_dos_candidatos",
        window_id,
        anchor.name,
        selected.path.name,
    )
    if selected.origin_path is None:
        logger.info(
            "janela=%s movimento_nao_gerado motivo=sem_origem_anchor",
            window_id,
        )
        return None
    return selected


def build_motion_video(
    candidates: Sequence[CandidateImage],
    window_id: int,
    frame_sets: Sequence[MotionFrameSet],
) -> Optional[tuple[Path, int]]:
    if not Config.ENHANCED_MOTION_ENABLED:
        return None
    salience_cache = _build_salience_cache(frame_sets)
    camera_half = _pick_camera_half(frame_sets, salience_cache)
    upper_people = max(
        (candidate.upper_person_count for candidate in candidates),
        default=0,
    )
    lower_people = max(
        (candidate.lower_person_count for candidate in candidates),
        default=0,
    )
    if upper_people > 0 and lower_people == 0:
        camera_half = "upper"
        logger.info(
            "camera_metade escolhida=upper motivo=pessoa_detectada "
            "superior=%d inferior=%d",
            upper_people, lower_people,
        )
    elif lower_people > 0 and upper_people == 0:
        camera_half = "lower"
        logger.info(
            "camera_metade escolhida=lower motivo=pessoa_detectada "
            "superior=%d inferior=%d",
            upper_people, lower_people,
        )
    elif upper_people > 0 and lower_people > 0:
        camera_half = "full"
        logger.info(
            "camera_metade escolhida=full motivo=pessoas_nas_duas_cameras "
            "superior=%d inferior=%d",
            upper_people, lower_people,
        )
    anchor = pick_motion_anchor(
        candidates, window_id, frame_sets, camera_half, salience_cache
    )
    if not anchor:
        logger.info(
            "janela=%s movimento_nao_gerado motivo=sem_frame_candidato",
            window_id,
        )
        return None
    anchor_candidate = _resolve_anchor_for_motion(window_id, anchor, candidates)
    if anchor_candidate is None:
        return None
    all_frames: list[Path] = []
    for frame_set in frame_sets:
        if (
            frame_set.video_path.resolve()
            == anchor_candidate.origin_path.resolve()
        ):
            all_frames = list(frame_set.frames)
            break
    if len(all_frames) < 2:
        logger.info(
            "janela=%s movimento_nao_gerado motivo=frames_insuficientes "
            "total=%d",
            window_id, len(all_frames),
        )
        return None
    numbers = [_frame_number(p) for p in all_frames]
    anchor_num = _frame_number(anchor)
    if anchor_num is None:
        return None
    desired_count = min(
        Config.MOTION_FRAME_COUNT,
        _dynamic_motion_frame_count(
            all_frames,
            anchor_num,
            candidates,
            (
                salience_cache.get(anchor_candidate.origin_path.resolve(), {})
                .get(camera_half if camera_half in ("upper", "lower") else "lower")
                if anchor_candidate.origin_path is not None
                else None
            ),
        ),
        len(all_frames),
    )
    half = desired_count // 2
    start_num = max(1, anchor_num - half)
    end_num = min(len(all_frames), start_num + desired_count - 1)
    if end_num - start_num < desired_count:
        if start_num == 1:
            end_num = min(len(all_frames), desired_count)
        else:
            start_num = max(1, end_num - desired_count + 1)
    available = {
        n: p for n, p in zip(numbers, all_frames) if n is not None
    }
    selected = [
        available.get(n)
        for n in range(start_num, end_num + 1)
        if n in available
    ]
    if len(selected) < 2:
        selected = all_frames[:desired_count]
    frame_paths = [p for p in selected if p is not None]
    if len(frame_paths) < 2:
        logger.info(
            "janela=%s movimento_nao_gerado "
            "motivo=frames_selecionados_insuficientes",
            window_id,
        )
        return None
    output_dir = Config.TMP_DIR / f"motion_{window_id}_{time.time_ns()}"
    output = output_dir / f"motion_{window_id}.mp4"
    video, final_frame_count = create_motion_mp4(
        frame_paths, output, camera_half
    )
    if video:
        logger.info(
            "janela=%d movimento mp4 gerado frames=%d fps=%d "
            "duracao_aprox=%.1fs arquivo=%s centro=%s",
            window_id, final_frame_count, Config.MOTION_FPS,
            final_frame_count / Config.MOTION_FPS, video.name, anchor.name,
        )
        return video, final_frame_count
    return None


def _extract_all_frames(video: Path, window_id: int) -> list[Path]:
    if not is_valid_video_file(video):
        return []
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", video.stem)
    frame_dir = Config.TMP_DIR / (
        f"all_{safe_stem}_{window_id}_"
        f"{time.time_ns()}_{threading.get_ident()}"
    )
    frame_dir.mkdir(parents=True, exist_ok=True)
    fix_permissions(frame_dir, is_dir=True)
    if not Config.EXTRACT_FRAMES_SCRIPT.exists():
        logger.warning(
            "extract_frames.py não encontrado: %s", Config.EXTRACT_FRAMES_SCRIPT
        )
        return []
    command = [
        sys.executable, str(Config.EXTRACT_FRAMES_SCRIPT),
        str(video), str(frame_dir), "0",
        "--fps", str(Config.MOTION_FPS),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=Config.EXTRACT_ALL_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "Janela %s extract_frames erro em %s: %s",
            window_id, video.name, exc,
        )
        return []
    generated = sorted(
        (
            p for p in frame_dir.glob("*.jpg")
            if is_valid_image_file(p)
        ),
        key=_frame_sequence_key,
    )
    if generated:
        if result.returncode != 0:
            logger.warning(
                "Janela %s extract_frames terminou com código=%s para %s, "
                "mas preservou %d frame(s) válidos",
                window_id, result.returncode, video.name, len(generated),
            )
        else:
            logger.info(
                "Janela %s extração video=%s frames_validos=%d",
                window_id, video.name, len(generated),
            )
        if len(generated) >= 2:
            return generated
        logger.warning(
            "Janela %s extração insuficiente em %s (%d frame(s)); "
            "tentando fallback pontual",
            window_id, video.name, len(generated),
        )
    elif result.returncode != 0:
        logger.warning(
            "Janela %s extract_frames falhou em %s stderr=%s",
            window_id, video.name, (result.stderr or "")[-500:],
        )
    fallback = _extract_fallback_motion_frames(video, window_id)
    if generated or fallback:
        merged = generated[:]
        seen: set[str] = set()
        for path in merged:
            digest = compute_md5(path) or path.name
            seen.add(digest)
        for path in fallback:
            digest = compute_md5(path) or path.name
            if digest in seen:
                continue
            seen.add(digest)
            merged.append(path)
        logger.info(
            "Janela %s extração final video=%s frames_validos=%d fallback=%d",
            window_id, video.name, len(merged), len(fallback),
        )
        if len(merged) >= 2:
            return merged
        return merged
    return []


def _extract_fallback_motion_frames(video: Path, window_id: int) -> list[Path]:
    timestamps = (0.0, 1.0, 2.5, 5.0, 10.0)
    collected: list[Path] = []
    seen_hashes: set[str] = set()
    for timestamp in timestamps:
        frame = extract_single_frame(video, timestamp, fast=True)
        if frame is None or not is_valid_image_file(frame):
            continue
        digest = compute_md5(frame) or frame.name
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        collected.append(frame)
        if len(collected) >= 4:
            break
    if collected:
        logger.warning(
            "Janela %s fallback de vídeo usado em %s: %d frame(s)",
            window_id, video.name, len(collected),
        )
    return collected


def sample_frames_across_timeline(
    frame_part: Sequence[CandidateImage],
    videos: Sequence[Path],
    total_budget: int,
) -> list[CandidateImage]:
    if total_budget <= 0:
        return []
    per_video: dict[Path, list[CandidateImage]] = {}
    for c in frame_part:
        if c.origin_path:
            per_video.setdefault(c.origin_path.resolve(), []).append(c)
    total_frames = sum(len(group) for group in per_video.values())
    if total_frames == 0:
        return []
    selected: list[CandidateImage] = []
    for video in videos:
        group = per_video.get(video.resolve())
        if not group:
            continue
        group.sort(key=lambda c: (
            _frame_number(c.path) is None,
            _frame_number(c.path) or 0,
            c.path.name,
        ))
        share = max(1, round(total_budget * len(group) / total_frames))
        share = min(share, len(group))
        if share >= len(group):
            selected.extend(group)
            continue
        segment_size = len(group) / share
        for i in range(share):
            lo = int(i * segment_size)
            hi = (
                int((i + 1) * segment_size)
                if i < share - 1
                else len(group)
            )
            hi = max(hi, lo + 1)
            segment = group[lo:hi]
            best = max(
                segment, key=lambda c: compute_sharpness(c.path) or -1.0
            )
            selected.append(best)
    return selected[:total_budget]


def select_best_image(
    files: Sequence[Path],
    window_id: int,
    mode: str,
    sort_key: int = 0,
    rebuild_videos: Sequence[Path] = (),
) -> tuple[
    Optional[CandidateImage],
    list[CandidateImage],
    list[MotionFrameSet],
]:
    files = dedupe_media_paths(files)
    photos = sorted(
        (p for p in files if is_photo(p)), key=lambda p: p.name
    )
    videos = sorted(
        (p for p in files if is_video(p)), key=lambda p: p.name
    )
    if mode == "immediate":
        # O primeiro aviso não pode esperar o YOLO. A seleção refinada fica
        # para a fase aprimorada, que roda fora do caminho crítico.
        winner = choose_immediate_media(files)
        return winner, [], []

    candidates: list[CandidateImage] = []
    frame_sets: list[MotionFrameSet] = []

    if mode == "late":
        for photo in choose_evenly(photos, Config.LATE_MAX_PHOTOS):
            candidate = make_candidate(photo, "photo", photo)
            if candidate:
                candidates.append(candidate)
        seconds = (2.0, 5.0, 10.0)[:Config.LATE_FRAMES_PER_VIDEO]
        for video in videos[:Config.LATE_MAX_VIDEOS]:
            for second in seconds:
                frame = extract_single_frame(video, second, fast=True)
                if frame:
                    candidate = make_candidate(frame, "frame", video, second)
                    if candidate:
                        candidates.append(candidate)
        if not candidates:
            return None, [], []
        scored, winner = run_melhor_foto(
            candidates, window_id, "late",
            Config.MELHOR_FOTO_LATE_TIMEOUT, (2, sort_key),
        )
        return winner, scored or candidates, []

    if mode == "late_rebuild":
        for photo in choose_evenly(photos, Config.LATE_MAX_PHOTOS):
            candidate = make_candidate(photo, "photo", photo)
            if candidate:
                candidates.append(candidate)
        rebuild_set = [
            p for p in videos
            if any(p.resolve() == r.resolve() for r in rebuild_videos)
        ][: max(1, int(Config.LATE_REBUILD_MAX_VIDEOS))]
        other_videos = [p for p in videos if p not in rebuild_set]
        for video in rebuild_set:
            generated = _extract_all_frames(video, window_id)
            for frame in generated:
                candidate = make_candidate(frame, "frame", video, None)
                if candidate:
                    candidates.append(candidate)
            if generated:
                frame_sets.append(
                    MotionFrameSet(video_path=video, frames=tuple(generated))
                )
        seconds = (2.0, 5.0, 10.0)[:Config.LATE_FRAMES_PER_VIDEO]
        for video in other_videos[:Config.LATE_MAX_VIDEOS]:
            for second in seconds:
                frame = extract_single_frame(video, second, fast=True)
                if frame:
                    candidate = make_candidate(frame, "frame", video, second)
                    if candidate:
                        candidates.append(candidate)
        if not candidates:
            return None, [], []
        scored, winner = run_melhor_foto(
            candidates, window_id, "late_rebuild",
            Config.MELHOR_FOTO_LATE_REBUILD_TIMEOUT, (2, sort_key),
        )
        return winner, scored or candidates, frame_sets

    if mode != "enhanced":
        raise ValueError(f"Modo desconhecido: {mode}")

    for photo in choose_evenly(photos, Config.ENHANCED_MAX_PHOTOS):
        candidate = make_candidate(photo, "photo", photo)
        if candidate:
            candidates.append(candidate)
    videos_to_extract = [
        p for p in dedupe_media_paths(videos) if is_video(p)
    ]
    for video in videos_to_extract:
        generated = _extract_all_frames(video, window_id)
        for frame in generated:
            candidate = make_candidate(frame, "frame", video, None)
            if candidate:
                candidates.append(candidate)
        if generated:
            frame_sets.append(
                MotionFrameSet(video_path=video, frames=tuple(generated))
            )
    if not candidates:
        return None, [], []
    if (
        Config.ENHANCED_ANALYSIS_MAX_FRAMES > 0
        and len(candidates) > Config.ENHANCED_ANALYSIS_MAX_FRAMES
    ):
        photos_part = [c for c in candidates if c.source == "photo"]
        frame_part = [c for c in candidates if c.source == "frame"]
        frame_limit = max(
            0, Config.ENHANCED_ANALYSIS_MAX_FRAMES - len(photos_part)
        )
        selected_frames = sample_frames_across_timeline(
            frame_part, videos_to_extract, frame_limit
        )
        candidates = photos_part + selected_frames[:frame_limit]
        logger.info(
            "Janela %s análise limitada: candidatos=%d limite=%d",
            window_id, len(candidates),
            Config.ENHANCED_ANALYSIS_MAX_FRAMES,
        )
    else:
        logger.info(
            "Janela %s análise completa: %d candidatos (sem limite)",
            window_id, len(candidates),
        )
    scored, winner = run_melhor_foto(
        candidates, window_id, "enhanced",
        Config.MELHOR_FOTO_ENHANCED_TIMEOUT, (1, sort_key),
    )
    return winner, scored or candidates, frame_sets


def send_whatsapp(
    message: str, media_path: Optional[Path] = None
) -> bool:
    if not Config.WHATSAPP_SCRIPT.exists():
        logger.error(
            "Script do WhatsApp não encontrado: %s",
            Config.WHATSAPP_SCRIPT,
        )
        return False
    if not Config.WHATSAPP_NUMBERS:
        logger.error("Nenhum número de WhatsApp configurado")
        return False
    command = [
        sys.executable, str(Config.WHATSAPP_SCRIPT),
        "-n", ", ".join(Config.WHATSAPP_NUMBERS),
        "-m", message,
    ]
    env = os.environ.copy()
    if media_path and is_valid_media_file(media_path):
        if is_video(media_path):
            media_timeout_base = max(
                90,
                int(env.get("MEDIA_SEND_TIMEOUT_BASE", "30") or 30),
            )
            env["MEDIA_SEND_TIMEOUT_BASE"] = str(media_timeout_base)
            env["SCRIPT_TIMEOUT"] = str(
                max(300, int(env.get("SCRIPT_TIMEOUT", "120") or 120))
            )
        else:
            env.pop("MEDIA_SEND_TIMEOUT_BASE", None)
        if is_video(media_path):
            command += ["-v", str(media_path)]
        elif is_photo(media_path):
            command += ["-f", str(media_path)]
        else:
            command += ["-a", str(media_path)]
    timeout = (
        Config.WHATSAPP_VIDEO_TIMEOUT
        if media_path and is_video(media_path)
        else Config.WHATSAPP_TIMEOUT
    )
    if media_path and is_video(media_path):
        timeout = max(timeout, 300)
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=timeout, check=False, env=env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Falha no WhatsApp: %s", exc)
        return False
    if result.returncode == 0:
        return True
    logger.warning(
        "WhatsApp retornou código=%s stderr=%s",
        result.returncode, (result.stderr or "")[-500:],
    )
    return False


def score_fmt(score: Optional[float]) -> str:
    return f"{score:.2f}" if score is not None else "N/D"


def discovered_fmt(seconds: Optional[int]) -> str:
    if seconds is None or seconds <= 0:
        return "agora" if seconds is not None else "indisponível"
    if seconds < 60:
        return f"há {seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"há {minutes}min"
    return f"há {minutes // 60}h"


def log_decision(
    phase: str,
    window_id: int,
    action: str,
    reason: str,
    event_code: Optional[str],
    candidate: Optional[CandidateImage] = None,
    champion: Optional[ChampionInfo] = None,
) -> None:
    parts = [
        f"janela={window_id}", f"fase={phase}",
        f"acao={action}", f"motivo={reason}",
    ]
    if event_code:
        parts.append(f"evento={event_code}")
    if candidate:
        parts.append(summarize_candidate(candidate))
    if champion:
        parts.append(summarize_champion(champion))
    logger.info(" ".join(parts))


def window_message_prefix(meta: WindowMeta) -> tuple[str, str]:
    start = datetime.fromtimestamp(meta.window_start).strftime("%H:%M:%S")
    end = datetime.fromtimestamp(
        max(meta.window_end, meta.window_start)
    ).strftime("%H:%M:%S")
    return start, end


def collect_temp_paths(
    winner: Optional[CandidateImage],
    motion_video: Optional[Path],
    frame_sets: Sequence[MotionFrameSet],
) -> list[Path]:
    paths: list[Path] = []
    if winner is not None and winner.source == "frame":
        paths.append(winner.path)
    if motion_video is not None:
        paths.append(motion_video)
    for frame_set in frame_sets:
        if frame_set.frames:
            paths.append(frame_set.frames[0])
    return paths


def cleanup_attempt_temp(paths: Iterable[Optional[Path]]) -> None:
    tmp_root = Config.TMP_DIR.resolve()
    seen: set[Path] = set()
    for path in paths:
        if path is None:
            continue
        try:
            parent = path.resolve().parent
        except OSError:
            continue
        if parent in seen or tmp_root not in parent.parents:
            continue
        seen.add(parent)
        shutil.rmtree(parent, ignore_errors=True)


def worker_immediate(
    db: Database, window_id: int, stats: CycleStats
) -> None:
    meta = db.get_window_meta(window_id)
    if meta is None:
        db.mark_immediate_done(window_id, None, False, None, "no_meta")
        stats.inc("immediate_failed")
        logger.warning(
            "janela=%s fase=imediato acao=falhou motivo=no_meta",
            window_id,
        )
        return
    files = db.get_normal_files(window_id)
    if not files:
        db.mark_immediate_done(
            window_id, None, False, meta.event_code, "no_files"
        )
        stats.inc("immediate_failed")
        logger.warning(
            "janela=%s fase=imediato acao=falhou motivo=no_files evento=%s",
            window_id, meta.event_code,
        )
        return
    if (
        len(files) == 1
        and is_photo(files[0])
        and is_valid_image_file(files[0])
    ):
        # Uma única foto já é a evidência completa do alerta. Evita chamar o
        # motor de análise e envia a imagem original imediatamente.
        candidate = make_candidate(files[0], "photo", files[0])
    else:
        candidate, _, _ = select_best_image(
            files, window_id, "immediate", meta.window_start
        )
    if candidate is None:
        attempts = db.increment_attempt(window_id, "attempts")
        if attempts >= Config.MAX_ALERT_RETRIES:
            db.mark_immediate_done(
                window_id, None, False, meta.event_code, "no_media"
            )
            stats.inc("immediate_failed")
            logger.warning(
                "janela=%s fase=imediato acao=falhou motivo=no_media "
                "tentativas=%d arquivos=%d evento=%s",
                window_id, attempts, len(files), meta.event_code,
            )
        else:
            logger.info(
                "janela=%s fase=imediato acao=retry motivo=no_media "
                "tentativa=%d/%d arquivos=%d",
                window_id, attempts, Config.MAX_ALERT_RETRIES, len(files),
            )
        return
    send_path, temp_path = prepare_photo_for_send(candidate)
    try:
        first_discovery, last_discovery = db.discovery_times(window_id)
        now = int(time.time())
        reference = (
            last_discovery if last_discovery is not None else first_discovery
        )
        discovery_age = (
            max(0, now - reference) if reference is not None else None
        )
        start, end = window_message_prefix(meta)
        message = (
            f"🚨 Movimento · ID #{window_id}\n"
            f"{start}-{end} · {meta.file_count} arq "
            f"(📷{meta.photo_count} 📹{meta.video_count}) "
            f"· desc {discovered_fmt(discovery_age)}\n"
            f"{Config.CAMERA_LINK}"
        )
        if send_whatsapp(message, send_path):
            db.mark_immediate_done(
                window_id, candidate.path, True, meta.event_code, None
            )
            db.update_champion(window_id, candidate, "immediate")
            stats.inc("immediate_sent")
            log_decision(
                "imediato", window_id, "enviado", "alerta_rapido",
                meta.event_code, candidate,
            )
            return
        attempts = db.increment_attempt(window_id, "attempts")
        if attempts >= Config.MAX_ALERT_RETRIES:
            db.mark_immediate_done(
                window_id, candidate.path, False,
                meta.event_code, "whatsapp_failed",
            )
            stats.inc("immediate_failed")
        else:
            logger.debug(
                "Janela %s imediato aguardando retry %d/%d",
                window_id, attempts, Config.MAX_ALERT_RETRIES,
            )
    finally:
        cleanup_attempt_temp(
            collect_temp_paths(candidate, None, [])
            + ([temp_path] if temp_path else [])
        )


def worker_enhanced(
    db: Database, window_id: int, stats: CycleStats
) -> None:
    meta = db.get_window_meta(window_id)
    if meta is None:
        db.mark_enhanced_done(
            window_id, None, "no_meta", False, "no_meta"
        )
        stats.inc("enhanced_failed")
        return
    db.mark_enhanced_started(window_id)
    files = db.get_normal_files(window_id)
    if not files:
        db.mark_enhanced_done(
            window_id, None, meta.event_code, False, "no_files"
        )
        stats.inc("enhanced_failed")
        return
    winner, scored_candidates, frame_sets = select_best_image(
        files, window_id, "enhanced", meta.window_start,
    )
    motion_video: Optional[Path] = None
    motion_frames = 0
    send_path: Optional[Path] = None
    temp_path: Optional[Path] = None
    if scored_candidates:
        built = build_motion_video(
            scored_candidates, window_id, frame_sets
        )
        if built:
            motion_video, motion_frames = built
            db.set_motion_frames(window_id, motion_frames)
    winner_path = winner.path if winner is not None else None
    try:
        if winner is None and motion_video is None:
            attempts = db.increment_attempt(window_id, "enhanced_attempts")
            if attempts >= Config.MAX_ALERT_RETRIES:
                db.mark_enhanced_done(
                    window_id, None, meta.event_code, False, "no_candidate"
                )
                stats.inc("enhanced_failed")
                return
        champion = db.get_champion(window_id)
        should_send, reason = compare_candidates(winner, champion)
        if motion_video is not None:
            # O resumo aprimorado é obrigatório quando há vídeo de movimento,
            # mesmo que a melhor imagem não supere o campeão do instantâneo.
            send_reason = "video_movimento"
            enhanced_media = motion_video
            media_note = "🎥 vídeo de movimento"
        elif should_send:
            send_reason = reason
            enhanced_media = winner.path if winner else None
            media_note = "📷 melhor imagem (melhoria)"
            send_path, temp_path = prepare_photo_for_send(winner)
            if send_path is not None:
                enhanced_media = send_path
        else:
            logger.info(
                "janela=%d: sem vídeo e sem melhoria (reason=%s), ignorando envio",
                window_id, reason,
            )
            db.mark_enhanced_done(
                window_id, winner_path, meta.event_code,
                False, "ignored_no_improvement",
            )
            stats.inc("enhanced_skipped")
            return
        start, end = window_message_prefix(meta)
        photo_count = sum(is_photo(p) for p in files)
        video_count = sum(is_video(p) for p in files)
        old_person = champion.person_count if champion else 0
        old_animal = champion.animal_count if champion else 0
        old_score = champion.engine_score if champion else None
        new_person = winner.person_count if winner else 0
        new_animal = winner.animal_count if winner else 0
        new_score = winner.engine_score if winner else None
        message = (
            f"🚨 *Resumo* · ID #{window_id}\n"
            f"{start}-{end} · {len(files)} arq "
            f"(📷{photo_count} 📹{video_count})\n"
            f"👤{old_person}→{new_person} "
            f"🐾{old_animal}→{new_animal} · "
            f"score {score_fmt(old_score)}→{score_fmt(new_score)}\n"
            f"{media_note} · {Config.CAMERA_LINK}"
        )
        if send_whatsapp(message, enhanced_media):
            db.mark_enhanced_done(
                window_id, winner_path, meta.event_code, True, None
            )
            if winner is not None:
                db.update_champion(window_id, winner, "enhanced")
            stats.inc("enhanced_sent")
            log_decision(
                "aprimorado", window_id, "enviado", send_reason,
                meta.event_code, winner, champion,
            )
            return
        attempts = db.increment_attempt(window_id, "enhanced_attempts")
        if attempts >= Config.MAX_ALERT_RETRIES:
            db.mark_enhanced_done(
                window_id, winner_path, meta.event_code,
                False, "whatsapp_failed",
            )
            stats.inc("enhanced_failed")
        else:
            logger.debug(
                "Janela %s aprimorado aguardando retry %d/%d",
                window_id, attempts, Config.MAX_ALERT_RETRIES,
            )
    finally:
        cleanup_attempt_temp(
            collect_temp_paths(winner, motion_video, frame_sets)
            + ([temp_path] if temp_path else [])
        )


def worker_late(
    db: Database, window_id: int, stats: CycleStats
) -> None:
    meta = db.get_window_meta(window_id)
    if meta is None:
        stats.inc("late_failed")
        return
    late_files = db.get_late_files(window_id)
    if not late_files:
        return
    rebuild_videos: list[Path] = []
    mode = "late"
    if Config.LATE_REBUILD_MOTION:
        found = db.get_late_rebuild_videos(window_id)
        if found and meta.late_motion_sent == 0:
            rebuild_videos = found[: max(1, int(Config.LATE_REBUILD_MAX_VIDEOS))]
            mode = "late_rebuild"
    candidate, scored, frame_sets = select_best_image(
        late_files, window_id, mode, meta.window_start,
        rebuild_videos=rebuild_videos,
    )
    motion_video: Optional[Path] = None
    motion_frames = 0
    send_path: Optional[Path] = None
    temp_path: Optional[Path] = None
    if mode == "late_rebuild" and scored:
        built = build_motion_video(scored, window_id, frame_sets)
        if built:
            motion_video, motion_frames = built
    try:
        if candidate is None and motion_video is None:
            attempts = db.increment_attempt(window_id, "late_attempts")
            if attempts >= Config.MAX_ALERT_RETRIES:
                db.mark_late_files_evaluated(window_id, late_files)
                stats.inc("late_failed")
            return
        champion = db.get_champion(window_id)
        should_send, reason = compare_candidates(candidate, champion)
        send_video = (
            motion_video is not None
            and meta.late_motion_sent == 0
            and motion_frames > meta.motion_frames
        )
        if send_video:
            send_reason = "video_reconstruido_sucessor"
            media = motion_video
            media_note = (
                f"🎥 vídeo de movimento atualizado "
                f"({motion_frames} frames)"
            )
        elif should_send:
            send_reason = reason
            media = candidate.path if candidate is not None else None
            media_note = "📷 melhor imagem (atualização)"
            send_path, temp_path = prepare_photo_for_send(candidate)
            if send_path is not None:
                media = send_path
        else:
            db.mark_late_files_evaluated(window_id, late_files)
            if candidate is not None and (
                champion is None or candidate.rank_key > champion.rank_key
            ):
                db.update_champion(window_id, candidate, "late_silent")
            stats.inc("late_skipped")
            log_decision(
                "tardio", window_id, "ignorado", reason,
                meta.event_code, candidate, champion,
            )
            return
        old_people = champion.person_count if champion else 0
        old_animals = champion.animal_count if champion else 0
        old_score = champion.engine_score if champion else None
        new_person = (
            candidate.person_count if candidate is not None else old_people
        )
        new_animal = (
            candidate.animal_count if candidate is not None else old_animals
        )
        new_score = (
            candidate.engine_score if candidate is not None else None
        )
        start, end = window_message_prefix(meta)
        message = (
            f"🔁 Atualização · ID #{window_id}\n"
            f"{start}-{end} · "
            f"👤{old_people}→{new_person} "
            f"🐾{old_animals}→{new_animal} · "
            f"score {score_fmt(old_score)}→{score_fmt(new_score)}\n"
            f"{media_note} · {Config.CAMERA_LINK}"
        )
        if send_whatsapp(message, media):
            if candidate is not None and should_send:
                db.update_champion(
                    window_id, candidate,
                    "late_rebuild" if send_video else "late",
                )
            if send_video:
                db.mark_late_motion_sent(window_id, motion_frames)
                logger.info(
                    "janela=%d tardio video_reconstruido frames=%d "
                    "frames_anteriores=%d arquivo=%s",
                    window_id, motion_frames, meta.motion_frames,
                    media.name if media else "?",
                )
            db.mark_late_files_evaluated(window_id, late_files)
            stats.inc("late_sent")
            log_decision(
                "tardio", window_id, "enviado", send_reason,
                meta.event_code, candidate, champion,
            )
            return
        attempts = db.increment_attempt(window_id, "late_attempts")
        if attempts >= Config.MAX_ALERT_RETRIES:
            db.mark_late_files_evaluated(window_id, late_files)
            stats.inc("late_failed")
        else:
            logger.debug(
                "Janela %s tardio aguardando retry %d/%d",
                window_id, attempts, Config.MAX_ALERT_RETRIES,
            )
    finally:
        cleanup_attempt_temp(
            collect_temp_paths(candidate, motion_video, frame_sets)
            + ([temp_path] if temp_path else [])
        )


def clean_temp_files() -> None:
    if not Config.TMP_DIR.exists():
        return
    now = time.time()
    for item in Config.TMP_DIR.iterdir():
        try:
            if item.is_dir() and item.name.startswith(
                ("single_", "first_", "all_", "motion_")
            ):
                if now - item.stat().st_mtime > Config.TMP_FILE_TTL:
                    shutil.rmtree(item, ignore_errors=True)
            elif item.is_file():
                if now - item.stat().st_mtime > Config.TMP_FILE_TTL:
                    item.unlink(missing_ok=True)
        except OSError:
            logger.debug("Falha ao limpar temporário: %s", item)


BG_LOCK = threading.Lock()
ENHANCED_IN_PROGRESS: set[int] = set()
LATE_IN_PROGRESS: set[int] = set()
ENHANCED_EXECUTOR = ThreadPoolExecutor(
    max_workers=Config.WORKERS_ENHANCED, thread_name_prefix="EnhancedBG"
)
LATE_EXECUTOR = ThreadPoolExecutor(
    max_workers=Config.WORKERS_LATE, thread_name_prefix="LateBG"
)


def background_active_count() -> int:
    with BG_LOCK:
        return len(ENHANCED_IN_PROGRESS) + len(LATE_IN_PROGRESS)


def _bg_enhanced(window_id: int) -> None:
    db: Optional[Database] = None
    try:
        db = Database()
        stats = CycleStats()
        worker_enhanced(db, window_id, stats)
    except Exception as exc:
        logger.error(
            "Erro no aprimorado background janela %s: %s",
            window_id, exc, exc_info=True,
        )
    finally:
        if db is not None:
            db.close()
        with BG_LOCK:
            ENHANCED_IN_PROGRESS.discard(window_id)


def _bg_late(window_id: int) -> None:
    db: Optional[Database] = None
    try:
        db = Database()
        stats = CycleStats()
        worker_late(db, window_id, stats)
    except Exception as exc:
        logger.error(
            "Erro no tardio background janela %s: %s",
            window_id, exc, exc_info=True,
        )
    finally:
        if db is not None:
            db.close()
        with BG_LOCK:
            LATE_IN_PROGRESS.discard(window_id)


def submit_background_work(db: Database) -> tuple[int, int]:
    with BG_LOCK:
        if db.pending_immediate():
            return 0, 0
        submitted_enhanced = 0
        submitted_late = 0
        for window_id in db.pending_enhanced():
            if window_id not in ENHANCED_IN_PROGRESS:
                ENHANCED_IN_PROGRESS.add(window_id)
                ENHANCED_EXECUTOR.submit(_bg_enhanced, window_id)
                submitted_enhanced += 1
        if (
            submitted_enhanced == 0
            and len(ENHANCED_IN_PROGRESS) == 0
            and not db.pending_enhanced()
        ):
            for window_id in db.pending_late():
                if window_id not in LATE_IN_PROGRESS:
                    LATE_IN_PROGRESS.add(window_id)
                    LATE_EXECUTOR.submit(_bg_late, window_id)
                    submitted_late += 1
        return submitted_enhanced, submitted_late


def process_scan_and_immediate(force_log: bool = False) -> bool:
    scanned = scan_files()
    db = Database()
    try:
        known_files = db.known_file_paths()
        now = int(time.time())
        new_files = 0
        for path, span in scanned:
            rel = safe_rel_path(path) or str(path)
            if rel in known_files:
                continue
            db.allocate_file(
                path=path,
                file_ts=int(span.start.timestamp()),
                file_end_ts=int(span.end.timestamp()),
                video=is_video(path),
                discovered_at=now,
                source_id=span.rec_key,
            )
            new_files += 1
        db.close_ready_windows()
        immediate_pending = db.pending_immediate()
        stats = CycleStats()
        stats.set("windows", db.count_windows())
        stats.set("immediate_pending", len(immediate_pending))
        for window_id in immediate_pending:
            try:
                worker_immediate(db, window_id, stats)
            except Exception as exc:
                logger.error(
                    "Erro no imediato janela %s: %s",
                    window_id, exc, exc_info=True,
                )
        submitted_enhanced, submitted_late = submit_background_work(db)
        db.cleanup_old_records()
        active_bg = background_active_count()
        had_work = bool(
            new_files or immediate_pending
            or submitted_enhanced or submitted_late or active_bg
        )
        log_fn = logger.info if (had_work or force_log) else logger.debug
        log_fn(
            "Ciclo principal: janelas=%d novos_arquivos=%d "
            "imediato(p=%d,ok=%d,fail=%d) "
            "aprimorado_bg_submetido=%d tardio_bg_submetido=%d bg_ativo=%d",
            stats.windows, new_files, stats.immediate_pending,
            stats.immediate_sent, stats.immediate_failed,
            submitted_enhanced, submitted_late, active_bg,
        )
        return had_work
    finally:
        db.close()
        clean_temp_files()


def run_until_idle() -> None:
    last_activity = time.monotonic()
    first_iteration = True
    while True:
        had_work = process_scan_and_immediate(force_log=first_iteration)
        first_iteration = False
        active_bg = background_active_count()
        if had_work or active_bg > 0:
            last_activity = time.monotonic()
        elif time.monotonic() - last_activity >= Config.LOOP_IDLE_TIMEOUT:
            logger.debug(
                "Sem atividade por %ds, encerrando para poupar recursos",
                Config.LOOP_IDLE_TIMEOUT,
            )
            ENHANCED_EXECUTOR.shutdown(wait=False)
            LATE_EXECUTOR.shutdown(wait=False)
            return
        time.sleep(Config.LOOP_POLL_INTERVAL)


def main() -> None:
    Config.BASE_DIR.mkdir(parents=True, exist_ok=True)
    Config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    fix_permissions(Config.TMP_DIR, is_dir=True)
    try:
        with FileLock(Config.LOCK_FILE):
            run_until_idle()
    except RuntimeError:
        logger.debug("Outra instância já está processando; rodada ignorada")
        sys.exit(0)
    except KeyboardInterrupt:
        logger.info("Encerrando por comando do usuário")
        sys.exit(0)
    except Exception as exc:
        logger.error("Erro fatal: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()