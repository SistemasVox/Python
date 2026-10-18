from __future__ import annotations

import errno
import fcntl
import hashlib
import heapq
import importlib
import json
import logging
import math
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import lru_cache
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

# alert_motion.py também é iniciado pelo downloader usando o Python dele, que
# pode não ter OpenCV. Reutilize o runtime do projeto (mesmo Python minor ABI)
# para não transformar silenciosamente toda saliência em zero.
CV2_RUNTIME_FALLBACK: Optional[Path] = None
CV2_RUNTIME_ERROR: Optional[str] = None
if cv2 is None:
    bundled_site_packages = (
        PROJECT_ROOT / ".venv-cam" / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    if bundled_site_packages.is_dir():
        sys.path.append(str(bundled_site_packages))
        try:
            cv2 = importlib.import_module("cv2")
            CV2_RUNTIME_FALLBACK = bundled_site_packages
        except Exception as exc:
            CV2_RUNTIME_ERROR = f"{type(exc).__name__}: {exc}"


class Config:
    BASE_DIR: Path = (PROJECT_ROOT / "gravacoes").resolve()
    WHATSAPP_NUMBERS: tuple[str, ...] = (
        "553491509513",
        "553496668954",
    )
    CAMERA_LINK: str = "https://cam.svox.com.br/"
    WHATSAPP_SCRIPT: Path = Path(
        "/var/www/opt/scripts/msg/send_whatsapp_media.py"
    ).expanduser().resolve()
    EXTRACT_FRAMES_SCRIPT: Path = (
        PROJECT_ROOT / "processamento_img" / "extract_frames.py"
    ).resolve()
    MELHOR_FOTO_SCRIPT: Path = (
        PROJECT_ROOT / "processamento_img" / "melhor_foto.py"
    ).resolve()
    # Ambiente que contém torch/ultralytics e o modelo YOLO efetivamente
    # utilizado pelo alerta. Nunca deixe a execução cair no Python do sistema,
    # pois ele pode degradar a análise para HOG sem capacidade equivalente.
    MELHOR_FOTO_PYTHON: Path = (
        PROJECT_ROOT / ".venv-cam" / "bin" / "python"
    )
    FFMPEG_CMD: str = "/usr/bin/ffmpeg"
    FFPROBE_CMD: str = "/usr/bin/ffprobe"
    FRAME_OUTPUT_IDLE_TIMEOUT: float = 3.0
    MELHOR_FOTO_ENGINE: str = "yolo"
    MELHOR_FOTO_LOGS: bool = False
    MAX_ALERT_RETRIES: int = 3
    WHATSAPP_TIMEOUT: int = 60
    WHATSAPP_VIDEO_TIMEOUT: int = 180
    FRAME_EXTRACT_TIMEOUT: int = 60
    FRAME_EXTRACT_TIMEOUT_FAST: int = 15
    EXTRACT_ALL_TIMEOUT: int = 600
    MELHOR_FOTO_ENHANCED_TIMEOUT: int = 300
    MELHOR_FOTO_ENHANCED_TIMEOUT_MAX: int = 3600
    MELHOR_FOTO_ENHANCED_SECONDS_PER_CANDIDATE: float = 3.5
    MELHOR_FOTO_LATE_TIMEOUT: int = 300
    MELHOR_FOTO_LATE_REBUILD_TIMEOUT: int = 600
    # O filho YOLO importa PyTorch/Ultralytics e carrega o modelo em cada
    # execução. Os logs mostraram análises válidas chegando a ~58 s, então 60 s
    # causava timeouts falsos. Mantemos um teto finito, configurável, para não
    # deixar a fila imediata presa indefinidamente.
    MELHOR_FOTO_IMMEDIATE_TIMEOUT: int = max(
        60, min(180, int(os.getenv("MELHOR_FOTO_IMMEDIATE_TIMEOUT", "90")))
    )
    # Zero significa avaliar todas as fotos disponíveis no aviso instantâneo.
    IMMEDIATE_MAX_PHOTOS: int = 0
    # Só precisamos de alguns instantes distintos no aviso urgente; o passe
    # completo e adaptativo fica para o aprimorado.
    IMMEDIATE_VIDEO_FRAMES: int = 3
    IMMEDIATE_FULL_SAMPLE_MAX_SECONDS: int = 30
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
    # Fotos complementares com baixa definição geram ruído no aviso. O valor
    # foi calibrado com as complementares deste dia: menor enviada útil ~977;
    # a foto ruim da janela 66 mediu 339.
    ENHANCED_SUPPLEMENTAL_MIN_SHARPNESS: float = float(
        os.getenv("ENHANCED_SUPPLEMENTAL_MIN_SHARPNESS", "700")
    )
    ENHANCED_SUPPLEMENTAL_TIME_TOLERANCE: float = float(
        os.getenv("ENHANCED_SUPPLEMENTAL_TIME_TOLERANCE", "3")
    )
    ENHANCED_ANALYSIS_MAX_FRAMES: int = 40
    ENHANCED_ADAPTIVE_EXTRA_FRAMES: int = 8
    # A análise percorre o vídeo inteiro com resolução temporal econômica.
    # Depois de localizar o intervalo vencedor, somente ele é reextraído na
    # cadência do vídeo final.
    ANALYSIS_FPS: int = int(os.getenv("ANALYSIS_FPS", "1"))
    ANALYSIS_HIGH_FPS: int = int(os.getenv("ANALYSIS_HIGH_FPS", "15"))
    ANALYSIS_HIGH_FPS_MAX_SECONDS: int = int(
        os.getenv("ANALYSIS_HIGH_FPS_MAX_SECONDS", "6")
    )
    # Entre clipes muito curtos e gravações longas, 5 fps preservam resolução
    # temporal de 200 ms com um terço dos quadros do antigo passe de 15 fps.
    ANALYSIS_MEDIUM_FPS: int = int(os.getenv("ANALYSIS_MEDIUM_FPS", "5"))
    ANALYSIS_MEDIUM_FPS_MAX_SECONDS: int = int(
        os.getenv("ANALYSIS_MEDIUM_FPS_MAX_SECONDS", "30")
    )
    # Antes do YOLO, a amostra mantém cobertura temporal, mas privilegia o
    # pico de saliência de cada trecho. Em cena estática, volta à nitidez.
    YOLO_SALIENCE_MIN: float = float(
        os.getenv("YOLO_SALIENCE_MIN", "0.015")
    )
    YOLO_SALIENCE_WEIGHT: float = float(
        os.getenv("YOLO_SALIENCE_WEIGHT", "0.75")
    )
    LATE_MAX_PHOTOS: int = 3
    LATE_MAX_VIDEOS: int = 1
    LATE_FRAMES_PER_VIDEO: int = 2
    LATE_REBUILD_MOTION: bool = True
    LATE_REBUILD_MAX_VIDEOS: int = 1
    DB_PATH: Path = BASE_DIR / ".alert_cache.db"
    LOG_FILE: Path = BASE_DIR / "alert_motion.log"
    TMP_DIR: Path = BASE_DIR / ".tmp_frames"
    # Mídia já pronta fica aqui enquanto uma tentativa de transporte falha.
    # Assim, retry de WhatsApp não repete ffmpeg, saliência nem YOLO.
    RETRY_DIR: Path = BASE_DIR / ".retry_media"
    LOCK_FILE: Path = BASE_DIR / ".alert_motion.lock"
    WORKERS_ENHANCED: int = 1
    WORKERS_LATE: int = 1
    ENGINE_CONCURRENCY: int = 1
    # Intervalo de resposta da preempção cooperativa dos trabalhos de baixa
    # prioridade. Não há suspensão insegura de threads; o subprocesso atual
    # é encerrado e a janela volta à fila ainda pendente.
    BACKGROUND_PREEMPT_POLL: float = float(
        os.getenv("BACKGROUND_PREEMPT_POLL", "0.25")
    )
    TMP_FILE_TTL: int = 900
    RETENTION_DAYS: int = 30
    MIN_IMAGE_BYTES: int = 1024
    MIN_VIDEO_BYTES: int = 1024
    ENHANCED_MOTION_ENABLED: bool = True
    # Cadência exclusiva do vídeo final, extraída apenas no intervalo eleito.
    MOTION_FRAME_COUNT: int = 45
    MOTION_MIN_SECONDS: int = 5
    # Blocos contíguos de 5 s: contexto útil até 15 s. Uma chegada de veículo
    # confirmada pode receber pré-roll parcial com saliência quando os blocos
    # vizinhos também são relevantes. Saliência sozinha não cria um corte; o
    # quarto bloco até 20 s exige pessoas recorrentes em grupo e movimento.
    MOTION_BASE_MAX_SECONDS: int = 15
    MOTION_MAX_SECONDS: int = 20
    MOTION_BLOCK_SECONDS: float = 5.0
    MOTION_BLOCK_MIN_COVERAGE: float = 0.60
    MOTION_BLOCK_LOCAL_FLOOR: float = 0.003
    MOTION_BLOCK_MIN_MOVEMENT: float = 0.20
    MOTION_BLOCK_CROWD_COUNT: int = 4
    # Evidence is local to each analysed instant; never bridge long YOLO gaps.
    MOTION_EVIDENCE_MAX_DISTANCE: float = 1.25
    MOTION_VIDEO_MAX_BYTES: int = 15 * 1024 * 1024
    MOTION_EXTENSION_MIN_QUALITY: float = 0.65
    MOTION_FPS: int = int(os.getenv("MOTION_FPS", "5"))
    MOTION_VIDEO_WIDTH: int = 1280
    MOTION_DEDUP_THRESHOLD: float = 8.0
    MOTION_SALIENCE_THRESHOLD: float = 15.0
    MOTION_SALIENCE_FLOOR: float = 0.01
    MOTION_ANCHOR_SMOOTH: int = 3
    MOTION_POSITION_PENALTY: float = 0.50
    # A saliência é a fração de pixels que diferem do fundo temporal. Em
    # câmeras abertas, uma pessoa ocupa poucos pixels: 1,5% já é evidência
    # útil. Só se preserva o quadro completo quando as duas câmeras têm
    # atividade relevante e de intensidade parecida.
    MOTION_HALF_MIN_SALIENCE: float = 0.015
    MOTION_HALF_DOMINANCE_RATIO: float = 1.50
    # Em modo full, o instante do melhor âncora legível é o início do corte.
    # Não aplicar pré-roll: nos testes isso afastava o trecho da aproximação
    # mais nítida e deixava o âncora na borda do vídeo enviado.
    MOTION_FULL_ANCHOR_LEAD_SECONDS: float = 0.0
    # Em vez de esperar até o pico absoluto de nitidez (que pode acontecer
    # tarde demais), iniciar no primeiro quadro full que já esteja próximo do
    # melhor nível de legibilidade humana encontrado na janela.
    MOTION_FULL_ANCHOR_MIN_QUALITY: float = float(
        os.getenv("MOTION_FULL_ANCHOR_MIN_QUALITY", "0.70")
    )
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
Config.RETRY_DIR.mkdir(parents=True, exist_ok=True)


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


logger = logging.getLogger("alert_motion")
logger.addHandler(logging.NullHandler())
logger.propagate = False


def configure_logging(log_file: Optional[Path] = None) -> None:
    """Configure the operational log only when the worker actually starts.

    Importing this module from tests or helper tools must never open the live
    camera log. Tests may pass a temporary path to exercise the setup safely.
    """
    target = log_file or Config.LOG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [Thread:%(threadName)s] %(message)s"
    )
    file_handler = RotatingFileHandler(
        target,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    if cv2 is None:
        logger.warning(
            "OpenCV indisponível; saliência visual ficará indisponível%s",
            f" ({CV2_RUNTIME_ERROR})" if CV2_RUNTIME_ERROR else "",
        )
    elif CV2_RUNTIME_FALLBACK is not None:
        logger.info(
            "OpenCV carregado do runtime do projeto: %s versão=%s",
            CV2_RUNTIME_FALLBACK, getattr(cv2, "__version__", "desconhecida"),
        )
    logger.setLevel(logging.INFO)


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
IMMEDIATE_PRESSURE = threading.Event()
ENHANCED_PRESSURE = threading.Event()


class BackgroundPreempted(RuntimeError):
    """Trabalho em segundo plano cedeu CPU/RAM a uma fila prioritária."""


class BackgroundYOLOTimeout(BackgroundPreempted):
    """Timeout de inferência que preserva checkpoint e consome uma tentativa."""


def background_preempt_reason(phase: str) -> Optional[str]:
    """Retorna a prioridade que deve interromper uma etapa de fundo."""
    if phase == "immediate":
        return None
    if IMMEDIATE_PRESSURE.is_set():
        return "imediato"
    if phase.startswith("late") and ENHANCED_PRESSURE.is_set():
        return "aprimorado"
    return None


def raise_if_background_preempted(phase: str, stage: str) -> None:
    reason = background_preempt_reason(phase)
    if reason is not None:
        raise BackgroundPreempted(
            f"fase={phase} etapa={stage} prioridade={reason}"
        )


def _output_growth_signature(directory: Path) -> tuple[tuple[str, int, int], ...]:
    """Snapshots generated JPEG names, sizes, and mtimes for idle detection."""
    signature: list[tuple[str, int, int]] = []
    try:
        paths = directory.glob("frame_*.jpg")
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > 0:
                signature.append((path.name, stat.st_size, stat.st_mtime_ns))
    except OSError:
        return ()
    return tuple(sorted(signature))


def _terminate_subprocess_group(
    process: subprocess.Popen[Any], grace_seconds: float = 5.0
) -> tuple[Any, Any]:
    """Stop the command and descendants, including ffmpeg child processes."""
    def group_exists() -> bool:
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return False

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + max(0.0, grace_seconds)
    output: Any = None
    errors: Any = None
    try:
        output, errors = process.communicate(
            timeout=max(0.0, deadline - time.monotonic())
        )
    except subprocess.TimeoutExpired as exc:
        output, errors = exc.output, exc.stderr

    # The Python wrapper may exit on SIGTERM while ffmpeg ignores it. In that
    # case communicate() can finish even though descendants still occupy the
    # isolated process group, so check the group explicitly before returning.
    while group_exists() and time.monotonic() < deadline:
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    if group_exists():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if process.poll() is None:
                process.kill()
    try:
        tail_output, tail_errors = process.communicate(timeout=1.0)
        output = tail_output if tail_output is not None else output
        errors = tail_errors if tail_errors is not None else errors
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        tail_output, tail_errors = process.communicate()
        output = tail_output if tail_output is not None else output
        errors = tail_errors if tail_errors is not None else errors
    return output, errors


def run_preemptible_subprocess(
    command: Sequence[str],
    *,
    timeout: float,
    phase: str,
    capture_output: bool = False,
    text: bool = False,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
    env: Optional[dict[str, str]] = None,
    idle_output_dir: Optional[Path] = None,
    idle_timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[Any]:
    """Executa subprocesso cancelável para liberar recursos ao imediato.

    A preempção é limitada às fases em segundo plano. A mídia do WhatsApp não
    é interrompida, pois cancelar uma transmissão poderia produzir duplicata.
    """
    # A assinatura aceita capture_output para espelhar subprocess.run; os
    # pipes já são o padrão desta rotina para devolver CompletedProcess.
    del capture_output
    raise_if_background_preempted(phase, "antes_do_subprocesso")
    process = subprocess.Popen(
        list(command), stdout=stdout, stderr=stderr, text=text, env=env,
        start_new_session=True,
    )
    started = time.monotonic()
    poll = max(0.05, Config.BACKGROUND_PREEMPT_POLL)
    last_output_signature: tuple[tuple[str, int, int], ...] = ()
    last_output_growth: Optional[float] = None
    process_tree_stopped = False
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                output, errors = process.communicate(
                    timeout=min(poll, remaining)
                )
                process_tree_stopped = True
                return subprocess.CompletedProcess(
                    list(command), process.returncode, output, errors
                )
            except subprocess.TimeoutExpired:
                reason = background_preempt_reason(phase)
                if reason is None:
                    if idle_output_dir is not None and idle_timeout is not None:
                        signature = _output_growth_signature(idle_output_dir)
                        now = time.monotonic()
                        if signature != last_output_signature:
                            last_output_signature = signature
                            last_output_growth = now if signature else None
                        elif (
                            signature
                            and last_output_growth is not None
                            and now - last_output_growth >= idle_timeout
                        ):
                            logger.info(
                                "subprocesso encerrado por inatividade de saída "
                                "fase=%s pid=%s inativo=%.1fs arquivos=%d",
                                phase, process.pid, idle_timeout, len(signature),
                            )
                            output, errors = _terminate_subprocess_group(process)
                            process_tree_stopped = True
                            return subprocess.CompletedProcess(
                                list(command), process.returncode, output, errors
                            )
                    continue
                logger.info(
                    "preempcao fase=%s prioridade=%s pid=%s",
                    phase, reason, process.pid,
                )
                raise BackgroundPreempted(
                    f"fase={phase} subprocesso prioridade={reason}"
                )
    finally:
        if not process_tree_stopped:
            _terminate_subprocess_group(process)


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
    upper_animal_count: int = 0
    lower_animal_count: int = 0
    person_sharpness: float = 0.0
    frame_sharpness: float = 0.0
    upper_vehicle_count: int = 0
    lower_vehicle_count: int = 0
    cat_rescue: bool = False
    clear_person_count: int = 0
    upper_clear_person_count: int = 0
    lower_clear_person_count: int = 0
    clear_animal_count: int = 0
    upper_clear_animal_count: int = 0
    lower_clear_animal_count: int = 0
    person_visible_area: float = 0.0
    visibility_measured: bool = False

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
        elif self.category == "vehicle":
            group = 0
            count = self.object_count
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
    analysis_fps: int = 1
    # Local source-time (seconds) for each frame. Usually idx/fps; sparse
    # recovery samples need explicit timestamps to preserve clip coverage.
    frame_times: tuple[float, ...] = ()
    coverage_complete: bool = True
    # Cache derived only from these in-memory frames. It is intentionally not
    # checkpointed: after a restart it is cheap and safe to rebuild once.
    salience_cache: Optional[dict[str, list[float]]] = field(
        default=None, repr=False, compare=False
    )


@dataclass(slots=True)
class MotionBuild:
    path: Path
    frame_count: int
    camera_half: str
    reason: str
    evidence: CandidateImage
    cut_candidates: tuple[CandidateImage, ...] = ()
    cut_intervals: tuple[tuple[datetime, datetime], ...] = ()


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
class BackgroundAnalysisCheckpoint:
    """Checkpoint for decoded work when a background task is preempted."""

    signature: tuple[tuple[str, int, int, int, str], ...]
    candidates: list[CandidateImage]
    frame_sets: list[MotionFrameSet]
    adaptive_fallback_count: int = 0
    analysis_complete: bool = False


BACKGROUND_CHECKPOINT_LOCK = threading.RLock()
BACKGROUND_ANALYSIS_CHECKPOINTS: dict[
    tuple[int, str], BackgroundAnalysisCheckpoint
] = {}


def _checkpoint_manifest_path(window_id: int, mode: str) -> Optional[Path]:
    if mode not in {"enhanced", "late", "late_rebuild"}:
        return None
    return Config.TMP_DIR / "background_checkpoints" / f"{int(window_id)}_{mode}.json"


def _yolo_checkpoint_path(window_id: int, mode: str) -> Optional[Path]:
    if mode not in {"enhanced", "late", "late_rebuild"}:
        return None
    return Config.TMP_DIR / "background_checkpoints" / f"yolo_{int(window_id)}_{mode}.json"


def _candidate_to_checkpoint(candidate: CandidateImage) -> dict[str, Any]:
    return {
        name: str(value) if isinstance(value, Path) else list(value)
        if isinstance(value, tuple) else value
        for name, value in (
            (field_name, getattr(candidate, field_name))
            for field_name in CandidateImage.__dataclass_fields__
        )
    }


def _candidate_from_checkpoint(record: object) -> Optional[CandidateImage]:
    if not isinstance(record, dict):
        return None
    try:
        values = dict(record)
        values["path"] = Path(values["path"])
        if values.get("origin_path"):
            values["origin_path"] = Path(values["origin_path"])
        if "detected" in values:
            values["detected"] = tuple(values["detected"] or ())
        return CandidateImage(**values)
    except (KeyError, TypeError, ValueError):
        return None


def _write_checkpoint_manifest(
    window_id: int, mode: str, checkpoint: BackgroundAnalysisCheckpoint,
) -> None:
    manifest = _checkpoint_manifest_path(window_id, mode)
    if manifest is None:
        return
    payload = {
        "version": 2,
        "signature": [list(item) for item in checkpoint.signature],
        "candidates": [
            _candidate_to_checkpoint(candidate)
            for candidate in checkpoint.candidates
        ],
        "frame_sets": [
            {
                "video_path": str(group.video_path),
                "frames": [str(frame) for frame in group.frames],
                "analysis_fps": group.analysis_fps,
                "frame_times": list(group.frame_times),
                "coverage_complete": group.coverage_complete,
            }
            for group in checkpoint.frame_sets
        ],
        "adaptive_fallback_count": checkpoint.adaptive_fallback_count,
        "analysis_complete": checkpoint.analysis_complete,
    }
    try:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        fix_permissions(manifest.parent, is_dir=True)
        temporary = manifest.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        fix_permissions(temporary)
        os.replace(temporary, manifest)
        fix_permissions(manifest)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning(
            "janela=%s checkpoint_disco gravação_falhou fase=%s erro=%s",
            window_id, mode, exc,
        )


def _read_checkpoint_manifest(
    window_id: int, mode: str,
) -> Optional[BackgroundAnalysisCheckpoint]:
    manifest = _checkpoint_manifest_path(window_id, mode)
    if manifest is None:
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 2:
            return None
        signature = tuple(
            (str(item[0]), int(item[1]), int(item[2]), int(item[3]), str(item[4]))
            for item in payload.get("signature", [])
            if isinstance(item, list) and len(item) == 5
        )
        candidates = [
            candidate for record in payload.get("candidates", [])
            if (candidate := _candidate_from_checkpoint(record)) is not None
        ]
        frame_sets: list[MotionFrameSet] = []
        for record in payload.get("frame_sets", []):
            if not isinstance(record, dict):
                continue
            frame_sets.append(MotionFrameSet(
                video_path=Path(record["video_path"]),
                frames=tuple(Path(item) for item in record.get("frames", [])),
                analysis_fps=max(1, int(record.get("analysis_fps", 1))),
                frame_times=tuple(float(item) for item in record.get("frame_times", [])),
                coverage_complete=bool(record.get("coverage_complete", True)),
            ))
        if not candidates and not frame_sets:
            return None
        return BackgroundAnalysisCheckpoint(
            signature=signature,
            candidates=candidates,
            frame_sets=frame_sets,
            adaptive_fallback_count=max(
                0, int(payload.get("adaptive_fallback_count", 0))
            ),
            analysis_complete=bool(payload.get("analysis_complete", False)),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _checkpoint_artifacts_exist(
    checkpoint: BackgroundAnalysisCheckpoint,
) -> bool:
    paths = [candidate.path for candidate in checkpoint.candidates]
    paths.extend(
        frame for group in checkpoint.frame_sets for frame in group.frames
    )
    if not paths:
        return False
    try:
        return all(path.is_file() and path.stat().st_size > 0 for path in paths)
    except OSError:
        return False


def _remove_checkpoint_manifest(window_id: int, mode: str) -> None:
    manifest = _checkpoint_manifest_path(window_id, mode)
    if manifest is None:
        return
    try:
        manifest.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "janela=%s checkpoint_disco remoção_falhou fase=%s erro=%s",
            window_id, mode, exc,
        )


def _remove_yolo_checkpoint(window_id: int, mode: str) -> None:
    checkpoint = _yolo_checkpoint_path(window_id, mode)
    if checkpoint is None:
        return
    try:
        checkpoint.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "janela=%s checkpoint_yolo remoção_falhou fase=%s erro=%s",
            window_id, mode, exc,
        )


def _analysis_input_signature(
    files: Sequence[Path], rebuild_videos: Sequence[Path] = (),
    cached_signature: Optional[tuple[tuple[str, int, int, int, str], ...]] = None,
    *, label: str = "checkpoint",
) -> tuple[tuple[str, int, int, int, str], ...]:
    """Fingerprint inputs with metadata and a streaming full-content hash.

    Reads are streamed in fixed-size chunks, so memory use stays bounded.
    When a checkpoint is already resident in this process, unchanged file
    metadata reuses its digest; after restart the full hash is recomputed.
    """
    signature: list[tuple[str, int, int, int, str]] = []
    started = time.monotonic()
    bytes_hashed = 0
    hashed_files = 0
    cached_files = 0
    cached_by_path = {
        item[0]: item for item in (cached_signature or ())
    }
    paths = dedupe_media_paths([*files, *rebuild_videos])
    for path in paths:
        try:
            resolved = path.resolve()
            stat = resolved.stat()
            metadata = (
                str(resolved), stat.st_size, stat.st_mtime_ns,
                int(getattr(stat, "st_ino", 0)),
            )
            cached = cached_by_path.get(str(resolved))
            if cached is not None and cached[:4] == metadata:
                signature.append(cached)
                cached_files += 1
                continue
            digest = hashlib.blake2b(digest_size=16)
            with resolved.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
                    bytes_hashed += len(block)
            hashed_files += 1
            after = resolved.stat()
            signature.append((
                str(resolved), after.st_size, after.st_mtime_ns,
                int(getattr(after, "st_ino", 0)), digest.hexdigest(),
            ))
        except OSError:
            signature.append((str(path), -1, -1, -1, ""))
    logger.info(
        "checkpoint_fingerprint contexto=%s arquivos=%d hasheados=%d "
        "cache=%d bytes=%d duração=%.3fs",
        label, len(signature), hashed_files, cached_files,
        bytes_hashed, time.monotonic() - started,
    )
    return tuple(sorted(signature))


def _store_background_checkpoint(
    files: Sequence[Path], window_id: int, mode: str,
    candidates: Sequence[CandidateImage],
    frame_sets: Sequence[MotionFrameSet],
    rebuild_videos: Sequence[Path] = (),
    adaptive_fallback_count: int = 0,
) -> None:
    if mode not in {"enhanced", "late", "late_rebuild"}:
        return
    key = (int(window_id), mode)
    with BACKGROUND_CHECKPOINT_LOCK:
        previous = BACKGROUND_ANALYSIS_CHECKPOINTS.get(key)
    if previous is None:
        # Sem checkpoint de preparação correspondente, nunca reaproveite
        # inferências órfãs possivelmente associadas a frames temporários antigos.
        _remove_yolo_checkpoint(window_id, mode)
    checkpoint = BackgroundAnalysisCheckpoint(
        signature=_analysis_input_signature(
            files, rebuild_videos,
            previous.signature if previous is not None else None,
            label=f"{window_id}:{mode}",
        ),
        candidates=list(candidates), frame_sets=list(frame_sets),
        adaptive_fallback_count=max(0, int(adaptive_fallback_count)),
    )
    with BACKGROUND_CHECKPOINT_LOCK:
        BACKGROUND_ANALYSIS_CHECKPOINTS[key] = checkpoint
        _write_checkpoint_manifest(window_id, mode, checkpoint)


def _load_background_checkpoint(
    files: Sequence[Path], window_id: int, mode: str,
    rebuild_videos: Sequence[Path] = (),
) -> Optional[BackgroundAnalysisCheckpoint]:
    key = (int(window_id), mode)
    with BACKGROUND_CHECKPOINT_LOCK:
        checkpoint = BACKGROUND_ANALYSIS_CHECKPOINTS.get(key)
        if checkpoint is None:
            checkpoint = _read_checkpoint_manifest(window_id, mode)
            if checkpoint is not None:
                BACKGROUND_ANALYSIS_CHECKPOINTS[key] = checkpoint
    if checkpoint is None:
        return None
    # Rehash on every actual resume. This catches same-size/same-mtime edits;
    # no fingerprinting I/O is done on the normal no-checkpoint path.
    signature = _analysis_input_signature(
        files, rebuild_videos, label=f"{window_id}:{mode}",
    )
    with BACKGROUND_CHECKPOINT_LOCK:
        if (
            checkpoint.signature != signature
            or not _checkpoint_artifacts_exist(checkpoint)
        ):
            # Late files may have extended an event. Never mix frames/scoring
            # across different versions of the same window; temporary frames
            # may also have expired while the service was stopped.
            BACKGROUND_ANALYSIS_CHECKPOINTS.pop(key, None)
            _remove_checkpoint_manifest(window_id, mode)
            _remove_yolo_checkpoint(window_id, mode)
            return None
        return checkpoint


def _clear_background_checkpoint(window_id: int, mode: str, cleanup_preparation: bool = True) -> None:
    if cleanup_preparation:
        _clear_preparation(window_id, mode)
    with BACKGROUND_CHECKPOINT_LOCK:
        BACKGROUND_ANALYSIS_CHECKPOINTS.pop((int(window_id), mode), None)
        _remove_checkpoint_manifest(window_id, mode)
        _remove_yolo_checkpoint(window_id, mode)


def _run_checkpointed_analysis(
    files: Sequence[Path], window_id: int, mode: str,
    candidates: Sequence[CandidateImage],
    frame_sets: Sequence[MotionFrameSet], timeout: int,
    priority: tuple[int, int],
    rebuild_videos: Sequence[Path] = (),
    adaptive_fallback_count: int = 0,
) -> tuple[Optional[CandidateImage], list[CandidateImage], list[MotionFrameSet]]:
    """Preserve decoded frames/candidates in RAM if YOLO yields to priority."""
    _store_background_checkpoint(
        files, window_id, mode, candidates, frame_sets, rebuild_videos,
        adaptive_fallback_count,
    )
    try:
        salience_cache = (
            {
                frame_set.video_path.resolve(): frame_set.salience_cache
                for frame_set in frame_sets
                if frame_set.salience_cache is not None
            }
            if mode == "enhanced" else None
        )
        scored, winner = run_melhor_foto(
            candidates, window_id, mode, timeout, priority,
            adaptive_fallback_count, salience_cache=salience_cache,
        )
    except BackgroundYOLOTimeout:
        logger.info(
            "janela=%s checkpoint_memoria preservado fase=%s candidatos=%d "
            "quadros=%d retomada=YOLO_timeout",
            window_id, mode, len(candidates),
            sum(len(group.frames) for group in frame_sets),
        )
        raise
    except BackgroundPreempted:
        logger.info(
            "janela=%s checkpoint_memoria preservado fase=%s candidatos=%d "
            "quadros=%d retomada=YOLO",
            window_id, mode, len(candidates),
            sum(len(group.frames) for group in frame_sets),
        )
        raise
    except Exception:
        _clear_background_checkpoint(window_id, mode)
        raise
    if scored:
        # Keep completed detections until the worker finishes delivery. A
        # higher-priority alert can still preempt cropping/encoding afterward.
        with BACKGROUND_CHECKPOINT_LOCK:
            checkpoint = BACKGROUND_ANALYSIS_CHECKPOINTS[(int(window_id), mode)]
            checkpoint.candidates = list(scored)
            checkpoint.analysis_complete = True
            _write_checkpoint_manifest(window_id, mode, checkpoint)
    else:
        _clear_background_checkpoint(window_id, mode, cleanup_preparation=False)
    return winner, scored or list(candidates), list(frame_sets)


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
    enhanced_attempts: int = 0
    enhanced_retry_media: Optional[Path] = None
    enhanced_retry_message: Optional[str] = None
    motion_frames: int = 0
    late_motion_sent: int = 0
    motion_camera_half: Optional[str] = None


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
    r"\[M\]\[(?P<id>[^\]]+)\](?:\((?P<channel>[^)]+)\))?"
    r"(?:\[(?P<camera_id>\d+)\])?",
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


def compute_sharpness(
    path: Path, camera_half: Optional[str] = None,
) -> Optional[float]:
    if cv2 is None:
        return None
    try:
        image = active_camera_image(
            cv2.imread(str(path), cv2.IMREAD_GRAYSCALE),
            camera_half or "lower",
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
    camera_id = str(match.group("camera_id") or "").strip()
    if not rec_id:
        return None
    if channel:
        rec_id = f"{rec_id}[{channel}]"
    if camera_id:
        rec_id = f"{rec_id}[{camera_id}]"
    return rec_id


@dataclass(slots=True)
class MotionSpan:
    start: datetime
    end: datetime
    rec_id: Optional[str] = None
    channel: Optional[str] = None
    rec_key: Optional[str] = None
    camera_id: Optional[str] = None


@dataclass(slots=True)
class MotionTimelinePart:
    frame_set: MotionFrameSet
    span: MotionSpan
    frames: tuple[Path, ...]
    source_offset: float
    timeline_start: float
    duration: float
    frame_times: tuple[float, ...] = ()


def extract_motion_span(
    path: Path, fallback_date: Optional[datetime] = None
) -> Optional[MotionSpan]:
    date_match = DATE_RE.search(str(path))
    if not date_match and fallback_date is None:
        return None
    motion_match = MOTION_FILE_RE.search(path.name)
    if not motion_match:
        return None
    date_str = (
        date_match.group(1)
        if date_match is not None
        else fallback_date.strftime("%Y-%m-%d")
    )
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
        camera_id=(
            str(motion_match.group("camera_id") or "").strip() or None
        ),
    )


def group_motion_frame_sets(
    frame_sets: Sequence[MotionFrameSet],
) -> list[list[tuple[MotionFrameSet, Optional[MotionSpan]]]]:
    """Join adjacent clips from the same camera into one movement timeline.

    Recording IDs identify individual files and naturally change between
    consecutive clips. The trailing numeric filename field is the camera ID;
    it must match, and the next clip must start less than five seconds after
    the current movement's end.
    """
    entries = [
        (
            frame_set,
            extract_motion_span(
                frame_set.video_path, fallback_date=datetime(2000, 1, 1)
            ),
        )
        for frame_set in frame_sets
    ]
    entries.sort(key=lambda item: (
        item[1].start if item[1] is not None else datetime.max,
        str(item[0].video_path),
    ))
    groups: list[list[tuple[MotionFrameSet, Optional[MotionSpan]]]] = []
    last_end: Optional[datetime] = None
    last_camera_id: Optional[str] = None
    for frame_set, span in entries:
        if span is None:
            groups.append([(frame_set, None)])
            last_end = None
            last_camera_id = None
            continue
        gap = (
            (span.start - last_end).total_seconds()
            if last_end is not None
            else float("inf")
        )
        can_join = bool(
            groups
            and last_end is not None
            and span.camera_id
            and span.camera_id == last_camera_id
            and gap < 5.0
        )
        if can_join:
            groups[-1].append((frame_set, span))
            last_end = max(last_end, span.end)
        else:
            groups.append([(frame_set, span)])
            last_end = span.end
            last_camera_id = span.camera_id
    return groups


def build_motion_timeline_parts(
    group: Sequence[tuple[MotionFrameSet, Optional[MotionSpan]]],
) -> list[MotionTimelinePart]:
    """Create one ordered timeline from decodable frames, trimming overlaps.

    Filename intervals describe the camera event, but damaged/truncated raw
    H.264 can end before the nominal end. Advance the next clip by valid frame
    coverage, not nominal duration, so the optimizer does not select time
    without decoded frames at a clip edge.
    """
    parts: list[MotionTimelinePart] = []
    timeline_cursor = 0.0
    previous_content_end: Optional[datetime] = None
    for frame_set, span in group:
        if span is None or not frame_set.frames:
            continue
        fps = max(1, int(frame_set.analysis_fps))
        source_duration = max(0.0, (span.end - span.start).total_seconds())
        overlap = max(
            0.0,
            (previous_content_end - span.start).total_seconds()
            if previous_content_end is not None else 0.0,
        )
        source_times = (
            frame_set.frame_times
            if len(frame_set.frame_times) == len(frame_set.frames)
            else tuple(index / fps for index in range(len(frame_set.frames)))
        )
        selected = [
            (frame, source_time)
            for frame, source_time in zip(frame_set.frames, source_times)
            if source_time + 1e-6 >= overlap
            and source_time < source_duration + 1e-6
        ]
        frames = tuple(frame for frame, _ in selected)
        local_frame_times = tuple(
            max(0.0, source_time - overlap) for _, source_time in selected
        )
        source_offset = overlap
        if not frames:
            continue
        # Do not pretend missing tail frames exist. If the next clip is part
        # of this movement, it can immediately fill the decoded timeline.
        duration = min(
            max(0.0, source_duration - overlap),
            max(local_frame_times) + 1.0 / fps,
        )
        if duration <= 0.0:
            continue
        parts.append(MotionTimelinePart(
            frame_set=frame_set,
            span=span,
            frames=frames,
            source_offset=source_offset,
            timeline_start=timeline_cursor,
            duration=duration,
            frame_times=local_frame_times,
        ))
        timeline_cursor += duration
        previous_content_end = span.start + timedelta(
            seconds=source_offset + duration
        )
    return parts


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


def distinct_view_count(
    raw_count: int, upper_count: int, lower_count: int
) -> int:
    """Evita somar a mesma entidade observada nas duas câmeras empilhadas.

    Sem rastreamento de identidade entre ângulos, max() é uma estimativa
    conservadora; raw_count só é usado quando o motor não trouxe contagem
    discriminada por metade.
    """
    upper_count = clean_nonnegative_int(upper_count)
    lower_count = clean_nonnegative_int(lower_count)
    if upper_count or lower_count:
        return max(upper_count, lower_count)
    return clean_nonnegative_int(raw_count)


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
    upper_person_count = clean_nonnegative_int(
        data.get("upper_person_count")
    )
    lower_person_count = clean_nonnegative_int(
        data.get("lower_person_count")
    )
    upper_animal_count = clean_nonnegative_int(
        data.get("upper_animal_count")
    )
    lower_animal_count = clean_nonnegative_int(
        data.get("lower_animal_count")
    )
    upper_vehicle_count = clean_nonnegative_int(
        data.get("upper_vehicle_count")
    )
    lower_vehicle_count = clean_nonnegative_int(
        data.get("lower_vehicle_count")
    )
    raw_person_count, raw_animal_count = person_count, animal_count
    person_count = distinct_view_count(
        person_count, upper_person_count, lower_person_count
    )
    animal_count = distinct_view_count(
        animal_count, upper_animal_count, lower_animal_count
    )
    if (person_count, animal_count) != (raw_person_count, raw_animal_count):
        logger.info(
            "contagem_camera_deduplicada bruto=%d/%d distinto=%d/%d "
            "por_camera=%d/%d_pessoas,%d/%d_animais",
            raw_person_count, raw_animal_count, person_count, animal_count,
            upper_person_count, lower_person_count,
            upper_animal_count, lower_animal_count,
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
        person_sharpness=clean_score(data.get("person_sharpness")) or 0.0,
        frame_sharpness=clean_score(data.get("frame_sharpness")) or 0.0,
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
        upper_person_count=upper_person_count,
        lower_person_count=lower_person_count,
        upper_animal_count=upper_animal_count,
        lower_animal_count=lower_animal_count,
        upper_vehicle_count=upper_vehicle_count,
        lower_vehicle_count=lower_vehicle_count,
        cat_rescue=bool(data.get("cat_rescue", False)),
        clear_person_count=clean_nonnegative_int(data.get("clear_person_count")),
        upper_clear_person_count=clean_nonnegative_int(data.get("upper_clear_person_count")),
        lower_clear_person_count=clean_nonnegative_int(data.get("lower_clear_person_count")),
        clear_animal_count=clean_nonnegative_int(data.get("clear_animal_count")),
        upper_clear_animal_count=clean_nonnegative_int(data.get("upper_clear_animal_count")),
        lower_clear_animal_count=clean_nonnegative_int(data.get("lower_clear_animal_count")),
        person_visible_area=max(0.0, clean_score(data.get("person_visible_area")) or 0.0),
        visibility_measured="person_visible_area" in data,
    )
    if base:
        candidate.path = base.path
        candidate.source = base.source
        candidate.origin_path = base.origin_path
        candidate.frame_sec = base.frame_sec
        candidate.md5 = base.md5
    return apply_candidate_metrics(candidate)


def _late_candidate_key(candidate: CandidateImage) -> tuple[int, int, float, float]:
    group = 3 if candidate.person_count else 2 if candidate.animal_count else 1 if candidate.category == "vehicle" else 0
    count = (candidate.person_count or candidate.animal_count
             or (candidate.object_count if group == 1 else 0))
    return group, count, float(candidate.sharpness or 0.0), float(candidate.engine_score or 0.0)


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
    def priority(value: CandidateImage | ChampionInfo) -> tuple[int, int]:
        if value.person_count:
            return 3, value.person_count
        if value.animal_count:
            return 2, value.animal_count
        if value.category == "vehicle":
            return 1, value.object_count
        return 0, 0

    new_priority, old_priority = priority(candidate), priority(champion)
    if new_priority < old_priority:
        return False, "menos_alvos_relevantes"
    if new_priority > old_priority:
        return True, ("mais_pessoas" if candidate.person_count else
                      "mais_animais" if candidate.animal_count else "mais_veiculos")
    if candidate.animal_count > champion.animal_count:
        return True, "mais_animais"
    # rank_key prefers an earlier timestamp *before* sharpness. That is useful
    # to pick an initial alert, but cannot measure whether a later image adds
    # information. Require a material, comparable quality improvement here.
    new_quality = float(candidate.sharpness or 0.0)
    old_quality = float(champion.sharpness or 0.0)
    if new_quality > old_quality and new_quality >= old_quality * 1.15:
        return True, "mesmos_alvos_mais_nitidez"
    return False, "sem_ganho_relevante"


def summarize_candidate(candidate: CandidateImage) -> str:
    nitidez = (
        f"{candidate.sharpness:.2f}"
        if candidate.sharpness is not None
        else "N/D"
    )
    if candidate.engine_score is None:
        counts = "pessoas=? animais=? analise=pendente"
    else:
        counts = (
            f"pessoas={candidate.person_count} "
            f"animais={candidate.animal_count}"
        )
    return f"arquivo={candidate.path.name} {counts} nitidez={nitidez}"


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


def consolidate_event_counts(
    candidates: Sequence[CandidateImage],
    winner: Optional[CandidateImage],
) -> tuple[int, int, dict[str, int]]:
    """Consolidate recurring detections per half without double-summing views."""
    evidence = list(candidates)
    if winner is not None and all(item.path != winner.path for item in evidence):
        evidence.append(winner)
    recurring: dict[tuple[str, str, str], list[CandidateImage]] = {}
    for candidate in evidence:
        if candidate.source != "frame":
            continue
        origin = candidate.origin_path.resolve() if candidate.origin_path else candidate.path.parent.resolve()
        for half, person_count, animal_count in (
            ("upper", candidate.upper_person_count, candidate.upper_animal_count),
            ("lower", candidate.lower_person_count, candidate.lower_animal_count),
        ):
            if person_count > 0:
                recurring.setdefault((str(origin), half, "person"), []).append(candidate)
            if animal_count > 0:
                recurring.setdefault((str(origin), half, "animal"), []).append(candidate)

    counts = {
        "upper_person": 0, "lower_person": 0,
        "upper_animal": 0, "lower_animal": 0,
    }
    for (_, half, kind), observations in recurring.items():
        times = {
            round(item.frame_sec, 3) if item.frame_sec is not None else item.path.name
            for item in observations
        }
        if len(times) < 2:
            continue
        key = f"{half}_{kind}"
        attr = f"{half}_{kind}_count"
        # Candidate fields are upper_person_count/lower_animal_count, etc.
        counts[key] = max(counts[key], max(getattr(item, attr) for item in observations))

    point_evidence = [
        item for item in evidence if item.source == "photo" or item is winner
    ]
    for candidate in point_evidence:
        for key in counts:
            counts[key] = max(
                counts[key],
                clean_nonnegative_int(getattr(candidate, f"{key}_count")),
            )
        if not any((candidate.upper_person_count, candidate.lower_person_count,
                    candidate.upper_animal_count, candidate.lower_animal_count)):
            if counts["upper_person"] == counts["lower_person"] == 0:
                counts["lower_person"] = max(counts["lower_person"], candidate.person_count)
            if counts["upper_animal"] == counts["lower_animal"] == 0:
                counts["lower_animal"] = max(counts["lower_animal"], candidate.animal_count)

    return (
        max(counts["upper_person"], counts["lower_person"]),
        max(counts["upper_animal"], counts["lower_animal"]),
        counts,
    )


def event_count_range(camera_counts: dict[str, int], kind: str) -> tuple[int, int]:
    upper = clean_nonnegative_int(camera_counts.get(f"upper_{kind}", 0))
    lower = clean_nonnegative_int(camera_counts.get(f"lower_{kind}", 0))
    return max(upper, lower), upper + lower


def format_event_count_range(camera_counts: dict[str, int], kind: str) -> str:
    minimum, maximum = event_count_range(camera_counts, kind)
    return str(minimum) if minimum == maximum else f"{minimum}–{maximum}"


def candidate_count_message(
    candidate: CandidateImage,
) -> tuple[str, str, Optional[str]]:
    counts = {
        "upper_person": candidate.upper_person_count,
        "lower_person": candidate.lower_person_count,
        "upper_animal": candidate.upper_animal_count,
        "lower_animal": candidate.lower_animal_count,
    }
    if not any(counts.values()):
        return str(candidate.person_count), str(candidate.animal_count), None
    people = format_event_count_range(counts, "person")
    animals = format_event_count_range(counts, "animal")
    ambiguous = any(
        low != high for low, high in (
            event_count_range(counts, "person"),
            event_count_range(counts, "animal"),
        )
    )
    detail = None
    if ambiguous:
        detail = (
            f"Ângulos: sup 👤{counts['upper_person']} 🐾{counts['upper_animal']} · "
            f"inf 👤{counts['lower_person']} 🐾{counts['lower_animal']} "
            "(possível sobreposição)"
        )
    return people, animals, detail


def immediate_object_summary(candidate: CandidateImage) -> str:
    """Não confunde timeout/ausência do YOLO com uma contagem realmente zero."""
    if candidate.engine_score is None:
        return "👤? 🐾? · análise YOLO pendente"
    people, animals, _detail = candidate_count_message(candidate)
    return f"👤{people} 🐾{animals}"
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
                    immediate_retry_media TEXT,
                    immediate_retry_message TEXT,
                    enhanced_done INTEGER NOT NULL DEFAULT 0,
                    enhanced_sent INTEGER NOT NULL DEFAULT 0,
                    enhanced_started_at INTEGER,
                    enhanced_retry_media TEXT,
                    enhanced_retry_message TEXT,
                    motion_frames INTEGER NOT NULL DEFAULT 0,
                    late_motion_sent INTEGER NOT NULL DEFAULT 0,
                    motion_camera_half TEXT,
                    motion_decision TEXT,
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
            if self._table_exists("windows") and not self._column_exists("windows", "immediate_retry_media"):
                cursor.execute("ALTER TABLE windows ADD COLUMN immediate_retry_media TEXT")
            if self._table_exists("windows") and not self._column_exists("windows", "immediate_retry_message"):
                cursor.execute("ALTER TABLE windows ADD COLUMN immediate_retry_message TEXT")
            if self._table_exists("windows") and not self._column_exists("windows", "enhanced_retry_media"):
                cursor.execute("ALTER TABLE windows ADD COLUMN enhanced_retry_media TEXT")
            if self._table_exists("windows") and not self._column_exists("windows", "enhanced_retry_message"):
                cursor.execute("ALTER TABLE windows ADD COLUMN enhanced_retry_message TEXT")
            if self._table_exists("windows") and not self._column_exists("windows", "motion_frames"):
                cursor.execute("ALTER TABLE windows ADD COLUMN motion_frames INTEGER NOT NULL DEFAULT 0")
            if self._table_exists("windows") and not self._column_exists("windows", "late_motion_sent"):
                cursor.execute("ALTER TABLE windows ADD COLUMN late_motion_sent INTEGER NOT NULL DEFAULT 0")
            if self._table_exists("windows") and not self._column_exists("windows", "motion_camera_half"):
                cursor.execute("ALTER TABLE windows ADD COLUMN motion_camera_half TEXT")
            if self._table_exists("windows") and not self._column_exists("windows", "motion_decision"):
                cursor.execute("ALTER TABLE windows ADD COLUMN motion_decision TEXT")
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

    def _recount_normal_files(
        self, cursor: sqlite3.Cursor, window_id: int
    ) -> None:
        """Mantém os contadores da janela alinhados aos arquivos normais."""
        row = cursor.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(is_video), 0) AS videos "
            "FROM files WHERE window_id=? AND assignment='normal'",
            (window_id,),
        ).fetchone()
        total = int(row["total"] or 0)
        videos = int(row["videos"] or 0)
        cursor.execute(
            "UPDATE windows SET file_count=?, video_count=?, photo_count=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
            (total, videos, total - videos, window_id),
        )

    def _merge_overlapping_windows(
        self, cursor: sqlite3.Cursor, survivor_id: int
    ) -> None:
        """Consolida janelas tocantes criadas antes de um vídeo ser estendido.

        Câmeras podem publicar primeiro um MP4 curto e, alguns minutos depois,
        a versão final do mesmo arquivo.  A extensão pode alcançar uma janela
        já aberta/criada; sem esta etapa o mesmo movimento gera dois alertas.
        """
        while True:
            survivor = cursor.execute(
                "SELECT window_id, window_start, window_end, enhanced_done, "
                "enhanced_started_at, first_seen_at, last_file_at "
                "FROM windows WHERE window_id=?",
                (survivor_id,),
            ).fetchone()
            if survivor is None:
                return
            overlap = cursor.execute(
                "SELECT window_id, window_start, window_end, first_seen_at, "
                "last_file_at FROM windows "
                "WHERE window_id != ? AND state != 'merged' "
                "AND window_start <= ? AND window_end >= ? "
                "ORDER BY window_start, window_id LIMIT 1",
                (
                    survivor_id,
                    int(survivor["window_end"])
                    + Config.WINDOW_TOLERANCE_SECONDS,
                    int(survivor["window_start"])
                    - Config.WINDOW_TOLERANCE_SECONDS,
                ),
            ).fetchone()
            if overlap is None:
                return
            loser_id = int(overlap["window_id"])
            # Preserve a janela cronologicamente mais antiga: ela é a que
            # normalmente já possui o alerta imediato e o campeão enviado.
            if (
                int(overlap["window_start"]), loser_id
            ) < (int(survivor["window_start"]), survivor_id):
                survivor_id, loser_id = loser_id, survivor_id
                continue
            enhanced_started = int(survivor["enhanced_started_at"] or 0) > 0
            if int(survivor["enhanced_done"] or 0) == 1 or enhanced_started:
                cursor.execute(
                    "UPDATE files SET assignment='late', evaluated=0, "
                    "rebuild_motion=is_video WHERE window_id=? "
                    "AND assignment='normal'",
                    (loser_id,),
                )
                cursor.execute(
                    "UPDATE windows SET late_attempts=0, late_motion_sent=0 "
                    "WHERE window_id=?",
                    (survivor_id,),
                )
            cursor.execute(
                "UPDATE files SET window_id=? WHERE window_id=?",
                (survivor_id, loser_id),
            )
            cursor.execute(
                "UPDATE windows SET window_start=MIN(window_start, ?), "
                "window_end=MAX(window_end, ?), "
                "first_seen_at=MIN(COALESCE(first_seen_at, ?), ?), "
                "last_file_at=MAX(COALESCE(last_file_at, 0), ?), "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (
                    int(overlap["window_start"]), int(overlap["window_end"]),
                    int(overlap["first_seen_at"] or survivor["first_seen_at"] or 0),
                    int(overlap["first_seen_at"] or survivor["first_seen_at"] or 0),
                    int(overlap["last_file_at"] or survivor["last_file_at"] or 0),
                    survivor_id,
                ),
            )
            cursor.execute(
                "UPDATE windows SET state='merged', close_reason=?, "
                "immediate_done=1, enhanced_done=1, "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (f"merged_into:{survivor_id}", loser_id),
            )
            self._recount_normal_files(cursor, survivor_id)
            logger.info(
                "janelas consolidadas sobrevivente=%d incorporada=%d",
                survivor_id, loser_id,
            )

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
                    self._merge_overlapping_windows(cursor, old_window)
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
                self._merge_overlapping_windows(cursor, target_id)
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
                self._merge_overlapping_windows(cursor, wid)
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
            enhanced_attempts=int(row["enhanced_attempts"] or 0),
            enhanced_retry_media=path_from_db(row["enhanced_retry_media"]),
            enhanced_retry_message=(
                str(row["enhanced_retry_message"])
                if row["enhanced_retry_message"]
                else None
            ),
            motion_frames=int(row["motion_frames"] or 0),
            late_motion_sent=int(row["late_motion_sent"] or 0),
            motion_camera_half=row["motion_camera_half"],
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
                # Retomadas voltam ao fim da rodada; sem rotação, a janela
                # mais antiga monopoliza o único worker após cada timeout.
                "ORDER BY enhanced_attempts, CASE WHEN enhanced_started_at IS NULL THEN 0 ELSE 1 END, "
                "enhanced_started_at, window_start, window_id",
                (Config.MAX_ALERT_RETRIES, threshold),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def has_enhanced_priority(self) -> bool:
        """Há uma janela normal entre o instantâneo e o resumo?

        Inclui a janela ainda aberta durante a tolerância de fechamento. Isso
        impede a retomada do tardio nos 30 s entre o instantâneo e o resumo.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM windows "
                "WHERE immediate_done=1 AND enhanced_done=0 "
                "AND enhanced_attempts<? "
                "AND state IN ('open', 'closed') LIMIT 1",
                (Config.MAX_ALERT_RETRIES,),
            ).fetchone()
        return row is not None

    def pending_late(self) -> list[int]:
        threshold = int(time.time()) - Config.LATE_COMPARE_DELAY
        with self._lock:
            rows = self.conn.execute(
                "SELECT f.window_id FROM files f "
                "JOIN windows w ON w.window_id=f.window_id "
                "WHERE f.assignment='late' AND f.evaluated=0 "
                "AND f.discovered_at<=? AND w.enhanced_done=1 "
                "AND w.late_attempts<? AND w.window_id != 0 "
                # Uma janela tardia que precisou de retry cede lugar às que
                # ainda não consumiram tentativa.
                "GROUP BY f.window_id "
                "ORDER BY w.late_attempts, MIN(f.discovered_at), w.window_id",
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

    def get_immediate_retry(
        self, window_id: int
    ) -> tuple[Optional[Path], Optional[str], Optional[Path]]:
        with self._lock:
            row = self.conn.execute(
                "SELECT immediate_retry_media, immediate_retry_message, "
                "immediate_image_path FROM windows WHERE window_id=?",
                (window_id,),
            ).fetchone()
        if row is None:
            return None, None, None
        return (
            path_from_db(row["immediate_retry_media"]),
            str(row["immediate_retry_message"])
            if row["immediate_retry_message"] else None,
            path_from_db(row["immediate_image_path"]),
        )

    def set_immediate_retry(
        self,
        window_id: int,
        media_path: Path,
        message: str,
        original_path: Path,
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET immediate_retry_media=?, "
                "immediate_retry_message=?, immediate_image_path=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (
                    safe_rel_path(media_path), message,
                    safe_rel_path(original_path), window_id,
                ),
            )
            self.conn.commit()

    def clear_immediate_retry(self, window_id: int) -> Optional[Path]:
        with self._lock:
            row = self.conn.execute(
                "SELECT immediate_retry_media FROM windows WHERE window_id=?",
                (window_id,),
            ).fetchone()
            self.conn.execute(
                "UPDATE windows SET immediate_retry_media=NULL, "
                "immediate_retry_message=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (window_id,),
            )
            self.conn.commit()
        return path_from_db(row[0]) if row and row[0] else None

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

    def set_enhanced_retry(
        self, window_id: int, media_path: Path, message: str
    ) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET enhanced_retry_media=?, "
                "enhanced_retry_message=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE window_id=?",
                (safe_rel_path(media_path), message, window_id),
            )
            self.conn.commit()

    def clear_enhanced_retry(self, window_id: int) -> Optional[Path]:
        with self._lock:
            row = self.conn.execute(
                "SELECT enhanced_retry_media FROM windows WHERE window_id=?",
                (window_id,),
            ).fetchone()
            self.conn.execute(
                "UPDATE windows SET enhanced_retry_media=NULL, "
                "enhanced_retry_message=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE window_id=?",
                (window_id,),
            )
            self.conn.commit()
        return path_from_db(row[0]) if row and row[0] else None

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
            "upper_person_count": candidate.upper_person_count,
            "upper_animal_count": candidate.upper_animal_count,
            "lower_person_count": candidate.lower_person_count,
            "lower_animal_count": candidate.lower_animal_count,
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

    def set_motion_context(self, window_id: int, motion: MotionBuild) -> None:
        """Persiste o motivo e as contagens por metade do vídeo enviado."""
        evidence = motion.evidence
        decision = {
            "version": 1,
            "reason": motion.reason,
            "evidence_path": safe_rel_path(evidence.path),
            "evidence_source": evidence.source,
            "upper_person_count": evidence.upper_person_count,
            "upper_animal_count": evidence.upper_animal_count,
            "lower_person_count": evidence.lower_person_count,
            "lower_animal_count": evidence.lower_animal_count,
        }
        with self._lock:
            self.conn.execute(
                "UPDATE windows SET motion_camera_half=?, motion_decision=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE window_id=?",
                (
                    motion.camera_half,
                    json.dumps(decision, ensure_ascii=False, separators=(",", ":")),
                    window_id,
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
    upper_count = (
        candidate.upper_person_count + candidate.upper_animal_count
        + candidate.upper_vehicle_count
    )
    lower_count = (
        candidate.lower_person_count + candidate.lower_animal_count
        + candidate.lower_vehicle_count
    )
    if upper_count > lower_count:
        return "upper"
    if lower_count > upper_count:
        return "lower"
    if upper_count > 0 and lower_count > 0:
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


def choose_immediate_media(
    files: Sequence[Path], window_id: int, sort_key: int = 0
) -> Optional[CandidateImage]:
    """Usa YOLO somente nas fotos já disponíveis, limitado e prioritário."""
    def sharpest(candidates: Sequence[CandidateImage]) -> Optional[CandidateImage]:
        for candidate in candidates:
            candidate.sharpness = compute_sharpness(candidate.path)
        return max(
            candidates,
            key=lambda c: (c.sharpness or 0.0, c.area),
            default=None,
        )

    files = dedupe_media_paths(files)
    photos = sorted(
        (p for p in files if is_photo(p) and is_valid_image_file(p)),
        key=lambda p: p.name,
    )
    if photos:
        selected_photos = (
            photos if Config.IMMEDIATE_MAX_PHOTOS <= 0
            else choose_evenly(photos, Config.IMMEDIATE_MAX_PHOTOS)
        )
        candidates = [
            candidate for photo in selected_photos
            if (candidate := make_candidate(photo, "photo", photo)) is not None
        ]
        if candidates:
            scored, winner = run_melhor_foto(
                candidates, window_id, "immediate",
                Config.MELHOR_FOTO_IMMEDIATE_TIMEOUT, (0, sort_key),
            )
            if winner is not None:
                return winner
            logger.warning(
                "janela=%s imediato YOLO indisponível/timeout; fallback=nitidez",
                window_id,
            )
            return sharpest(candidates)
    videos = sorted(
        (p for p in files if is_video(p) and is_valid_video_file(p)),
        key=lambda p: p.name,
    )
    for video in videos[:1]:
        candidates = [
            candidate
            for frame in extract_first_frames(
                video, Config.IMMEDIATE_VIDEO_FRAMES
            )
            for candidate in [make_candidate(
                frame, "frame", video,
                float(max(0, (_frame_number(frame) or 1) - 1)),
            )]
            if candidate is not None
        ]
        if candidates:
            scored, winner = run_melhor_foto(
                candidates, window_id, "immediate",
                Config.MELHOR_FOTO_IMMEDIATE_TIMEOUT, (0, sort_key),
            )
            if winner is not None:
                return winner
            logger.warning(
                "janela=%s imediato YOLO indisponível/timeout; fallback=nitidez",
                window_id,
            )
            return sharpest(candidates)
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
    span = extract_motion_span(video_path, fallback_date=datetime.now())
    duration = (
        (span.end - span.start).total_seconds() if span is not None else 0.0
    )
    sample_short_clip = (
        0.0 < duration <= Config.IMMEDIATE_FULL_SAMPLE_MAX_SECONDS
    )
    command = [
        Config.FFMPEG_CMD,
        *(["-f", "h264"] if raw else []),
        "-i", str(video_path),
        # Em clipes curtos, lê a linha temporal toda a 1 fps para selecionar
        # instantes representativos; em clipes longos, limita a leitura aos
        # primeiros segundos para preservar a prioridade do aviso imediato.
        "-vf", "fps=1",
        *([] if sample_short_clip else ["-frames:v", str(count)]),
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
    )
    if not frames:
        shutil.rmtree(frame_dir, ignore_errors=True)
        return []
    if len(frames) <= count or not sample_short_clip:
        return frames[:count]

    sample_candidates = [
        CandidateImage(
            path=frame,
            source="frame",
            origin_path=video_path,
            frame_sec=float(max(0, (_frame_number(frame) or 1) - 1)),
        )
        for frame in frames
    ]
    salience_cache = {
        video_path.resolve(): _compute_salience_scores_pair(frames)
    }
    selected = sample_frames_across_timeline(
        sample_candidates,
        [video_path],
        count,
        salience_cache=salience_cache,
        include_terminal=False,
    )
    logger.info(
        "imediato amostra_temporal video=%s duração=%.1fs extraídos=%d "
        "selecionados=%d frames=%s",
        video_path.name, duration, len(frames), len(selected),
        ",".join(candidate.path.name for candidate in selected),
    )
    return [candidate.path for candidate in selected]


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
    adaptive_fallback_count: int = 0,
    salience_cache: Optional[dict[Path, dict[str, list[float]]]] = None,
) -> tuple[list[CandidateImage], Optional[CandidateImage]]:
    valid: list[CandidateImage] = []
    seen_candidate_paths: set[Path] = set()
    duplicate_candidates = 0
    for candidate in candidates:
        if not is_valid_image_file(candidate.path):
            continue
        try:
            candidate_key = candidate.path.resolve()
        except OSError:
            candidate_key = candidate.path.absolute()
        if candidate_key in seen_candidate_paths:
            duplicate_candidates += 1
            continue
        seen_candidate_paths.add(candidate_key)
        valid.append(candidate)
    if duplicate_candidates:
        logger.info(
            "Janela %s YOLO candidatos duplicados removidos=%d restantes=%d",
            window_id, duplicate_candidates, len(valid),
        )
    if (
        not valid
        or not Config.MELHOR_FOTO_SCRIPT.exists()
        or not Config.MELHOR_FOTO_PYTHON.is_file()
    ):
        if not Config.MELHOR_FOTO_SCRIPT.exists():
            logger.warning(
                "melhor_foto.py não encontrado: %s", Config.MELHOR_FOTO_SCRIPT
            )
        if not Config.MELHOR_FOTO_PYTHON.is_file():
            logger.error(
                "Python YOLO não encontrado: %s", Config.MELHOR_FOTO_PYTHON
            )
        return [], None
    long_enhanced_video = tag == "enhanced" and any(
        candidate.source == "frame"
        and candidate.origin_path is not None
        and is_video(candidate.origin_path)
        and _declared_video_duration(candidate.origin_path) > 90.0
        for candidate in valid
    )
    candidate_limit = (
        32 + 4 if long_enhanced_video
        else Config.ENHANCED_ANALYSIS_MAX_FRAMES
        + Config.ENHANCED_ADAPTIVE_EXTRA_FRAMES
        if tag == "enhanced"
        else 0 if tag == "immediate"
        else Config.MELHOR_FOTO_MAX_CANDIDATES
    )
    if long_enhanced_video:
        adaptive_fallback_count = min(4, max(0, adaptive_fallback_count))
        logger.info(
            "Janela %s YOLO vídeo_longo amostra=%d frames "
            "(32 principais + até 4 adaptativos)",
            window_id, candidate_limit,
        )
    if tag == "enhanced":
        dense_video_paths = {
            video.resolve() for video in dedupe_media_paths(
                [candidate.origin_path for candidate in valid
                 if candidate.source == "frame" and candidate.origin_path]
            )
            if is_video(video)
            and 0 < _declared_video_duration(video) <= 240.0
        }
        dense_frames = [
            candidate for candidate in valid
            if candidate.source == "frame" and candidate.origin_path
            and candidate.origin_path.resolve() in dense_video_paths
        ]
        if len(valid) > candidate_limit and dense_frames:
            photos = _limit_candidates_for_analysis(
                [candidate for candidate in valid if candidate.source == "photo"],
                max_candidates=candidate_limit,
            )
            other = [
                candidate for candidate in valid
                if candidate not in dense_frames and candidate not in photos
            ]
            frame_budget = max(
                0, candidate_limit - len(photos) - min(len(other), candidate_limit)
            )
            selected_dense = sample_frames_across_timeline(
                dense_frames,
                list(dict.fromkeys(
                    candidate.origin_path for candidate in dense_frames
                    if candidate.origin_path is not None
                )),
                frame_budget, salience_cache=salience_cache,
            )
            other_budget = max(
                0, candidate_limit - len(photos) - len(selected_dense)
            )
            selected_other = _limit_candidates_for_analysis(
                other, max_candidates=other_budget,
            ) if other_budget else []
            valid = photos + selected_dense + selected_other
            logger.info(
                "Janela %s YOLO amostra frames de vídeo(s) curto(s) "
                "originais=%d selecionados=%d outros=%d total=%d limite=%d",
                window_id, len(dense_frames), len(selected_dense),
                len(selected_other), len(valid), candidate_limit,
            )
        else:
            valid = _limit_candidates_for_analysis(
                valid, max_candidates=candidate_limit,
            )
    else:
        valid = _limit_candidates_for_analysis(
            valid, max_candidates=candidate_limit,
        )
    effective_timeout = timeout
    if tag == "enhanced" and long_enhanced_video:
        # Long clips are deliberately capped at 36 YOLO frames. Keep enough
        # headroom for CPU inference (the arrival test took ~102s), while
        # avoiding the generic 300s timeout that previously stalled a queue.
        effective_timeout = min(timeout, 180)
        logger.info(
            "Janela %s YOLO timeout adaptativo vídeo_longo candidatos=%d "
            "timeout=%ds",
            window_id, len(valid), effective_timeout,
        )
    elif tag == "enhanced" and len(valid) > candidate_limit:
        scaled_timeout = int(
            Config.MELHOR_FOTO_ENHANCED_TIMEOUT
            + len(valid) * Config.MELHOR_FOTO_ENHANCED_SECONDS_PER_CANDIDATE
        )
        effective_timeout = min(
            Config.MELHOR_FOTO_ENHANCED_TIMEOUT_MAX,
            max(timeout, scaled_timeout),
        )
        logger.info(
            "Janela %s YOLO lote denso candidatos=%d timeout=%ds",
            window_id, len(valid), effective_timeout,
        )
    command = [
        str(Config.MELHOR_FOTO_PYTHON), str(Config.MELHOR_FOTO_SCRIPT),
        "--engine", Config.MELHOR_FOTO_ENGINE,
        "--output", "json", "--quiet",
    ]
    if tag == "enhanced" and adaptive_fallback_count > 0:
        command += [
            "--adaptive-primary-count",
            str(max(0, len(valid) - adaptive_fallback_count)),
        ]
    if tag == "immediate":
        command.append("--fast-priority")
        logger.info(
            "janela=%s imediato yolo_modo=fast_priority imagens=%d timeout=%ds",
            window_id, len(valid), timeout,
        )
    yolo_checkpoint = _yolo_checkpoint_path(window_id, tag)
    if yolo_checkpoint is not None:
        command += ["--checkpoint-file", str(yolo_checkpoint)]
    if Config.MELHOR_FOTO_LOGS:
        command += [
            "--log-file",
            str(Config.TMP_DIR / f"melhor_foto_{tag}_{window_id}.log"),
        ]
    command += [str(candidate.path) for candidate in valid]
    yolo_started = time.monotonic()
    with engine_slot(priority, f"{tag}:{window_id}"):
        yolo_acquired = time.monotonic()
        try:
            result = run_preemptible_subprocess(
                command, capture_output=True, text=True,
                timeout=effective_timeout, phase=tag,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                logger.warning(
                    "Janela %s melhor_foto.py timeout tag=%s limite=%ss; "
                    "decorrido=%.1fs checkpoint parcial será preservado "
                    "para retomada",
                    window_id, tag, effective_timeout,
                    time.monotonic() - yolo_started,
                )
                if tag != "immediate":
                    raise BackgroundYOLOTimeout(
                        f"fase={tag} YOLO timeout; checkpoint preservado"
                    ) from exc
            else:
                logger.warning(
                    "Janela %s melhor_foto.py falhou (%s): %s",
                    window_id, tag, exc,
                )
            return [], None
    yolo_elapsed = time.monotonic() - yolo_started
    if result.returncode != 0 or not result.stdout:
        logger.warning(
            "Janela %s melhor_foto.py código=%s tag=%s duração=%.1fs stderr=%s",
            window_id, result.returncode, tag, yolo_elapsed,
            (result.stderr or "")[-500:],
        )
        return [], None
    logger.info(
        "Janela %s melhor_foto.py concluído tag=%s candidatos=%d duração=%.1fs "
        "espera_motor=%.1fs execucao=%.1fs",
        window_id, tag, len(valid), yolo_elapsed,
        yolo_acquired - yolo_started, time.monotonic() - yolo_acquired,
    )
    data = parse_engine_json(result.stdout)
    if not data:
        logger.warning(
            "Janela %s melhor_foto.py retornou JSON inválido", window_id
        )
        return [], None
    analysis = data.get("analysis")
    sweep = analysis.get("adaptive_sweep") if isinstance(analysis, dict) else None
    if isinstance(sweep, dict):
        logger.info(
            "Janela %s varredura_adaptativa acionada=%s motivo=%s "
            "frames=%s/%s duração=%.2fs evidência_antes=%s evidência_depois=%s",
            window_id, sweep.get("triggered", False),
            sweep.get("reason", "N/D"),
            sweep.get("additional_processed", 0),
            sweep.get("additional_available", 0),
            float(sweep.get("duration_seconds", 0.0) or 0.0),
            sweep.get("evidence_before", "N/D"),
            sweep.get("evidence_after", "N/D"),
        )
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
    if tag == "enhanced" and adaptive_fallback_count > 0:
        primary_count = max(0, len(valid) - adaptive_fallback_count)
        logger.info(
            "Janela %s frames_avaliados primários=%d adicionais=%d limite=%d",
            window_id, min(primary_count, len(scored)),
            max(0, len(scored) - primary_count), candidate_limit,
        )
    winner = max(scored, key=(
        _late_candidate_key if tag in {"late", "late_rebuild"}
        else lambda candidate: candidate.rank_key
    ))
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
        signed_diffs = arr - bg             # (N, h, w)
        # O acendimento das luzes pode deslocar o brilho de quase todos os
        # pixels. Remova esse deslocamento global antes de medir movimento,
        # preservando diferenças locais como a chegada de um veículo.
        illumination_shift = np.median(
            signed_diffs, axis=(1, 2), keepdims=True
        )
        diffs = np.abs(signed_diffs - illumination_shift)
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
    result: dict[Path, dict[str, list[float]]] = {}
    for frame_set in frame_sets:
        if frame_set.salience_cache is None:
            cache_path = None
            if (frame_set.frames and Config.TMP_DIR.resolve() in frame_set.frames[0].resolve().parents
                    and all(p.is_file() for p in frame_set.frames)):
                cache_path = frame_set.frames[0].parent / ".salience.json"
            identity = [str(p) for p in frame_set.frames]
            saved = None
            if cache_path is not None:
                try:
                    data = json.loads(cache_path.read_text())
                    if data.get("frames") == identity and data.get("threshold") == Config.MOTION_SALIENCE_THRESHOLD:
                        values = data.get("scores", {})
                        if all(len(values.get(half, [])) == len(identity) for half in ("upper", "lower")):
                            saved = values
                except (OSError, ValueError, TypeError):
                    pass
            frame_set.salience_cache = saved or _compute_salience_scores_pair(frame_set.frames)
            if saved is None and cache_path is not None:
                try:
                    cache_path.write_text(json.dumps({"frames": identity, "threshold": Config.MOTION_SALIENCE_THRESHOLD,
                                                      "scores": frame_set.salience_cache}))
                except OSError:
                    pass
        result[frame_set.video_path.resolve()] = frame_set.salience_cache
    return result


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


def _salience_scores_for_half(
    frame_paths: Sequence[Path],
    camera_half: str,
    cached: Optional[dict[str, list[float]]] = None,
) -> list[float]:
    """Retorna a saliência de uma câmera ou do quadro composto.

    ``full`` não existe no cache: ele representa as duas câmeras empilhadas.
    Para escolher a âncora de um vídeo completo, um pico em qualquer uma das
    metades deve ser suficiente; por isso se usa o máximo por frame.
    """
    pair = cached if cached is not None else _compute_salience_scores_pair(
        frame_paths
    )
    if camera_half in ("upper", "lower"):
        return list(pair.get(camera_half, [0.0] * len(frame_paths)))
    upper = pair.get("upper", [0.0] * len(frame_paths))
    lower = pair.get("lower", [0.0] * len(frame_paths))
    return [max(u, l) for u, l in zip(upper, lower)]


def _pick_camera_half(
    frame_sets: Sequence[MotionFrameSet],
    salience_cache: Optional[dict[Path, dict[str, list[float]]]] = None,
) -> str:
    if salience_cache is None:
        salience_cache = _build_salience_cache(frame_sets)
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
                _salience_scores_for_half(fs.frames, camera_half, cached),
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
    min_salience = max(0.0, float(Config.MOTION_HALF_MIN_SALIENCE))
    if best_score < min_salience:
        logger.info(
            "camera_metade preliminar=full motivo=saliencia_baixa "
            "superior=%.4f inferior=%.4f limite=%.4f",
            upper_score, lower_score, min_salience,
        )
        return "full"
    other_score = min(upper_score, lower_score)
    dominance = (
        best_score / max(other_score, 1e-9)
        if other_score >= min_salience
        else math.inf
    )
    max_ratio = max(1.0, float(Config.MOTION_HALF_DOMINANCE_RATIO))
    if other_score >= min_salience and dominance <= max_ratio:
        selected_half = "lower"
        logger.info(
            "camera_metade preliminar=%s motivo=saliencia_nas_duas "
            "prioridade_inferior "
            "superior=%.4f inferior=%.4f razao=%.2f limite=%.2f",
            selected_half, upper_score, lower_score, dominance, max_ratio,
        )
        return selected_half
    logger.info(
        "camera_metade preliminar=%s saliencia_ajustada=%.4f "
        "superior=%.4f inferior=%.4f",
        best_half, best_score, upper_score, lower_score,
    )
    return best_half


def _select_motion_view_decision(
    candidates: Sequence[CandidateImage], salience_half: str,
) -> tuple[str, str, Optional[CandidateImage], Optional[CandidateImage]]:
    """Decide o enquadramento do vídeo a partir das evidências do evento.

    Full é mantido quando há objetos nas duas metades do mesmo quadro. Também
    usamos full quando o evento contém uma pessoa em uma metade e um animal
    confirmado pelo YOLO na outra, mesmo que apareçam em frames diferentes.
    Nesse segundo caso, a evidência humana continua sendo a referência da
    decisão/âncora; o animal precisa ter score positivo do YOLO (detecção acima
    dos limiares de confiança e nitidez do motor).
    """
    frame_candidates = [
        candidate for candidate in candidates
        if candidate.source == "frame" and candidate.origin_path is not None
    ]

    def has_upper(candidate: CandidateImage) -> bool:
        return (
            candidate.upper_person_count + candidate.upper_animal_count
            + candidate.upper_vehicle_count > 0
        )

    def has_lower(candidate: CandidateImage) -> bool:
        return (
            candidate.lower_person_count + candidate.lower_animal_count
            + candidate.lower_vehicle_count > 0
        )

    # The crop mode is for the motion video, so still-photo detections must
    # not force a full-frame crop outside the video evidence.
    simultaneous = next(
        (candidate for candidate in frame_candidates
         if has_upper(candidate) and has_lower(candidate)),
        None,
    )
    if simultaneous is not None:
        return "full", "objetos_simultaneos", simultaneous, None

    upper_animals = [
        candidate for candidate in frame_candidates
        if candidate.upper_animal_count > 0
        and candidate.engine_score is not None
        and candidate.engine_score > 0.0
    ]
    lower_animals = [
        candidate for candidate in frame_candidates
        if candidate.lower_animal_count > 0
        and candidate.engine_score is not None
        and candidate.engine_score > 0.0
    ]
    upper_people = [
        candidate for candidate in frame_candidates
        if candidate.upper_person_count > 0
    ]
    lower_people = [
        candidate for candidate in frame_candidates
        if candidate.lower_person_count > 0
    ]
    if upper_people and lower_people:
        # The final call is scoped to cut_candidates. People need not be
        # simultaneous for both views to be useful during the sent interval.
        return "full", "pessoas_nas_duas_metades", max(
            upper_people + lower_people, key=lambda c: c.rank_key
        ), None

    opposite_view_pairs: list[tuple[CandidateImage, CandidateImage]] = []
    opposite_view_pairs.extend(
        (animal, person)
        for animal in upper_animals for person in lower_people
    )
    opposite_view_pairs.extend(
        (animal, person)
        for animal in lower_animals for person in upper_people
    )
    if opposite_view_pairs:
        animal, person = max(
            opposite_view_pairs,
            key=lambda pair: (
                float(pair[0].engine_score or 0.0),
                pair[1].rank_key,
            ),
        )
        return "full", "animal_pessoa_angulos_opostos", person, animal

    lower_evidence = [c for c in frame_candidates if has_lower(c)]
    if lower_evidence:
        evidence = max(
            lower_evidence,
            key=lambda candidate: (
                candidate.lower_person_count + candidate.lower_animal_count,
                candidate.upper_person_count + candidate.upper_animal_count,
                candidate.rank_key,
            ),
        )
        return "lower", "objetos_inferior_prioridade", evidence, None

    upper_evidence = [c for c in frame_candidates if has_upper(c)]
    if upper_evidence:
        evidence = max(
            upper_evidence,
            key=lambda candidate: (
                candidate.upper_person_count + candidate.upper_animal_count,
                candidate.lower_person_count + candidate.lower_animal_count,
                candidate.rank_key,
            ),
        )
        return "upper", "objetos_superior", evidence, None

    return salience_half, "saliencia", None, None


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
        scores = _salience_scores_for_half(fs.frames, camera_half, cached)
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
        if (
            c.person_count > 0
            or (c.animal_count > 0 and not c.cat_rescue)
        )
    ]
    if detected:
        def estimated_distinct_people(c: CandidateImage) -> int:
            upper = max(0, int(c.upper_person_count or 0))
            lower = max(0, int(c.lower_person_count or 0))
            # The two stacked views often show the same person. Without a
            # cross-camera identity model, max-per-view is a conservative
            # estimate and avoids rewarding a duplicate as an extra person.
            if upper + lower > 0:
                return max(upper, lower)
            return max(0, int(c.person_count or 0))

        def estimated_distinct_animals(c: CandidateImage) -> int:
            upper = max(0, int(c.upper_animal_count or 0))
            lower = max(0, int(c.lower_animal_count or 0))
            if upper + lower > 0:
                return max(upper, lower)
            return max(0, int(c.animal_count or 0))

        def anchor_rank(
            c: CandidateImage,
        ) -> tuple[int | float, ...]:
            distinct_people = estimated_distinct_people(c)
            distinct_animals = estimated_distinct_animals(c)
            upper_people = max(0, int(c.upper_person_count or 0))
            lower_people = max(0, int(c.lower_person_count or 0))
            upper_animals = max(0, int(c.upper_animal_count or 0))
            lower_animals = max(0, int(c.lower_animal_count or 0))
            # Em modo full, exige evidência nas duas vistas e um mínimo de
            # duas pessoas na metade menos populosa antes de priorizar a
            # legibilidade humana. Assim um grupo inicial com mais caixas,
            # mas menor/menos nítido, não antecipa o corte sobre pessoas
            # grandes e claras que aparecem depois.
            # Fora do modo full preservamos a seleção conservadora atual.
            person_view_priority = (
                3 if upper_people and lower_people else
                2 if lower_people else
                1 if upper_people else 0
            )
            animal_view_priority = (
                3 if upper_animals and lower_animals else
                2 if lower_animals else
                1 if upper_animals else 0
            )
            # Source time is only a final tie-breaker (never filesystem mtime).
            timestamp = c.frame_sec
            if timestamp is None:
                frame_number = _frame_number(c.path)
                timestamp = float(frame_number) if frame_number is not None else 0.0
            frame_sharpness = (
                c.frame_sharpness if c.frame_sharpness > 0.0
                else (c.sharpness or 0.0)
            )
            if distinct_people > 0:
                if camera_half == "full":
                    minimum_view_count = min(upper_people, lower_people)
                    return (
                        # In full mode, a real human detection must outrank
                        # an animal-only candidate. Otherwise a sharp animal
                        # visible in both halves can become the video anchor
                        # even when the same event has usable people frames.
                        2,
                        1 if upper_people and lower_people else 0,
                        1 if minimum_view_count >= 2 else 0,
                        c.person_sharpness,
                        minimum_view_count,
                        lower_people,
                        distinct_people,
                        upper_people,
                        frame_sharpness,
                        float(timestamp),
                    )
                return (
                    1, person_view_priority, distinct_people,
                    lower_people, c.person_sharpness,
                    frame_sharpness, float(timestamp),
                )
            if camera_half == "full":
                return (
                    1,
                    1 if upper_animals and lower_animals else 0,
                    lower_animals,
                    frame_sharpness,
                    distinct_animals,
                    upper_animals,
                    float(timestamp),
                )
            return (
                0, animal_view_priority, distinct_animals,
                lower_animals, frame_sharpness,
                0.0, float(timestamp),
            )

        best_detected = max(detected, key=anchor_rank)
        if camera_half == "full":
            full_candidates = [
                c for c in detected
                if c.upper_person_count > 0 and c.lower_person_count > 0
            ]
            sufficiently_balanced = [
                c for c in full_candidates
                if min(c.upper_person_count, c.lower_person_count) >= 2
            ]
            if sufficiently_balanced:
                peak_person_sharpness = max(
                    c.person_sharpness for c in sufficiently_balanced
                )
                quality_floor = peak_person_sharpness * min(
                    1.0, max(0.0, Config.MOTION_FULL_ANCHOR_MIN_QUALITY)
                )
                legible_candidates = [
                    c for c in sufficiently_balanced
                    if c.person_sharpness >= quality_floor
                ]
                if legible_candidates:
                    def source_time(candidate: CandidateImage) -> float:
                        if candidate.frame_sec is not None:
                            return candidate.frame_sec
                        frame_number = _frame_number(candidate.path)
                        return float(max(0, (frame_number or 1) - 1))

                    best_detected = min(
                        legible_candidates,
                        key=lambda c: (
                            source_time(c),
                            -c.person_sharpness,
                            -min(c.upper_person_count, c.lower_person_count),
                            -(c.frame_sharpness or c.sharpness or 0.0),
                        ),
                    )
                    logger.info(
                        "janela=%s ancora_full_legibilidade "
                        "estrategia=primeiro_qualificado limiar=%.0f%% "
                        "nitidez_pico=%.2f elegiveis=%d",
                        window_id,
                        Config.MOTION_FULL_ANCHOR_MIN_QUALITY * 100.0,
                        peak_person_sharpness, len(legible_candidates),
                    )
        selected_count = estimated_distinct_people(best_detected)
        logger.info(
            "janela=%s ancora_escolhida modo=deteccao frame=%s "
            "pessoas_distintas_proxy=%d pessoas_brutas=%d "
            "por_camera=%d/%d nitidez_pessoa=%.2f nitidez_geral=%.2f",
            window_id, best_detected.path.name,
            selected_count, best_detected.person_count,
            best_detected.upper_person_count,
            best_detected.lower_person_count,
            best_detected.person_sharpness,
            best_detected.frame_sharpness or best_detected.sharpness or 0.0,
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
    recording_start: Optional[datetime] = None,
    recording_offset_seconds: float = 0.0,
    frame_timestamps: Optional[dict[Path, datetime]] = None,
    phase: str = "enhanced",
) -> tuple[Optional[Path], int]:
    raise_if_background_preempted(phase, "preparacao_video")
    if len(frame_paths) < 2:
        return None, 0
    # These are timed samples, including legitimate stationary subjects.
    # Removing near-duplicates at fixed FPS accelerates time and shortens
    # the elected blocks. H.264 already compresses repeated content.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pattern_dir = output_path.parent / "frames"
    pattern_dir.mkdir(parents=True, exist_ok=True)
    try:
        for idx, frame in enumerate(frame_paths, start=1):
            raise_if_background_preempted(phase, "preparo_frames_video")
            target = pattern_dir / f"frame_{idx:04d}.jpg"
            if cv2 is None or not Config.ACTIVE_CAMERA_ONLY:
                shutil.copy2(frame, target)
                continue
            image = cv2.imread(str(frame), cv2.IMREAD_COLOR)
            active = active_camera_image(image, camera_half)
            if active is image or active is None:
                shutil.copy2(frame, target)
                continue
            frame_time = (
                frame_timestamps.get(frame)
                if frame_timestamps is not None else None
            )
            if frame_time is not None or recording_start is not None:
                if frame_time is None and recording_start is not None:
                    offset = recording_offset_seconds + max(0, idx - 1) / max(
                        1, Config.MOTION_FPS
                    )
                    frame_time = recording_start + timedelta(seconds=offset)
                assert frame_time is not None
                label = frame_time.strftime("%H:%M:%S")
                font = cv2.FONT_HERSHEY_SIMPLEX
                # Referência temporal ampliada em 100% para continuar legível
                # no vídeo recortado e comprimido pelo WhatsApp.
                scale, thickness, padding = 1.4, 4, 20
                (text_width, text_height), baseline = cv2.getTextSize(
                    label, font, scale, thickness
                )
                x = max(padding, active.shape[1] - text_width - padding)
                y = max(text_height + padding, active.shape[0] - padding)
                cv2.rectangle(
                    active, (x - 5, y - text_height - 5),
                    (active.shape[1] - padding + 5, y + baseline + 5),
                    (0, 0, 0), -1,
                )
                cv2.putText(
                    active, label, (x, y), font, scale, (255, 255, 255),
                    thickness, cv2.LINE_AA,
                )
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
        crf_values = list(dict.fromkeys(
            min(30, max(0, int(Config.MOTION_VIDEO_CRF) + offset))
            for offset in (0, 2, 4, 6)
        ))
        for strat in strategies:
            for crf_value in crf_values:
                command = [
                    Config.FFMPEG_CMD, "-hide_banner", "-loglevel", "error",
                    "-framerate", str(Config.MOTION_FPS),
                    "-i", str(pattern_dir / "frame_%04d.jpg"),
                    *strat["codec"],
                    "-pix_fmt", "yuv420p",
                    "-vf", vf,
                    "-r", str(Config.MOTION_FPS), "-vsync", "cfr",
                    "-crf", str(crf_value),
                    "-preset", Config.MOTION_VIDEO_PRESET,
                    *strat["extra"],
                    "-movflags", "+faststart", "-y", str(output_path),
                ]
                try:
                    result = run_preemptible_subprocess(
                        command, stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=Config.MOTION_VIDEO_TIMEOUT, phase=phase,
                    )
                    if result.returncode != 0:
                        raise subprocess.CalledProcessError(
                            result.returncode, command, stderr=result.stderr,
                        )
                    if not _check_video_duration(output_path):
                        continue
                    output_size = output_path.stat().st_size
                    if output_size > Config.MOTION_VIDEO_MAX_BYTES:
                        logger.warning(
                            "MP4 acima do limite preventivo: %d bytes; "
                            "tentando compressão CRF=%d (%s)",
                            output_size, crf_value, strat["label"],
                        )
                        continue
                    duration = len(frame_paths) / Config.MOTION_FPS
                    logger.info(
                        "Vídeo gerado com sucesso (%s CRF=%d): %d frames, "
                        "%.1fs, %d bytes",
                        strat["label"], crf_value, len(frame_paths), duration,
                        output_size,
                    )
                    fix_permissions(output_path)
                    return output_path, len(frame_paths)
                except (OSError, subprocess.SubprocessError) as exc:
                    logger.warning(
                        "Estratégia %s CRF=%d falhou: %s",
                        strat["label"], crf_value, exc,
                    )
                    continue
        logger.error("Todas as estratégias de geração de vídeo falharam.")
        return None, 0
    except BackgroundPreempted:
        # A janela permanece pendente; o worker de fundo a reenfileira sem
        # tratar a cessão de prioridade como falha de geração.
        raise
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
    fps: Optional[int] = None,
) -> int:
    fps = max(1, int(fps if fps is not None else Config.MOTION_FPS))
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

    frame_index = {path: index for index, path in enumerate(frame_paths)}
    for candidate in candidates:
        if candidate.source != "frame" or candidate.origin_path is None:
            continue
        index = frame_index.get(candidate.path)
        if index is None:
            continue
        if candidate.person_count > 0 or candidate.animal_count > 0:
            activity.add(index)

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
        and candidate.path in frame_index
        and (candidate.person_count > 0 or candidate.animal_count > 0)
        for candidate in candidates
    ):
        desired = max(desired, min_frames + fps * 2)
    return min(limit, desired)


def _camera_views_overlap(path: Path) -> bool:
    """Detect whether the stacked cameras currently show the same scene."""
    if cv2 is None or not path.is_file():
        return False
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None or image.shape[0] < 4:
        return False
    midpoint = image.shape[0] // 2
    upper = cv2.resize(image[:midpoint], (640, 360))
    lower = cv2.resize(image[midpoint:], (640, 360))
    orb = cv2.ORB_create(nfeatures=400, fastThreshold=12)
    _, upper_descriptors = orb.detectAndCompute(upper, None)
    _, lower_descriptors = orb.detectAndCompute(lower, None)
    if upper_descriptors is None or lower_descriptors is None:
        return False
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = matcher.knnMatch(upper_descriptors, lower_descriptors, k=2)
    consistent = sum(1 for pair in matches if len(pair) == 2
                     and pair[0].distance < 0.75 * pair[1].distance)
    return consistent >= 18


def _dynamic_motion_interval(
    frame_paths: Sequence[Path],
    frame_times: Sequence[float],
    frame_durations: Sequence[float],
    anchor_index: int,
    candidates: Sequence[CandidateImage],
    salience_scores: Sequence[float],
    timeline_duration: float,
    window_id: Optional[int] = None,
    camera_half: str = "lower",
) -> tuple[float, float]:
    """Rank observed five-second blocks, then add relevant adjacent context.

    The rank uses actual YOLO observations. Time cells only estimate whether
    the targets and movement persist between nearby samples; they never make
    one observation count as several independent people detections.
    """
    if timeline_duration <= 0:
        return 0.0, 0.0
    block = min(float(Config.MOTION_BLOCK_SECONDS), timeline_duration)
    if not frame_paths or len(frame_paths) != len(frame_times):
        return 0.0, block
    anchor_time = frame_times[min(max(0, anchor_index), len(frame_paths) - 1)]
    path_times = dict(zip(frame_paths, frame_times))
    scored = sorted(
        (c for c in candidates if c.source == "frame" and c.path in path_times),
        key=lambda c: path_times[c.path],
    )
    times = [path_times[c.path] for c in scored]
    person_peak = max((c.person_sharpness for c in scored), default=0.0)
    image_peak = max((c.frame_sharpness or c.sharpness or 0.0 for c in scored
                      if c.animal_count or c.upper_vehicle_count or c.lower_vehicle_count
                      or c.category == "vehicle"), default=0.0)
    salience_peak = max(salience_scores, default=0.0)
    overlap_cache: dict[Path, bool] = {}
    local_motion_floors: list[float] = []
    for t in frame_times:
        lo, hi = bisect_left(frame_times, t - 2.5), bisect_left(frame_times, t + 2.5)
        local = sorted(salience_scores[lo:hi])
        typical = local[len(local) // 2] if local else 0.0
        local_motion_floors.append(max(Config.MOTION_BLOCK_LOCAL_FLOOR, typical * 0.15))

    def nearest(values: Sequence[float], t: float) -> int:
        right = bisect_left(values, t)
        return min((i for i in (right - 1, right) if 0 <= i < len(values)),
                   key=lambda i: abs(values[i] - t))

    def features(c: CandidateImage) -> tuple[int, int, int, int, int, float]:
        if camera_half in {"upper", "lower"}:
            view = camera_half
            people = getattr(c, view + "_person_count")
            animals = getattr(c, view + "_animal_count")
            vehicles = getattr(c, view + "_vehicle_count")
            clear_people = getattr(c, view + "_clear_person_count")
            clear_animals = getattr(c, view + "_clear_animal_count")
            if not (c.upper_person_count or c.lower_person_count):
                people = c.person_count
            if not (c.upper_animal_count or c.lower_animal_count):
                animals = c.animal_count
            if not (c.upper_vehicle_count or c.lower_vehicle_count):
                vehicles = max(vehicles, c.object_count if c.category == "vehicle" else 0)
        else:
            # At one instant, people visible in different camera images are
            # separate only when the views do not overlap. During pan, both
            # cameras can show the same people and must be de-duplicated.
            overlapping = False
            if c.upper_person_count and c.lower_person_count:
                if c.path not in overlap_cache:
                    overlap_cache[c.path] = _camera_views_overlap(c.path)
                overlapping = overlap_cache[c.path]
            combine = max if overlapping else lambda upper, lower: upper + lower
            people = (combine(c.upper_person_count, c.lower_person_count)
                      if c.upper_person_count or c.lower_person_count else c.person_count)
            animals = (combine(c.upper_animal_count, c.lower_animal_count)
                       if c.upper_animal_count or c.lower_animal_count else c.animal_count)
            vehicles = (c.upper_vehicle_count + c.lower_vehicle_count
                        if c.upper_vehicle_count or c.lower_vehicle_count else
                        c.object_count if c.category == "vehicle" else 0)
            clear_people = combine(c.upper_clear_person_count, c.lower_clear_person_count)
            clear_animals = combine(c.upper_clear_animal_count, c.lower_clear_animal_count)
        if not c.visibility_measured:
            # Legacy checkpoints and synthetic tests have no per-box clarity.
            # Use ROI sharpness without treating one bright outlier as a veto.
            p_quality = (math.log1p(c.person_sharpness) / math.log1p(person_peak)
                         if person_peak > 0 else 1.0)
            a_quality = (math.log1p(c.frame_sharpness or c.sharpness or 0.0)
                         / math.log1p(image_peak) if image_peak > 0 else 1.0)
            clear_people = people if p_quality >= Config.MOTION_EXTENSION_MIN_QUALITY else 0
            clear_animals = animals if a_quality >= Config.MOTION_EXTENSION_MIN_QUALITY else 0
        if c.cat_rescue:
            animals = clear_animals = 0
        return (max(0, clear_people), max(0, people), max(0, clear_animals),
                max(0, animals), max(0, vehicles), max(0.0, c.person_visible_area))

    sampled = [features(c) for c in scored]
    step = 0.25
    cells = int(math.ceil(timeline_duration / step))
    support, movement, crowd = ([0.0] for _ in range(3))
    positive_times: set[float] = set()
    for i in range(cells):
        t = min(timeline_duration, (i + 0.5) * step)
        frame_i = nearest(frame_times, t)
        salience = salience_scores[frame_i] if frame_i < len(salience_scores) else 0.0
        moving = salience >= local_motion_floors[frame_i]
        clear = many = False
        if times:
            ci = nearest(times, t)
            if abs(times[ci] - t) <= Config.MOTION_EVIDENCE_MAX_DISTANCE:
                clear_people, people, clear_animals, animals, vehicles, _ = sampled[ci]
                clear = bool(clear_people or clear_animals or vehicles)
                many = clear_people >= Config.MOTION_BLOCK_CROWD_COUNT
                if clear:
                    positive_times.add(times[ci])
        support.append(support[-1] + float(clear) * step)
        movement.append(movement[-1] + float(clear and moving) * step)
        crowd.append(crowd[-1] + float(many and moving) * step)

    def amount(prefix: list[float], start: float, duration: float) -> float:
        def at(t: float) -> float:
            position = min(float(cells), max(0.0, t / step))
            index = min(cells, int(position))
            return prefix[index] + ((position - index) * (prefix[index + 1] - prefix[index])
                                    if index < cells else 0.0)
        return at(start + duration) - at(start)

    evidence_times = sorted(positive_times)
    def block_metrics(start: float, require_crowd: bool = False) -> tuple[bool, str, float, float, int]:
        if start < -1e-6 or start >= timeline_duration:
            return False, "limite_video", 0.0, 0.0, 0
        effective_block = min(block, timeline_duration - start)
        if effective_block < block * 0.5:
            return False, "limite_video", 0.0, 0.0, 0
        local = evidence_times[
            bisect_left(evidence_times, start):
            bisect_left(evidence_times, start + effective_block)
        ]
        coverage = amount(support, start, effective_block) / effective_block
        motion = amount(movement, start, effective_block) / effective_block
        if len(local) >= 2:
            recurrent = local[-1] - local[0] >= min(1.5, block * 0.3)
        elif len(local) == 1:
            local_index = bisect_left(times, local[0])
            local_vehicle = (
                local_index < len(sampled) and sampled[local_index][4] > 0
            )
            if local_vehicle:
                nearby_vehicle_times = [
                    t for t, row in zip(times, sampled)
                    if row[4] > 0 and abs(t - local[0]) > 1e-6
                ]
                # A car can be visible in one clear YOLO frame, disappear
                # briefly in glare, then recur as it approaches. Accept that
                # single-frame block only when vehicle evidence returns in
                # the next 1.5 blocks; person/animal gaps keep the tighter rule.
                recurrent = any(
                    abs(t - local[0]) <= block * 1.5
                    for t in nearby_vehicle_times
                )
            else:
                nearby = [t for t in evidence_times
                          if t < start or t >= start + block]
                recurrent = any(
                    abs(t - local[0]) <= block + 1.0 for t in nearby
                )
        else:
            recurrent = False
        min_coverage = (Config.MOTION_BLOCK_MIN_COVERAGE if len(local) >= 2 else 0.30)
        min_movement = (Config.MOTION_BLOCK_MIN_MOVEMENT if len(local) >= 2 else 0.10)
        reason = (
            "amostras_insuficientes" if not local else
            "evidencia_isolada" if not recurrent else
            "cobertura_insuficiente" if coverage < min_coverage else
            "movimento_insuficiente" if motion < min_movement else
            "multidao_insuficiente" if require_crowd and amount(crowd, start, block) / block < 0.30 else
            "aprovado"
        )
        return reason == "aprovado", reason, coverage, motion, len(local)

    def qualified(start: float, require_crowd: bool = False) -> bool:
        return block_metrics(start, require_crowd)[0]

    def block_observations(start: float) -> list[tuple[int, int, int, int, int, float]]:
        first = bisect_left(times, start)
        last = bisect_left(times, start + block)
        return sampled[first:last]

    def salience_context(start: float, duration: Optional[float] = None) -> bool:
        """Allow a strong salience lead-in before a confirmed car approach."""
        context_duration = block if duration is None else max(0.0, duration)
        if (
            context_duration <= 0.0
            or start < -1e-6
            or start + context_duration > timeline_duration + 1e-6
        ):
            return False
        first = bisect_left(frame_times, start)
        last = bisect_left(frame_times, start + context_duration)
        values = list(salience_scores[first:last])
        if not values:
            return False
        baseline = max(0.02, salience_peak * 0.18)
        local_peak = max(values)
        local_mean = sum(values) / len(values)
        return (
            local_mean >= baseline
            and local_peak >= max(baseline * 1.5, salience_peak * 0.35)
        )

    @lru_cache(maxsize=None)
    def rank(start: float) -> tuple[float, ...]:
        observations = block_observations(start)
        if not observations:
            return (-1.0,) + (0.0,) * 7
        def strongest(index: int) -> float:
            values = sorted((row[index] for row in observations), reverse=True)[:2]
            return sum(values) / 2.0  # one strong frame cannot win alone
        clear_people = strongest(0)
        people = strongest(1)
        mixed = float(any(clear_p > 0 and clear_a > 0
                          for clear_p, _, clear_a, _, _, _ in observations))
        clear_animals = strongest(2)
        animals = strongest(3)
        vehicles = strongest(4)
        vehicle_candidates = [
            c for c in scored
            if start <= path_times[c.path] < start + block
            and features(c)[4] > 0
        ]
        vehicle_observations = [
            (times[index], row[4])
            for index, row in enumerate(sampled)
            if start <= times[index] < start + block and row[4] > 0
        ]
        if len(vehicle_observations) == 1:
            vehicle_time, vehicle_count = vehicle_observations[0]
            recurrence = any(
                row[4] > 0 and abs(times[index] - vehicle_time) <= block * 1.5
                for index, row in enumerate(sampled)
                if abs(times[index] - vehicle_time) > 1e-6
            )
            if recurrence:
                # Do not halve a strong approach frame against an adjacent
                # block's lower-confidence detections of the same vehicle.
                vehicles = float(vehicle_count)
        visibility = strongest(5)
        if vehicle_candidates and not (
            clear_people or people or clear_animals or animals
        ):
            # In car arrivals, a later, sharper view is a better core than an
            # earlier glare frame that happened to produce more boxes across
            # the two camera views. Counts remain a tie-break after clarity.
            vehicle_clarity = max(
                (
                    math.log1p(
                        c.frame_sharpness or c.sharpness or 0.0
                    ) / math.log1p(image_peak)
                    if image_peak > 0 else 0.0
                )
                for c in vehicle_candidates
            )
            visibility = max(visibility, vehicle_clarity)
        salience = sum(
            salience_scores[nearest(frame_times, start + (i + 0.5) * step)]
            if salience_scores else 0.0 for i in range(int(block / step))
        ) / max(1, int(block / step))
        if not (clear_people or people or clear_animals or animals or vehicles):
            salience = 0.0
        return (round(clear_people, 3), round(people, 3), mixed,
                round(clear_animals, 3), round(animals, 3),
                round(visibility, 5), round(vehicles, 3), round(salience, 5))

    max_start = max(0.0, timeline_duration - block)
    starts = sorted({min(max_start, i * step) for i in range(int(max_start / step) + 2)})
    viable = []
    for t in starts:
        observations = block_observations(t)
        confirmed_single_vehicle = (
            len(observations) == 1
            and observations[0][4] > 0
            and qualified(t)
        )
        if qualified(t) and (len(observations) >= 2 or confirmed_single_vehicle):
            viable.append(t)
    if not any(row[:5] != (0, 0, 0, 0, 0) for row in sampled):
        best_start = min(starts, key=lambda t: abs(t + block / 2 - anchor_time))
    else:
        pool = viable or starts
        best_evidence = max(rank(t)[:7] for t in pool)
        pool = [t for t in pool if rank(t)[:7] == best_evidence]
        if viable and best_evidence[0] >= 3.0:
            # A tracking camera may hide one person behind another near the
            # destination. Keep a later, persistent group if its observed
            # count is within one person of the peak and both neighbours are
            # relevant. This also puts the chosen block second in the cut.
            best_visibility = max(rank(t)[5] for t in viable)
            persistent = [t for t in viable if
                          rank(t)[0] >= best_evidence[0] - 1.0
                          and rank(t)[1] >= best_evidence[1] - 1.0
                          and rank(t)[5] >= max(0.02, best_visibility * 0.15)
                          and qualified(t - block) and qualified(t + block)]
            if persistent:
                late_edge = max(persistent)
                if late_edge > max(pool) + block:
                    late_pool = [t for t in persistent if t >= late_edge - 2 * block]
                    pool = [max(late_pool, key=lambda t: (
                        rank(t)[0], rank(t)[1], rank(t)[5],
                        min(rank(t - block)[0], rank(t + block)[0]),
                        rank(t - block)[0] + rank(t + block)[0],
                        -abs(t - late_edge + block / 2),
                    ))]
        nearest_anchor = min(abs(t + block / 2 - anchor_time) for t in pool)
        if nearest_anchor > block:
            # If the best subjects appear well after the salience anchor,
            # place their best block between useful neighbours when possible.
            best_context = max(int(qualified(t - block)) + int(qualified(t + block))
                               for t in pool)
            pool = [t for t in pool if int(qualified(t - block))
                    + int(qualified(t + block)) == best_context]
        else:
            pool = [t for t in pool if abs(t + block / 2 - anchor_time)
                    <= nearest_anchor + 0.75]
        best_start = max(
            pool,
            key=lambda t: (int(qualified(t - block)) + int(qualified(t + block)),
                           rank(t)[7], -abs(t + block / 2 - anchor_time)),
        )
    selected_rank = rank(best_start)
    logger.info("janela=%s melhor_bloco_5s inicio=%.2fs claros_pessoas=%.1f pessoas=%.1f "
                "mistura=%d claros_animais=%.1f animais=%.1f visibilidade=%.4f "
                "saliencia=%.4f",
                window_id, best_start, selected_rank[0], selected_rank[1],
                int(selected_rank[2]), selected_rank[3], selected_rank[4],
                selected_rank[5], selected_rank[7])
    for label, position in (("principal", best_start), ("anterior", best_start-block),
                            ("posterior", best_start+block)):
        ok, reason, coverage, motion, samples = block_metrics(position)
        logger.info("janela=%s bloco=%s inicio=%.2fs aprovado=%s motivo=%s "
                    "cobertura=%.0f%% movimento=%.0f%% amostras=%d",
                    window_id, label, position, ok, reason,
                    100*coverage, 100*motion, samples)

    start, duration = best_start, block
    core_ok = qualified(best_start)
    before = core_ok and qualified(best_start - block)
    after = core_ok and qualified(best_start + block)
    vehicle_in_core = any(
        row[4] > 0 for row in block_observations(best_start)
    )
    if (
        core_ok and not before and vehicle_in_core
        and after and salience_context(best_start - block)
    ):
        before = True
        logger.info(
            "janela=%s bloco_anterior_saliencia aceito motivo="
            "transicao_antes_de_veiculo inicio=%.2fs",
            window_id if window_id is not None else "?", best_start - block,
        )
    if before and duration + block <= Config.MOTION_BASE_MAX_SECONDS:
        start -= block
        duration += block
    else:
        before = False
    after_duration = min(
        block, max(0.0, timeline_duration - (best_start + block))
    )
    if after and duration + after_duration <= Config.MOTION_BASE_MAX_SECONDS:
        duration += after_duration
    else:
        after = False
    extra = (before and after and duration + block <= Config.MOTION_MAX_SECONDS
             and qualified(best_start, require_crowd=True)
             and qualified(best_start + 2 * block, require_crowd=True))
    if extra:
        duration += block
    pre_roll = min(
        max(0.0, float(Config.MOTION_BASE_MAX_SECONDS) - duration),
        max(0.0, start),
    )
    if (
        vehicle_in_core
        and before
        and after
        and pre_roll >= 1.0 / max(1, int(Config.MOTION_FPS))
        and salience_context(start - pre_roll, pre_roll)
    ):
        start -= pre_roll
        duration += pre_roll
        logger.info(
            "janela=%s corte_movimento pre_roll_saliencia aceito "
            "motivo=preenchimento_base_veiculo acrescido=%.1fs "
            "inicio=%.2fs duracao=%.1fs",
            window_id if window_id is not None else "?",
            pre_roll, start, duration,
        )
    logger.info(
        "janela=%s corte_movimento estrategia=blocos_5s inicio=%.2fs duracao=%.1fs "
        "melhor_bloco=%.2fs posicao=%d antes=%s depois=%s extra=%s "
        "cobertura_yolo=%.0f%% movimento_com_alvo=%.0f%%",
        window_id if window_id is not None else "?", start, duration,
        best_start, 2 if before else 1, before, after, extra,
        100 * amount(support, start, duration) / duration,
        100 * amount(movement, start, duration) / duration,
    )
    return max(0.0, start), min(duration, timeline_duration - start)


def _extend_interval_for_clear_people(
    start: float,
    duration: float,
    anchor_time: float,
    frame_paths: Sequence[Path],
    frame_times: Sequence[float],
    candidates: Sequence[CandidateImage],
    timeline_duration: float,
    log: bool = True,
    allow_anchor_at_start: bool = False,
) -> tuple[float, float]:
    """Extend a selected cut only across sustained, sharp people detections."""
    max_duration = min(
        float(Config.MOTION_MAX_SECONDS),
        max(0.0, timeline_duration - start),
    )
    if duration >= max_duration:
        return start, duration

    path_to_index = {path: index for index, path in enumerate(frame_paths)}
    person_candidates = [
        candidate for candidate in candidates
        if candidate.source == "frame"
        and candidate.path in path_to_index
        and candidate.person_count > 0
        and candidate.person_sharpness > 0.0
    ]
    peak_sharpness = max(
        (candidate.person_sharpness for candidate in person_candidates),
        default=0.0,
    )
    if peak_sharpness <= 0.0:
        return start, duration

    quality_floor = peak_sharpness * Config.MOTION_EXTENSION_MIN_QUALITY
    end = start + duration
    clear_times = [
        frame_times[path_to_index[candidate.path]]
        for candidate in person_candidates
        if candidate.person_sharpness >= quality_floor
    ]
    recent_before = [t for t in clear_times if end - 4.0 <= t < end]
    extension_budget = max(0.0, max_duration - duration)
    upcoming = [
        t for t in clear_times
        if end <= t <= end + extension_budget + 1e-6
    ]
    if not recent_before or not upcoming:
        return start, duration

    proposed_end = min(
        start + max_duration,
        max(upcoming) + 0.25,
    )
    proposed_duration = proposed_end - start
    anchor_fraction = (anchor_time - start) / proposed_duration if proposed_duration else 0.0
    min_anchor_fraction = 0.0 if allow_anchor_at_start else 0.18
    if (
        proposed_duration <= duration
        or not min_anchor_fraction <= anchor_fraction <= 0.82
    ):
        return start, duration

    if log:
        logger.info(
            "corte_movimento extensao_adaptativa duracao=%.1fs->%.1fs "
            "nitidez_pessoa_min=%.2f proporcao=%.0f%%",
            duration, proposed_duration, Config.MOTION_EXTENSION_MIN_QUALITY,
            100.0 * anchor_fraction,
        )
    return start, proposed_duration


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


def _extract_motion_interval_frames(
    video: Path,
    window_id: int,
    start_seconds: float,
    duration_seconds: float,
    phase: str,
) -> list[Path]:
    """Extrai somente o trecho vencedor na cadência do vídeo final."""
    raise_if_background_preempted(phase, "extracao_intervalo_video")
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", video.stem)
    frame_dir = Config.TMP_DIR / (
        f"motion_source_{safe_stem}_{window_id}_{time.time_ns()}"
    )
    frame_dir.mkdir(parents=True, exist_ok=True)
    fix_permissions(frame_dir, is_dir=True)
    fps = max(1, int(Config.MOTION_FPS))
    # Raw H.264 cannot seek reliably without decoding from its beginning, so
    # reaching a late clip interval can take substantially longer than the
    # duration of the selected frames themselves.
    decode_horizon = max(0.0, float(start_seconds)) + max(
        0.5, float(duration_seconds)
    )
    interval_timeout = min(
        float(Config.EXTRACT_ALL_TIMEOUT),
        max(
            float(Config.FRAME_EXTRACT_TIMEOUT),
            decode_horizon * 2.0 + 15.0,
        ),
    )
    command = [
        Config.FFMPEG_CMD, "-hide_banner", "-loglevel", "error",
        # H.264 cru não possui índice de seek confiável. Posicionar -ss após
        # a entrada decodifica até o ponto pedido; é barato para o pequeno
        # trecho escolhido e evita sucesso vazio do seek rápido.
        "-f", "h264", "-r", "10", "-i", str(video),
        "-ss", f"{max(0.0, start_seconds):.3f}",
        "-t", f"{max(0.5, duration_seconds):.3f}",
        "-vf", (
            f"fps={fps},scale='min(1920,iw)':'min(1080,ih)':"
            "force_original_aspect_ratio=decrease"
        ),
        "-vsync", "0", "-q:v", str(Config.MOTION_JPEG_QUALITY), "-y",
        str(frame_dir / "frame_%04d.jpg"),
    ]
    try:
        result = run_preemptible_subprocess(
            command, capture_output=True, text=True,
            timeout=interval_timeout, phase=phase,
            idle_output_dir=frame_dir,
            idle_timeout=_frame_output_idle_timeout(video),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # FFmpeg may write a complete set of JPEGs and then stall while
        # draining/closing a raw H.264 input. The subprocess wrapper kills it
        # after timeout, so inspect the stable output directory only after it
        # has exited and keep every image that passed our media validation.
        frames = sorted(
            (
                path for path in frame_dir.glob("*.jpg")
                if is_valid_image_file(path)
            ),
            key=_frame_sequence_key,
        )
        if frames:
            reason = (
                f"timeout>{interval_timeout:.0f}s"
                if isinstance(exc, subprocess.TimeoutExpired)
                else str(exc)
            )
            logger.warning(
                "janela=%s extracao_intervalo interrompida video=%s "
                "motivo=%s frames_validos=%d preservados=sim",
                window_id, video.name, reason, len(frames),
            )
            return frames
        logger.warning(
            "janela=%s extracao_intervalo falhou video=%s motivo=%s "
            "frames_validos=0",
            window_id, video.name, exc,
        )
        return []
    frames = sorted(
        (p for p in frame_dir.glob("*.jpg") if is_valid_image_file(p)),
        key=_frame_sequence_key,
    )
    if len(frames) >= 2:
        logger.info(
            "janela=%s extracao_intervalo video=%s inicio=%.1fs "
            "duracao=%.1fs fps=%d frames=%d",
            window_id, video.name, start_seconds, duration_seconds, fps,
            len(frames),
        )
        return frames
    logger.warning(
        "janela=%s extracao_intervalo insuficiente video=%s codigo=%s",
        window_id, video.name, result.returncode,
    )
    return []


def _frames_for_timeline_interval(
    frame_paths: Sequence[Path],
    frame_times: Sequence[float],
    start: float,
    duration: float,
) -> list[Path]:
    """Reuse the already-decoded analysis frames for an interval at that cadence.

    Short H.264 files are already decoded at the final 5 fps cadence. Seeking
    into raw H.264 a second time wastes CPU and may stall on damaged tails;
    selecting from the validated analysis timeline avoids that second decode.
    """
    if len(frame_paths) != len(frame_times):
        return []
    end = start + max(0.0, duration)
    return [
        path for path, timestamp in zip(frame_paths, frame_times)
        if start - 1e-6 <= timestamp < end - 1e-6
    ]


def _reuse_motion_interval_frames(
    frames: Sequence[Path], times: Sequence[float], start: float, duration: float,
) -> list[Path]:
    """Reuse decoded frames only if they cover every output-time slot.

    Explicit times matter: recovered sparse samples may carry nominal 5 FPS
    metadata even though their real spacing is more than one second.
    """
    if not frames or len(frames) != len(times):
        return []
    fps = max(1, int(Config.MOTION_FPS))
    selected: list[Path] = []
    for slot in range(int(round(duration * fps))):
        target = start + slot / fps
        right = bisect_left(times, target)
        valid = [i for i in (right - 1, right) if 0 <= i < len(times)]
        index = min(valid, key=lambda i: abs(times[i] - target))
        if abs(times[index] - target) > 0.55 / fps:
            return []
        selected.append(frames[index])
    return selected


def _motion_cut_evidence(
    candidates: Sequence[CandidateImage],
    anchor_group: Sequence[tuple[MotionFrameSet, Optional[MotionSpan]]],
    start: float,
    duration: float,
) -> tuple[
    tuple[CandidateImage, ...], tuple[tuple[datetime, datetime], ...]
]:
    """Reuse YOLO results and timestamps for sampled frames inside the cut."""
    parts = build_motion_timeline_parts(anchor_group)
    interval_end = start + max(0.0, duration)
    source_ranges: list[
        tuple[Path, float, float, datetime, datetime]
    ] = []
    for part in parts:
        part_start = part.timeline_start
        part_end = part_start + part.duration
        selected_start = max(start, part_start)
        selected_end = min(interval_end, part_end)
        if selected_end <= selected_start:
            continue
        source_start = (
            part.source_offset + selected_start - part.timeline_start
        )
        source_end = (
            part.source_offset + selected_end - part.timeline_start
        )
        absolute_start = part.span.start + timedelta(seconds=source_start)
        absolute_end = part.span.start + timedelta(seconds=source_end)
        source_ranges.append((
            part.frame_set.video_path.resolve(), source_start, source_end,
            absolute_start, absolute_end,
        ))

    selected: list[CandidateImage] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if (
            candidate.source != "frame"
            or candidate.origin_path is None
            or candidate.frame_sec is None
        ):
            continue
        video_path = candidate.origin_path.resolve()
        if any(
            video_path == source_path
            and source_start - 1e-6 <= candidate.frame_sec < source_end - 1e-6
            for source_path, source_start, source_end, _, _ in source_ranges
        ):
            key = candidate.path.resolve()
            if key not in seen:
                selected.append(candidate)
                seen.add(key)
    intervals = tuple(
        (absolute_start, absolute_end)
        for _, _, _, absolute_start, absolute_end in source_ranges
    )
    return tuple(selected), intervals


def build_motion_video(
    candidates: Sequence[CandidateImage],
    window_id: int,
    frame_sets: Sequence[MotionFrameSet],
    phase: str = "enhanced",
) -> Optional[MotionBuild]:
    raise_if_background_preempted(phase, "selecao_video")
    if not Config.ENHANCED_MOTION_ENABLED:
        return None
    salience_cache = _build_salience_cache(frame_sets)
    camera_half = _pick_camera_half(frame_sets, salience_cache)
    camera_half, decision_reason, decision_evidence, opposite_animal = (
        _select_motion_view_decision(candidates, camera_half)
    )
    if decision_reason == "animal_pessoa_angulos_opostos":
        assert decision_evidence is not None and opposite_animal is not None
        logger.info(
            "janela=%d modo_full motivo=animal_pessoa_angulos_opostos "
            "pessoa_frame=%s pessoas_superior=%d pessoas_inferior=%d "
            "animal_frame=%s animais_superior=%d animais_inferior=%d "
            "animal_score=%.3f ancora_prioridade=pessoa",
            window_id, decision_evidence.path.name,
            decision_evidence.upper_person_count,
            decision_evidence.lower_person_count,
            opposite_animal.path.name,
            opposite_animal.upper_animal_count,
            opposite_animal.lower_animal_count,
            float(opposite_animal.engine_score or 0.0),
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
    if decision_evidence is None:
        decision_evidence = anchor_candidate
    logger.info(
        "janela=%d camera_metade final=%s motivo=%s evidencia=%s "
        "pessoas_superior=%d animais_superior=%d "
        "pessoas_inferior=%d animais_inferior=%d ancora=%s",
        window_id, camera_half, decision_reason, decision_evidence.path.name,
        decision_evidence.upper_person_count,
        decision_evidence.upper_animal_count,
        decision_evidence.lower_person_count,
        decision_evidence.lower_animal_count,
        anchor_candidate.path.name,
    )
    frame_paths: list[Path] = []
    frame_paths_owned = False
    padding_frame_path: Optional[Path] = None
    frame_timestamps: Optional[dict[Path, datetime]] = None
    recording_start: Optional[datetime] = None
    interval_start = 0.0
    desired_seconds = 0.0
    anchor_video = anchor_candidate.origin_path
    groups = group_motion_frame_sets(frame_sets)
    logger.info(
        "janela=%d agrupamento_videos=%s",
        window_id,
        " | ".join(
            f"camera={next((span.camera_id for _, span in group if span), '?')} "
            f"partes={len(group)} "
            f"duracao={sum((span.end - span.start).total_seconds() for _, span in group if span):.1f}s"
            for group in groups
        ) or "nenhum",
    )
    anchor_group = next(
        (
            group for group in groups
            if anchor_video is not None and any(
                frame_set.video_path.resolve() == anchor_video.resolve()
                for frame_set, _ in group
            )
        ),
        [],
    )
    incomplete = [
        frame_set.video_path.name
        for frame_set, _ in anchor_group
        if not frame_set.coverage_complete
    ]
    if incomplete:
        logger.warning(
            "janela=%d movimento_nao_gerado motivo=cobertura_frames_incompleta "
            "videos=%s",
            window_id, ",".join(incomplete),
        )
        return None
    if len(anchor_group) > 1:
        parts = build_motion_timeline_parts(
            anchor_group
        )
        timeline_frames: list[Path] = []
        timeline_times: list[float] = []
        timeline_durations: list[float] = []
        timeline_salience: list[float] = []
        for part in parts:
            fps = max(1, int(part.frame_set.analysis_fps))
            timeline_frames.extend(part.frames)
            timeline_times.extend(
                part.timeline_start + source_time
                for source_time in part.frame_times
            )
            timeline_durations.extend([1.0 / fps] * len(part.frames))
        # Recompute salience over the concatenated sequence so background
        # estimation and peak selection span file boundaries as one movement.
        timeline_salience = _salience_scores_for_half(
            timeline_frames, camera_half
        )
        anchor_index = next(
            (
                idx for idx, path in enumerate(timeline_frames)
                if path == anchor
            ),
            -1,
        )
        if len(timeline_frames) < 2 or anchor_index < 0:
            logger.info(
                "janela=%s movimento_nao_gerado "
                "motivo=timeline_concatenada_insuficiente frames=%d ancora=%d",
                window_id, len(timeline_frames), anchor_index,
            )
            return None
        timeline_duration = sum(part.duration for part in parts)
        interval_start, desired_seconds = _dynamic_motion_interval(
            timeline_frames, timeline_times, timeline_durations,
            anchor_index, candidates, timeline_salience, timeline_duration,
            window_id, camera_half,
        )
        interval_end = interval_start + desired_seconds
        frame_timestamps = {}
        target_fps = max(1, int(Config.MOTION_FPS))
        reusable_frames = _reuse_motion_interval_frames(
            timeline_frames, timeline_times, interval_start, desired_seconds,
        )
        for part in parts:
            for path, source_time in zip(part.frames, part.frame_times):
                frame_timestamps[path] = part.span.start + timedelta(
                    seconds=part.source_offset + source_time
                )
        if reusable_frames:
            frame_paths = reusable_frames
            logger.info(
                "janela=%s extracao_intervalo fonte=frames_analise "
                "fps=%d frames=%d esperados_aprox=%d duracao_real=%.1fs",
                window_id, target_fps, len(frame_paths),
                int(round(desired_seconds * target_fps)),
                len(frame_paths) / target_fps,
            )
        else:
            for part in parts:
                part_start = part.timeline_start
                part_end = part_start + part.duration
                selected_start = max(interval_start, part_start)
                selected_end = min(interval_end, part_end)
                if selected_end <= selected_start:
                    continue
                local_start = (
                    part.source_offset + selected_start - part_start
                )
                selected_duration = selected_end - selected_start
                extracted = _extract_motion_interval_frames(
                    part.frame_set.video_path, window_id, local_start,
                    selected_duration, phase,
                )
                frame_paths.extend(extracted)
                frame_paths_owned = frame_paths_owned or bool(extracted)
                for idx, path in enumerate(extracted):
                    frame_timestamps[path] = part.span.start + timedelta(
                        seconds=local_start + idx / target_fps
                    )
        logger.info(
            "janela=%s timeline_movimento concatenada cameras=%s "
            "partes=%d duracao=%.1fs corte_inicio=%.1fs "
            "corte_duracao=%.1fs frames=%d",
            window_id,
            ",".join(sorted({
                span.camera_id or "?" for _, span in anchor_group
                if span is not None
            })),
            len(parts), timeline_duration, interval_start, desired_seconds,
            len(frame_paths),
        )
    else:
        anchor_frame_set = next(
            (
                frame_set for frame_set in frame_sets
                if anchor_video is not None
                and frame_set.video_path.resolve() == anchor_video.resolve()
            ),
            None,
        )
        all_frames = list(anchor_frame_set.frames) if anchor_frame_set else []
        analysis_fps = (
            max(1, int(anchor_frame_set.analysis_fps))
            if anchor_frame_set else max(1, int(Config.ANALYSIS_FPS))
        )
        if len(all_frames) < 2:
            logger.info(
                "janela=%s movimento_nao_gerado motivo=frames_insuficientes "
                "total=%d",
                window_id, len(all_frames),
            )
            return None
        anchor_num = _frame_number(anchor)
        if anchor_num is None:
            return None
        timeline_salience = _salience_scores_for_half(
            all_frames,
            camera_half,
            salience_cache.get(anchor_video.resolve(), {})
            if anchor_video is not None else None,
        )
        source_times = (
            list(anchor_frame_set.frame_times)
            if anchor_frame_set and len(anchor_frame_set.frame_times) == len(all_frames)
            else [idx / analysis_fps for idx in range(len(all_frames))]
        )
        available_duration = min(
            _declared_video_duration(anchor_video) if anchor_video is not None
            else len(all_frames) / analysis_fps,
            source_times[-1] + 1.0 / analysis_fps,
        )
        timeline_duration = available_duration
        interval_start, desired_seconds = _dynamic_motion_interval(
            all_frames,
            source_times,
            [1.0 / analysis_fps] * len(all_frames),
            min(max(anchor_num - 1, 0), len(all_frames) - 1),
            candidates,
            timeline_salience,
            available_duration,
            window_id,
            camera_half,
        )
        # Once the clip interval is known, keep its reported anchor inside
        # that interval. Otherwise a high-count frame from the beginning can
        # be reported as the anchor even when the selected clip is the late
        # approach at the active-camera end of the recording.
        interval_candidates = [
            candidate for candidate in candidates
            if candidate.source == "frame"
            and candidate.origin_path is not None
            and anchor_video is not None
            and candidate.origin_path.resolve() == anchor_video.resolve()
            and candidate.frame_sec is not None
            and interval_start - 1e-6 <= candidate.frame_sec
            <= interval_start + desired_seconds + 1e-6
        ]
        interval_anchor = pick_motion_anchor(
            interval_candidates, window_id, camera_half=camera_half
        )
        if interval_anchor is not None:
            anchor = interval_anchor
            anchor_candidate = next(
                c for c in interval_candidates if c.path == interval_anchor
            )
        frame_paths = _reuse_motion_interval_frames(
            all_frames, source_times, interval_start, desired_seconds,
        )
        if frame_paths:
            logger.info(
                "janela=%s extracao_intervalo fonte=frames_analise frames=%d duracao=%.1fs",
                window_id, len(frame_paths), desired_seconds,
            )
        else:
            frame_paths = _extract_motion_interval_frames(
                anchor_video, window_id, interval_start,
                desired_seconds, phase,
            )
            frame_paths_owned = bool(frame_paths)
        span = extract_motion_span(anchor_video)
        recording_start = span.start if span is not None else None
    target_fps = max(1, int(Config.MOTION_FPS))
    expected_frames = int(math.ceil(desired_seconds * target_fps - 1e-9))
    interval_reaches_timeline_end = (
        interval_start + desired_seconds >= timeline_duration - 1e-6
    )
    if (
        frame_paths
        and interval_reaches_timeline_end
        and expected_frames > 0
        and len(frame_paths) == expected_frames - 1
    ):
        padding_frame_path = Config.TMP_DIR / (
            f"motion_tail_pad_{window_id}_{time.time_ns()}.jpg"
        )
        try:
            shutil.copy2(frame_paths[-1], padding_frame_path)
            previous_frame = frame_paths[-1]
            frame_paths.append(padding_frame_path)
            if frame_timestamps is not None:
                previous_time = frame_timestamps.get(previous_frame)
                if previous_time is not None:
                    frame_timestamps[padding_frame_path] = previous_time + timedelta(
                        seconds=1.0 / target_fps
                    )
            logger.info(
                "janela=%s extracao_intervalo ultimo_quadro_repetido "
                "motivo=duracao_alvo_na_borda quadros=%d duracao=%.1fs",
                window_id, expected_frames,
                expected_frames / target_fps,
            )
        except OSError as exc:
            padding_frame_path.unlink(missing_ok=True)
            padding_frame_path = None
            logger.warning(
                "janela=%s extracao_intervalo padding_final_falhou motivo=%s",
                window_id, exc,
            )
    if len(frame_paths) < max(2, math.ceil(desired_seconds * Config.MOTION_FPS * 0.95)):
        logger.info(
            "janela=%s movimento_nao_gerado "
            "motivo=frames_selecionados_insuficientes",
            window_id,
        )
        if frame_paths_owned:
            cleanup_attempt_temp(frame_paths)
        if padding_frame_path is not None:
            padding_frame_path.unlink(missing_ok=True)
        return None
    cut_candidates, cut_intervals = _motion_cut_evidence(
        candidates, anchor_group, interval_start, desired_seconds,
    )
    logger.info(
        "janela=%s resultados_yolo_no_corte=%d intervalos=%d",
        window_id, len(cut_candidates), len(cut_intervals),
    )
    # Resolve the final view from the selected interval, not unrelated objects
    # elsewhere in the event. Include vehicles in the view decision too.
    if cut_candidates:
        cut_half = _pick_camera_half([
            MotionFrameSet(anchor_video or Path(""), tuple(frame_paths), Config.MOTION_FPS)
        ])
        camera_half, decision_reason, cut_evidence, _ = _select_motion_view_decision(
            cut_candidates, cut_half,
        )
        decision_evidence = cut_evidence or anchor_candidate
        logger.info(
            "janela=%s camera_metade_no_corte=%s motivo=%s",
            window_id, camera_half, decision_reason,
        )
    output_dir = Config.TMP_DIR / f"motion_{window_id}_{time.time_ns()}"
    output = output_dir / f"motion_{window_id}.mp4"
    try:
        video, final_frame_count = create_motion_mp4(
            frame_paths, output, camera_half,
            recording_start=recording_start,
            recording_offset_seconds=interval_start,
            frame_timestamps=frame_timestamps,
            phase=phase,
        )
    finally:
        # Só remover imagens temporárias da extração de montagem. Quando o
        # trecho reutiliza a análise a 5 fps, frame_paths aponta para os
        # próprios frames do worker e não pode ser limpo aqui.
        if frame_paths_owned:
            cleanup_attempt_temp(frame_paths)
        if padding_frame_path is not None:
            padding_frame_path.unlink(missing_ok=True)
    if video:
        logger.info(
            "janela=%d movimento mp4 gerado frames=%d fps=%d "
            "duracao_aprox=%.1fs arquivo=%s centro=%s",
            window_id, final_frame_count, Config.MOTION_FPS,
            final_frame_count / Config.MOTION_FPS, video.name, anchor.name,
        )
        return MotionBuild(
            path=video,
            frame_count=final_frame_count,
            camera_half=camera_half,
            reason=decision_reason,
            evidence=decision_evidence,
            cut_candidates=cut_candidates,
            cut_intervals=cut_intervals,
        )
    return None


def analysis_frame_rate(video: Path) -> int:
    """Escolhe a resolução temporal da análise sem afetar o MP4 final."""
    span = extract_motion_span(video)
    if span is not None:
        duration = max(0.0, (span.end - span.start).total_seconds())
    else:
        # Testes manuais podem ficar fora de ``AAAA-MM-DD``; o nome ainda
        # contém início/fim suficientes para decidir a cadência corretamente.
        match = MOTION_FILE_RE.search(video.name)
        if match is None:
            return max(1, int(Config.ANALYSIS_FPS))
        try:
            start = datetime.strptime(match.group("start"), "%H.%M.%S")
            end = datetime.strptime(match.group("end"), "%H.%M.%S")
            duration = (end - start).total_seconds()
            if duration < 0:
                duration += 86400
        except (TypeError, ValueError):
            return max(1, int(Config.ANALYSIS_FPS))
    if duration <= max(0, int(Config.ANALYSIS_HIGH_FPS_MAX_SECONDS)):
        return max(1, int(Config.ANALYSIS_HIGH_FPS))
    if duration <= max(0, int(Config.ANALYSIS_MEDIUM_FPS_MAX_SECONDS)):
        return max(1, int(Config.ANALYSIS_MEDIUM_FPS))
    return max(1, int(Config.ANALYSIS_FPS))


def _declared_video_duration(video: Path) -> float:
    span = extract_motion_span(video, fallback_date=datetime(2000, 1, 1))
    if span is None:
        return 0.0
    return max(0.0, (span.end - span.start).total_seconds())


def _frame_output_idle_timeout(video: Path) -> float:
    """Do not mistake a long raw-H.264 decode pause for completed extraction.

    The extractor itself has a duration-aware stall detector. Keep the outer
    file-growth watchdog a little more patient than that detector so it can
    report partial output deliberately instead of being killed first.
    """
    duration = _declared_video_duration(video)
    internal_stall = min(30.0, max(8.0, 8.0 + duration * 0.05))
    # Leave time for extract_frames.py to let its own FFmpeg watchdog stop the
    # decoder and return a classified partial result before the outer process
    # watchdog intervenes. A two second margin was too tight on raw H.264.
    return max(float(Config.FRAME_OUTPUT_IDLE_TIMEOUT), internal_stall + 8.0)


def _ensure_analysis_frame_coverage(
    video: Path,
    frames: Sequence[Path],
    analysis_fps: int,
    window_id: int,
    phase: str,
) -> tuple[tuple[Path, ...], tuple[float, ...], bool]:
    """Recover sparse, early-only extraction with a bounded full-span sample.

    Normal extraction retains its configured cadence. Recover short coverage
    and explicitly stalled/timeout prefixes with <=24 evenly distributed
    samples, so salience/YOLO can see the clip's terminal interval too.
    """
    fps = max(1, int(analysis_fps))
    duration = _declared_video_duration(video)
    original = tuple(frames)
    original_times = tuple(index / fps for index in range(len(original)))
    if duration <= 0.0:
        return original, original_times, True
    partial_marker = (
        original[0].parent / ".extract_partial"
        if original else None
    )
    extraction_interrupted = bool(
        partial_marker is not None and partial_marker.is_file()
    )
    expected = max(1, int(math.ceil(duration * fps)))
    original_coverage = (
        min(duration, (original_times[-1] + 1.0 / fps)) / duration
        if original_times else 0.0
    )
    missing_seconds = max(0.0, duration * (1.0 - original_coverage))
    # Raw H.264 often ends a few frames short of its filename duration.
    # Tolerate <=1 s/5%, or <=2 s/3% for longer streams. Missing time is
    # never synthesized; the cut remains bounded by actual decoded frames.
    if ((original_coverage >= 0.95 and missing_seconds <= 1.0)
            or (original_coverage >= 0.97 and missing_seconds <= 2.0 + 1e-6)):
        if extraction_interrupted:
            logger.info(
                "janela=%s cobertura_cauda_tolerada video=%s cobertura=%.0f%% "
                "faltante=%.2fs reextracao=False",
                window_id, video.name, original_coverage * 100, missing_seconds,
            )
        return original, original_times, True
    if original_coverage >= 0.80 and not extraction_interrupted:
        return original, original_times, True

    if extraction_interrupted:
        logger.info(
            "janela=%s cobertura_recuperacao video=%s "
            "motivo=extracao_interrompida cobertura_prefixo=%.0f%%",
            window_id, video.name, original_coverage * 100.0,
        )

    sample_fps = min(float(fps), 24.0 / duration)
    sample_fps = max(0.000001, sample_fps)
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", video.stem)
    sample_dir = Config.TMP_DIR / (
        f"coverage_{safe_stem}_{window_id}_{time.time_ns()}_"
        f"{threading.get_ident()}"
    )
    sample_dir.mkdir(parents=True, exist_ok=True)
    fix_permissions(sample_dir, is_dir=True)
    command = [
        sys.executable, str(Config.EXTRACT_FRAMES_SCRIPT),
        str(video), str(sample_dir), "0", "--fps", f"{sample_fps:.6f}",
    ]
    try:
        result = run_preemptible_subprocess(
            command, capture_output=True, text=True,
            timeout=Config.EXTRACT_ALL_TIMEOUT, phase=phase,
            idle_output_dir=sample_dir,
            idle_timeout=_frame_output_idle_timeout(video),
        )
        sampled = tuple(sorted(
            (p for p in sample_dir.glob("*.jpg") if is_valid_image_file(p)),
            key=_frame_sequence_key,
        ))
    except BackgroundPreempted:
        shutil.rmtree(sample_dir, ignore_errors=True)
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "janela=%s amostra_cobertura falhou video=%s motivo=%s",
            window_id, video.name, exc,
        )
        shutil.rmtree(sample_dir, ignore_errors=True)
        return original, original_times, False

    expected_sampled = max(1, int(math.floor(duration * sample_fps)))
    coverage_ratio = len(sampled) / expected_sampled
    sampled_times = tuple(index / sample_fps for index in range(len(sampled)))
    sampled_coverage = (
        min(duration, sampled_times[-1] + 1.0 / sample_fps) / duration
        if sampled_times else 0.0
    )
    if len(sampled) < 2 or coverage_ratio < 0.70 or sampled_coverage < 0.80:
        logger.warning(
            "janela=%s cobertura_frames_insuficiente video=%s "
            "primarios=%d/%d amostra=%d/%d cobertura=%.0f%%",
            window_id, video.name, len(original), expected,
            len(sampled), expected_sampled, sampled_coverage * 100.0,
        )
        shutil.rmtree(sample_dir, ignore_errors=True)
        return original, original_times, False

    logger.info(
        "janela=%s amostra_cobertura video=%s primarios=%d/%d "
        "frames=%d fps=%.3f cobertura=%.0f%%",
        window_id, video.name, len(original), expected, len(sampled),
        sample_fps, min(100.0, sampled_coverage * 100.0),
    )
    # The sparse full-span set replaces a truncated prefix; mixing both would
    # bias temporal salience and duplicate the start of the recording.
    return sampled, sampled_times, True


def _preparation_dir(window_id: int, phase: str) -> Path:
    if phase not in {"enhanced", "late", "late_rebuild"}:
        raise ValueError(f"Fase inválida: {phase}")
    return Config.TMP_DIR / "preparation" / f"{int(window_id)}_{phase}"


def _write_preparation(manifest: Path, payload: dict) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    fix_permissions(manifest.parent, is_dir=True)
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    fix_permissions(temporary)
    os.replace(temporary, manifest)


def _preparation_paths(payload: dict) -> list[Path]:
    return [Path(p) for key in ("raw", "frames") for p in payload.get(key, [])]


def _has_preparation(window_id: int, phase: str) -> bool:
    for manifest in _preparation_dir(window_id, phase).glob("*.json"):
        try:
            if time.time() - manifest.stat().st_mtime > Config.TMP_FILE_TTL:
                continue
            paths = _preparation_paths(json.loads(manifest.read_text()))
            if paths and all(p.is_file() for p in paths):
                return True
        except (OSError, ValueError, TypeError):
            continue
    return False


def _clear_preparation(window_id: int, phase: str) -> None:
    root = _preparation_dir(window_id, phase)
    for manifest in root.glob("*.json"):
        try:
            cleanup_attempt_temp(_preparation_paths(json.loads(manifest.read_text())))
        except (OSError, ValueError, TypeError):
            pass
    shutil.rmtree(root, ignore_errors=True)


def _prepare_video_frames(video: Path, window_id: int, phase: str) -> tuple[tuple[Path, ...], tuple[float, ...], bool]:
    """Persist each completed preparation stage before the next preemption point.

    Full source hash invalidates reuse even for same-size/same-mtime edits.
    An interrupted decoder is never treated as a completed extraction.
    """
    raise_if_background_preempted(phase, "preparacao_video")
    key = hashlib.sha256(str(video.resolve()).encode()).hexdigest()[:24]
    manifest = _preparation_dir(window_id, phase) / f"{key}.json"
    signature = [list(row) for row in _analysis_input_signature([video], label=f"prep:{window_id}:{phase}")]
    fps = analysis_frame_rate(video)
    payload: dict = {}
    try:
        payload = json.loads(manifest.read_text())
        if payload.get("version") != 1 or payload.get("signature") != signature or payload.get("fps") != fps:
            cleanup_attempt_temp(_preparation_paths(payload))
            payload = {}
    except (OSError, ValueError, TypeError):
        payload = {}
    raw = tuple(Path(p) for p in payload.get("raw", []))
    frames = tuple(Path(p) for p in payload.get("frames", []))
    if frames and all(is_valid_image_file(p) for p in frames):
        times = tuple(float(t) for t in payload.get("times", []))
        if len(times) == len(frames):
            logger.info("janela=%s preparacao_reutilizada fase=%s video=%s frames=%d etapa=cobertura",
                        window_id, phase, video.name, len(frames))
            return frames, times, bool(payload.get("complete", False))
    if not raw or not all(is_valid_image_file(p) for p in raw):
        raw = tuple(_extract_all_frames(video, window_id, phase))
        payload = {"version": 1, "signature": signature, "fps": fps, "raw": [str(p) for p in raw]}
        if raw:
            _write_preparation(manifest, payload)
    else:
        logger.info("janela=%s preparacao_reutilizada fase=%s video=%s frames=%d etapa=extracao",
                    window_id, phase, video.name, len(raw))
    frames, times, complete = _ensure_analysis_frame_coverage(video, raw, fps, window_id, phase)
    if frames:
        payload.update(frames=[str(p) for p in frames], times=list(times), complete=complete)
        _write_preparation(manifest, payload)
    return tuple(frames), tuple(times), complete


def _extract_all_frames(
    video: Path, window_id: int, phase: str = "enhanced"
) -> list[Path]:
    raise_if_background_preempted(phase, "extracao_frames")
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
    analysis_fps = analysis_frame_rate(video)
    command = [
        sys.executable, str(Config.EXTRACT_FRAMES_SCRIPT),
        str(video), str(frame_dir), "0",
        "--fps", str(analysis_fps),
    ]
    extraction_started = time.monotonic()
    try:
        result = run_preemptible_subprocess(
            command, capture_output=True, text=True,
            timeout=Config.EXTRACT_ALL_TIMEOUT, phase=phase,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "Janela %s extract_frames erro em %s duração=%.1fs: %s",
            window_id, video.name,
            time.monotonic() - extraction_started, exc,
        )
        return []
    extractor_output = "\n".join(
        part for part in (result.stdout or "", result.stderr or "") if part
    )
    # extract_frames may report an accepted early stop in its summary on
    # stdout, while a rejected partial extraction is usually logged to stderr.
    # Both cases can leave >=80% prefix coverage and still omit the clip tail.
    stopped_for_stall_or_timeout = bool(re.search(
        r"(?:Stopped early\s*\((?:stalled|timeout)|"
        r"Stopped early:\s*(?:ffmpeg stalled|absolute timeout reached)|"
        r"Early stop accepted\s*\((?:stalled|timeout))",
        extractor_output,
        flags=re.IGNORECASE,
    ))
    if stopped_for_stall_or_timeout:
        try:
            (frame_dir / ".extract_partial").touch(exist_ok=True)
        except OSError:
            logger.warning(
                "Janela %s não conseguiu marcar extração parcial em %s",
                window_id, video.name,
            )
    generated = sorted(
        (
            p for p in frame_dir.glob("*.jpg")
            if is_valid_image_file(p)
        ),
        key=_frame_sequence_key,
    )
    if generated:
        if result.returncode != 0:
            logger.info(
                "Janela %s extract_frames concluiu com ressalva código=%s "
                "para %s, preservando %d frame(s) válidos duração=%.1fs saída=%s",
                window_id, result.returncode, video.name, len(generated),
                time.monotonic() - extraction_started,
                extractor_output[-500:].replace("\n", " "),
            )
        else:
            logger.info(
                "Janela %s extração video=%s frames_validos=%d fps_analise=%d "
                "duração=%.1fs",
                window_id, video.name, len(generated), analysis_fps,
                time.monotonic() - extraction_started,
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
            "Janela %s extract_frames falhou em %s duração=%.1fs stderr=%s",
            window_id, video.name, time.monotonic() - extraction_started,
            (result.stderr or "")[-500:],
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
    salience_cache: Optional[dict[Path, dict[str, list[float]]]] = None,
    camera_half: Optional[str] = None,
    include_terminal: bool = True,
) -> list[CandidateImage]:
    """Amostra frames por tempo, priorizando movimento antes do YOLO.

    Cada vídeo conserva representação ao longo da duração. Dentro de cada
    trecho temporal, a saliência decide primeiro; a nitidez desempata e é o
    fallback quando a cena não tem atividade relevante.
    """
    if total_budget <= 0:
        return []
    per_video: dict[Path, list[CandidateImage]] = {}
    for c in frame_part:
        if c.origin_path:
            per_video.setdefault(c.origin_path.resolve(), []).append(c)
    total_frames = sum(len(group) for group in per_video.values())
    if total_frames == 0:
        return []
    # Allocate by seconds, never by decoded FPS. Cap at available frames and
    # redistribute the remainder without truncating the last video's share.
    durations = {
        video.resolve(): max(0.001, _declared_video_duration(video) or
            max((c.frame_sec or 0.0 for c in per_video.get(video.resolve(), [])), default=0.0)
            or float(len(per_video.get(video.resolve(), []))))
        for video in videos if video.resolve() in per_video
    }
    shares = dict.fromkeys(durations, 1 if total_budget >= len(durations) else 0)
    for _ in range(min(total_budget, total_frames) - sum(shares.values())):
        available = [v for v in shares if shares[v] < len(per_video[v])]
        if not available:
            break
        video_key = max(available, key=lambda v: durations[v] / (shares[v] + 1))
        shares[video_key] += 1
    selected: list[CandidateImage] = []
    salience_cache = salience_cache or {}
    salience_min = max(0.0, float(Config.YOLO_SALIENCE_MIN))
    salience_weight = min(1.0, max(0.0, float(Config.YOLO_SALIENCE_WEIGHT)))
    for video in videos:
        group = per_video.get(video.resolve())
        if not group:
            continue
        group.sort(key=lambda c: (
            _frame_number(c.path) is None,
            _frame_number(c.path) or 0,
            c.path.name,
        ))
        share = shares.get(video.resolve(), 0)
        if share <= 0:
            continue
        if share >= len(group):
            selected.extend(group)
            continue
        def salience_for(candidate: CandidateImage) -> float:
            frame_number = _frame_number(candidate.path)
            cache = salience_cache.get(video.resolve(), {})
            if frame_number is None:
                return 0.0
            index = frame_number - 1
            upper = cache.get("upper", [])
            lower = cache.get("lower", [])
            if camera_half in {"upper", "lower"}:
                selected_scores = cache.get(camera_half, [])
                values = (
                    [selected_scores[index]]
                    if 0 <= index < len(selected_scores) else []
                )
            else:
                values = [
                    scores[index] for scores in (upper, lower)
                    if 0 <= index < len(scores)
                ]
            return max(values, default=0.0)

        def salience_rise_for(candidate: CandidateImage) -> float:
            frame_number = _frame_number(candidate.path)
            cache = salience_cache.get(video.resolve(), {})
            if frame_number is None:
                return 0.0
            index = frame_number - 1
            halves = (
                (camera_half,)
                if camera_half in {"upper", "lower"}
                else ("upper", "lower")
            )
            rises: list[float] = []
            for half in halves:
                scores = cache.get(half, [])
                if 0 < index < len(scores):
                    rises.append(max(0.0, scores[index] - scores[index - 1]))
            return max(rises, default=0.0)

        salience_by_path = {candidate.path: salience_for(candidate) for candidate in group}
        salience_rise_by_path = {
            candidate.path: salience_rise_for(candidate) for candidate in group
        }
        sharpness_by_path = {
            candidate.path: (
                compute_sharpness(candidate.path, camera_half=camera_half) or 0.0
            )
            for candidate in group
        }
        peak_salience = max(salience_by_path.values(), default=0.0)
        peak_salience_rise = max(salience_rise_by_path.values(), default=0.0)
        peak_sharpness = max(sharpness_by_path.values(), default=0.0)
        use_salience = peak_salience >= salience_min
        use_salience_rise = peak_salience_rise >= max(
            0.02, salience_min * 2.0
        )
        transition_salience_by_path = {
            candidate.path: (
                salience_by_path[candidate.path]
                + (0.5 * salience_rise_by_path[candidate.path]
                   if use_salience_rise else 0.0)
            )
            for candidate in group
        }
        peak_transition_salience = max(
            transition_salience_by_path.values(), default=0.0
        )
        segment_size = len(group) / share
        video_selected: list[CandidateImage] = []
        for i in range(share):
            lo = int(i * segment_size)
            hi = (
                int((i + 1) * segment_size)
                if i < share - 1
                else len(group)
            )
            hi = max(hi, lo + 1)
            segment = group[lo:hi]
            if use_salience:
                best = max(
                    segment,
                    key=lambda c: (
                        salience_weight * (
                            transition_salience_by_path[c.path]
                            / max(peak_transition_salience, 1e-9)
                        )
                        + (1.0 - salience_weight) * (
                            sharpness_by_path[c.path] / max(peak_sharpness, 1e-9)
                        ),
                        salience_by_path[c.path],
                        sharpness_by_path[c.path],
                    ),
                )
            else:
                best = max(segment, key=lambda c: sharpness_by_path[c.path])
            video_selected.append(best)
        # Grupos costumam surgir perto do fim do clipe. Reserve um ponto de
        # observação em 87,5% da linha do tempo: mantém o mesmo orçamento,
        # mas evita que saliência/nitidez descarte justamente essa faixa.
        # Só se aplica às amostras densas de análise aprimorada.
        if include_terminal and share >= 16 and len(group) >= 20:
            late_index = round((len(group) - 1) * 0.875)
            late_candidate = group[late_index]
            if late_candidate not in video_selected:
                replaceable = [
                    (index, candidate) for index, candidate in enumerate(video_selected)
                    if candidate is not group[-1]
                ]
                if replaceable:
                    selected_slot, replaced_candidate = min(
                        replaceable,
                        key=lambda item: abs(
                            group.index(item[1]) - late_index
                        ),
                    )
                    video_selected[selected_slot] = late_candidate
                    logger.info(
                        "amostra_yolo video=%s faixa_tardia_incluida=%s "
                        "substituido=%s",
                        video.name, late_candidate.path.name,
                        replaced_candidate.path.name,
                    )
        # A high-salience frame is not always the frame with the most people;
        # keep the exact terminal frame in the YOLO budget so a late-arriving
        # group can compete for the anchor. Replace only the representative of
        # the final temporal segment, preserving both budget and coverage.
        terminal = group[-1]
        if include_terminal and video_selected and terminal not in video_selected:
            final_segment_start = int((share - 1) * segment_size)
            if terminal in group[final_segment_start:]:
                replaced = video_selected[-1].path.name
                video_selected[-1] = terminal
                logger.info(
                    "amostra_yolo video=%s frame_final_incluido=%s "
                    "substituido=%s",
                    video.name, terminal.path.name, replaced,
                )
        selected.extend(video_selected)
        logger.info(
            "amostra_yolo video=%s angulo=%s selecionados=%d/%d criterio=%s "
            "pico_saliencia=%.6f pico_subida=%.6f limiar=%.6f "
            "saliencia_ativa=%s refinamento_transicao=%s",
            video.name, camera_half or "ambos", share, len(group),
            "saliencia_nitidez" if use_salience else "nitidez_fallback",
            peak_salience, peak_salience_rise, salience_min, use_salience,
            use_salience and use_salience_rise,
        )
    return selected[:total_budget]


def _enhanced_sampling_budget(videos: Sequence[Path], photos: int) -> tuple[int, int]:
    """Roughly one primary sample per second, with a small adaptive reserve.

    Twelve samples remain available for very short clips. The global 40+8
    ceiling and temporal/terminal/dual-view sampling are retained.
    """
    duration = sum(_declared_video_duration(video) for video in videos)
    primary = min(Config.ENHANCED_ANALYSIS_MAX_FRAMES,
                  photos + max(12, int(math.ceil(duration))))
    adaptive = min(Config.ENHANCED_ADAPTIVE_EXTRA_FRAMES,
                   max(2, int(math.ceil(primary * 0.25))))
    return max(0, primary), max(0, adaptive)


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
        winner = choose_immediate_media(files, window_id, sort_key)
        return winner, [winner] if winner is not None else [], []

    with BACKGROUND_CHECKPOINT_LOCK:
        resumed_from_memory = (int(window_id), mode) in BACKGROUND_ANALYSIS_CHECKPOINTS
    checkpoint = _load_background_checkpoint(
        files, window_id, mode, rebuild_videos,
    )
    if checkpoint is not None:
        logger.info(
            "janela=%s checkpoint retomado origem=%s fase=%s "
            "candidatos=%d quadros=%d",
            window_id, "memoria" if resumed_from_memory else "disco",
            mode, len(checkpoint.candidates),
            sum(len(group.frames) for group in checkpoint.frame_sets),
        )
        if checkpoint.analysis_complete:
            logger.info("janela=%s fase=%s resultados_yolo_reutilizados=%d",
                        window_id, mode, len(checkpoint.candidates))
            return (max(checkpoint.candidates, key=(
                        _late_candidate_key if mode in {"late", "late_rebuild"}
                        else lambda c: c.rank_key), default=None),
                    list(checkpoint.candidates), list(checkpoint.frame_sets))
        winner, scored, resumed_sets = _run_checkpointed_analysis(
            files, window_id, mode, checkpoint.candidates,
            checkpoint.frame_sets,
            Config.MELHOR_FOTO_LATE_REBUILD_TIMEOUT
            if mode == "late_rebuild"
            else Config.MELHOR_FOTO_LATE_TIMEOUT
            if mode == "late"
            else Config.MELHOR_FOTO_ENHANCED_TIMEOUT,
            (2, sort_key) if mode in {"late", "late_rebuild"}
            else (1, sort_key),
            rebuild_videos,
            checkpoint.adaptive_fallback_count,
        )
        return winner, scored, resumed_sets

    candidates: list[CandidateImage] = []
    frame_sets: list[MotionFrameSet] = []

    if mode == "late":
        for photo in choose_evenly(photos, Config.LATE_MAX_PHOTOS):
            raise_if_background_preempted(mode, "coleta_fotos")
            candidate = make_candidate(photo, "photo", photo)
            if candidate:
                candidates.append(candidate)
        seconds = (2.0, 5.0, 10.0)[:Config.LATE_FRAMES_PER_VIDEO]
        for video in videos[:Config.LATE_MAX_VIDEOS]:
            for second in seconds:
                raise_if_background_preempted(mode, "coleta_frames")
                frame = extract_single_frame(video, second, fast=True)
                if frame:
                    candidate = make_candidate(frame, "frame", video, second)
                    if candidate:
                        candidates.append(candidate)
        if not candidates:
            return None, [], []
        winner, scored, frame_sets = _run_checkpointed_analysis(
            files, window_id, "late", candidates, frame_sets,
            Config.MELHOR_FOTO_LATE_TIMEOUT, (2, sort_key),
        )
        return winner, scored, frame_sets

    if mode == "late_rebuild":
        for photo in choose_evenly(photos, Config.LATE_MAX_PHOTOS):
            raise_if_background_preempted(mode, "coleta_fotos")
            candidate = make_candidate(photo, "photo", photo)
            if candidate:
                candidates.append(candidate)
        rebuild_set = [
            p for p in videos
            if any(p.resolve() == r.resolve() for r in rebuild_videos)
        ][: max(1, int(Config.LATE_REBUILD_MAX_VIDEOS))]
        other_videos = [p for p in videos if p not in rebuild_set]
        for video in rebuild_set:
            analysis_fps = analysis_frame_rate(video)
            generated, frame_times, coverage_complete = _prepare_video_frames(video, window_id, mode)
            for frame_index, frame in enumerate(generated):
                raise_if_background_preempted(mode, "leitura_frames")
                frame_sec = (
                    float(frame_times[frame_index])
                    if frame_index < len(frame_times)
                    else frame_index / max(1, analysis_fps)
                )
                candidate = make_candidate(frame, "frame", video, frame_sec)
                if candidate:
                    candidates.append(candidate)
            if generated:
                frame_sets.append(
                    MotionFrameSet(
                        video_path=video, frames=tuple(generated),
                        analysis_fps=analysis_fps,
                        frame_times=frame_times,
                        coverage_complete=coverage_complete,
                    )
                )
        seconds = (2.0, 5.0, 10.0)[:Config.LATE_FRAMES_PER_VIDEO]
        for video in other_videos[:Config.LATE_MAX_VIDEOS]:
            for second in seconds:
                raise_if_background_preempted(mode, "coleta_frames")
                frame = extract_single_frame(video, second, fast=True)
                if frame:
                    candidate = make_candidate(frame, "frame", video, second)
                    if candidate:
                        candidates.append(candidate)
        if not candidates:
            return None, [], []
        winner, scored, frame_sets = _run_checkpointed_analysis(
            files, window_id, "late_rebuild", candidates, frame_sets,
            Config.MELHOR_FOTO_LATE_REBUILD_TIMEOUT, (2, sort_key),
            rebuild_videos,
        )
        return winner, scored, frame_sets

    if mode != "enhanced":
        raise ValueError(f"Modo desconhecido: {mode}")

    for photo in choose_evenly(photos, Config.ENHANCED_MAX_PHOTOS):
        raise_if_background_preempted(mode, "coleta_fotos")
        candidate = make_candidate(photo, "photo", photo)
        if candidate:
            candidates.append(candidate)
    videos_to_extract = [
        p for p in dedupe_media_paths(videos) if is_video(p)
    ]
    for video in videos_to_extract:
        analysis_fps = analysis_frame_rate(video)
        generated, frame_times, coverage_complete = _prepare_video_frames(video, window_id, mode)
        for frame_index, frame in enumerate(generated):
            raise_if_background_preempted(mode, "leitura_frames")
            frame_sec = (
                float(frame_times[frame_index])
                if frame_index < len(frame_times)
                else frame_index / max(1, analysis_fps)
            )
            candidate = make_candidate(frame, "frame", video, frame_sec)
            if candidate:
                candidates.append(candidate)
        if generated:
            frame_sets.append(
                MotionFrameSet(
                    video_path=video, frames=tuple(generated),
                    analysis_fps=analysis_fps,
                    frame_times=frame_times,
                    coverage_complete=coverage_complete,
                )
            )
    if not candidates:
        return None, [], []
    adaptive_fallback_count = 0
    primary_budget, adaptive_budget = _enhanced_sampling_budget(
        videos_to_extract, sum(c.source == "photo" for c in candidates),
    )
    max_total_candidates = primary_budget + adaptive_budget
    dense_video_paths = {
        video.resolve() for video in videos_to_extract
        if 0 < _declared_video_duration(video)
        <= Config.ANALYSIS_MEDIUM_FPS_MAX_SECONDS
    }
    if (
        Config.ENHANCED_ANALYSIS_MAX_FRAMES > 0
        and len(candidates) > max_total_candidates
    ):
        photos_part = [c for c in candidates if c.source == "photo"]
        frame_part = [c for c in candidates if c.source == "frame"]
        frame_limit = max(
            0, primary_budget - len(photos_part)
        )
        # A saliência entra antes do YOLO para escolher, dentro de cada
        # trecho temporal, o frame mais provável de conter o evento.
        prefilter_salience = _build_salience_cache(frame_sets)
        selected_frames = sample_frames_across_timeline(
            frame_part, videos_to_extract, frame_limit, prefilter_salience
        )
        primary_candidates = photos_part + selected_frames[:frame_limit]
        selected_paths = {
            item.path.resolve() for item in selected_frames[:frame_limit]
        }
        omitted_frames = [
            item for item in frame_part
            if item.path.resolve() not in selected_paths
        ]
        extra_budget = min(
            adaptive_budget,
            max(0, primary_budget + adaptive_budget
                - len(primary_candidates)),
        )
        upper_budget = (extra_budget + 1) // 2
        lower_budget = extra_budget - upper_budget
        supplemental: list[CandidateImage] = []
        for half, budget in (("upper", upper_budget), ("lower", lower_budget)):
            if budget <= 0:
                continue
            available_frames = [
                item for item in omitted_frames
                if item.path.resolve() not in selected_paths
            ]
            half_frames = sample_frames_across_timeline(
                available_frames, videos_to_extract, budget,
                prefilter_salience, camera_half=half,
            )
            for item in half_frames:
                if item.path.resolve() not in selected_paths:
                    supplemental.append(item)
                    selected_paths.add(item.path.resolve())
        remaining_budget = max(0, extra_budget - len(supplemental))
        if remaining_budget:
            fill = sample_frames_across_timeline(
                [item for item in omitted_frames
                 if item.path.resolve() not in selected_paths],
                videos_to_extract, remaining_budget, prefilter_salience,
            )
            for item in fill:
                if item.path.resolve() not in selected_paths:
                    supplemental.append(item)
                    selected_paths.add(item.path.resolve())
        adaptive_fallback_count = len(supplemental)
        candidates = primary_candidates + supplemental
        logger.info(
            "Janela %s análise amostrada: primários=%d adicionais_adaptativos=%d "
            "limite_total=%d",
            window_id, len(primary_candidates), adaptive_fallback_count,
            max_total_candidates,
        )
    else:
        if dense_video_paths:
            logger.info(
                "Janela %s análise de vídeos curtos amostrada para YOLO; "
                "duracao_maxima=%ds candidatos=%d vídeos=%d limite=%d",
                window_id, Config.ANALYSIS_MEDIUM_FPS_MAX_SECONDS,
                len(candidates), len(dense_video_paths), max_total_candidates,
            )
        else:
            logger.info(
                "Janela %s análise completa: %d candidatos (sem limite)",
                window_id, len(candidates),
            )
    winner, scored, frame_sets = _run_checkpointed_analysis(
        files, window_id, "enhanced", candidates, frame_sets,
        Config.MELHOR_FOTO_ENHANCED_TIMEOUT, (1, sort_key),
        adaptive_fallback_count=adaptive_fallback_count,
    )
    return winner, scored, frame_sets


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
    # A mesma tentativa pode ser executada novamente após falha parcial. A
    # chave é estável mesmo quando o MP4 foi copiado para a área de retry.
    media_digest = compute_md5(media_path) if media_path else None
    event_match = re.search(r"\bID\s*#([^\s\n]+)", message)
    delivery_identity = (
        event_match.group(1) if event_match else message
    )
    delivery_key = hashlib.sha256(
        f"{delivery_identity}\x1f{media_digest or ''}".encode("utf-8")
    ).hexdigest()
    command += ["--delivery-key", delivery_key]
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
    send_started = time.monotonic()
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=timeout, check=False, env=env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Falha no WhatsApp duração=%.1fs: %s", time.monotonic() - send_started, exc)
        return False
    logger.info(
        "whatsapp_transporte tipo=%s duracao=%.1fs codigo=%s limite=%ss",
        "video" if media_path and is_video(media_path) else "foto",
        time.monotonic() - send_started, result.returncode, timeout,
    )
    if result.returncode == 0:
        return True
    # O transportador também escreve o resumo de falha em stdout.
    diagnostic = (result.stderr or result.stdout or "sem diagnóstico").strip()
    diagnostic = diagnostic.replace(message, "[mensagem]")
    for number in Config.WHATSAPP_NUMBERS:
        diagnostic = diagnostic.replace(str(number), "[destinatário]")
    diagnostic = re.sub(r"\b\d{10,15}\b", "[destinatário]", diagnostic)
    logger.warning(
        "WhatsApp retornou código=%s diagnóstico=%s",
        result.returncode, diagnostic[-500:],
    )
    return False


def cache_immediate_retry_media(
    window_id: int, media_path: Path
) -> Optional[Path]:
    """Persiste a foto/recorte exato usado no primeiro envio imediato."""
    if not is_valid_image_file(media_path):
        return None
    try:
        Config.RETRY_DIR.mkdir(parents=True, exist_ok=True)
        suffix = media_path.suffix.lower() or ".jpg"
        target = Config.RETRY_DIR / f"immediate_{window_id}{suffix}"
        shutil.copy2(media_path, target)
        fix_permissions(target)
        return target
    except OSError as exc:
        logger.warning(
            "janela=%s não salvou mídia para retry imediato: %s",
            window_id, exc,
        )
        return None


def cache_enhanced_retry_media(window_id: int, media_path: Path) -> Optional[Path]:
    """Guarda uma cópia estável da mídia antes do envio aprimorado.

    A mídia de movimento vive em diretório temporário e seria removida ao fim
    da tentativa. Esta cópia permite repetir exclusivamente o transporte.
    """
    if not is_valid_media_file(media_path):
        return None
    try:
        Config.RETRY_DIR.mkdir(parents=True, exist_ok=True)
        suffix = media_path.suffix.lower() or ".bin"
        target = Config.RETRY_DIR / f"enhanced_{window_id}{suffix}"
        shutil.copy2(media_path, target)
        fix_permissions(target)
        return target
    except OSError as exc:
        logger.warning(
            "janela=%s não salvou mídia para retry aprimorado: %s",
            window_id, exc,
        )
        return None


def choose_supplemental_photo(
    candidates: Sequence[CandidateImage],
    champion: Optional[ChampionInfo],
    cut_frame_candidates: Sequence[CandidateImage],
    cut_intervals: Sequence[tuple[datetime, datetime]],
) -> Optional[CandidateImage]:
    """Select a sharp photo whose YOLO target matches a frame in the cut."""
    already_sent_md5 = champion.md5 if champion else None
    photos = [
        candidate for candidate in candidates
        if candidate.source == "photo"
        and is_valid_image_file(candidate.path)
        and candidate.md5 != already_sent_md5
    ]

    def target_kinds(candidate: CandidateImage) -> set[str]:
        kinds: set[str] = set()
        detected = {normalize_label(label) for label in candidate.detected}
        if candidate.person_count > 0 or any(
            label_is_person(label) for label in detected
        ):
            kinds.add("person")
        if candidate.animal_count > 0 or any(
            label_is_animal(label) for label in detected
        ):
            kinds.add("animal")
        if (
            normalize_label(candidate.category or "") == "vehicle"
            and candidate.object_count > 0
        ) or detected.intersection({
            "vehicle", "car", "motorcycle", "bus", "truck",
        }):
            kinds.add("vehicle")
        return kinds

    cut_targets = set().union(*(
        target_kinds(candidate) for candidate in cut_frame_candidates
    )) if cut_frame_candidates else set()
    tolerance = max(
        0.0, float(Config.ENHANCED_SUPPLEMENTAL_TIME_TOLERANCE)
    )

    def overlaps_cut(photo: CandidateImage) -> bool:
        span = extract_motion_span(photo.path)
        if span is None:
            return False
        timestamp = span.start
        return any(
            start - timedelta(seconds=tolerance)
            <= timestamp
            <= end + timedelta(seconds=tolerance)
            for start, end in cut_intervals
        )

    relevant_photos = [
        candidate for candidate in photos
        if target_kinds(candidate).intersection(cut_targets)
        and overlaps_cut(candidate)
    ]
    if not relevant_photos:
        if photos:
            logger.info(
                "foto_complementar ignorada motivo="
                "sem_correspondencia_yolo_no_corte candidatas=%d "
                "quadros_corte=%d alvos_corte=%s",
                len(photos), len(cut_frame_candidates),
                ",".join(sorted(cut_targets)) or "nenhum",
            )
        return None

    min_sharpness = max(
        0.0, float(Config.ENHANCED_SUPPLEMENTAL_MIN_SHARPNESS)
    )
    qualified: list[CandidateImage] = []
    measured_scores: list[float] = []
    for candidate in relevant_photos:
        sharpness = candidate.sharpness
        if sharpness is None:
            camera_half = choose_photo_camera_half(candidate)
            sharpness = compute_sharpness(
                candidate.path,
                camera_half=None if camera_half == "full" else camera_half,
            )
            candidate.sharpness = sharpness
        if sharpness is None:
            continue
        measured_scores.append(float(sharpness))
        if sharpness >= min_sharpness:
            qualified.append(candidate)

    if not qualified:
        logger.info(
            "foto_complementar ignorada motivo=nitidez_insuficiente "
            "candidatas_com_alvo=%d melhor=%.1f limite=%.1f",
            len(relevant_photos), max(measured_scores, default=0.0),
            min_sharpness,
        )
        return None

    def quality_key(candidate: CandidateImage) -> tuple[int, int, float, float, float]:
        if candidate.person_count > 0:
            group, count = 3, candidate.person_count
        elif candidate.animal_count > 0:
            group, count = 2, candidate.animal_count
        else:
            group, count = 1, candidate.object_count
        timestamp = (
            candidate.frame_sec
            if candidate.frame_sec is not None
            else float(candidate.file_ts)
        )
        return (
            group, count,
            float(candidate.sharpness or 0.0),
            float(candidate.engine_score or 0.0),
            timestamp,
        )

    selected = max(qualified, key=quality_key)
    logger.info(
        "foto_complementar selecionada arquivo=%s nitidez=%.1f "
        "alvo=pessoa:%d,animal:%d,veiculo:%s",
        selected.path.name, float(selected.sharpness or 0.0),
        selected.person_count, selected.animal_count,
        selected.category or "detectado",
    )
    return selected


def remove_retry_media(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.resolve().relative_to(Config.RETRY_DIR.resolve())
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def score_fmt(score: Optional[float]) -> str:
    return f"{score:.2f}" if score is not None else "N/D"


def discovered_fmt(seconds: Optional[int]) -> str:
    if seconds is None or seconds <= 0:
        return "agora" if seconds is not None else "indisponível"
    return f"há {duration_fmt(seconds)}"


def duration_fmt(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}min")
    if secs or not parts:
        parts.append(f"{secs}s")
    return "".join(parts)


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
    if meta.state == "merged":
        logger.info("janela=%s fase=imediato ignorada motivo=merged", window_id)
        return

    retry_media, retry_message, original_media = db.get_immediate_retry(
        window_id
    )
    if retry_media is not None and retry_message and is_valid_image_file(retry_media):
        # O aviso imediato é um retrato do primeiro envio daquela janela.
        # Novos arquivos ficam para o aprimorado/tardio; retry repete exatamente
        # a mesma mídia e legenda, preservando também a chave de idempotência.
        if send_whatsapp(retry_message, retry_media):
            db.mark_immediate_done(
                window_id, original_media or retry_media,
                True, meta.event_code, None,
            )
            cached = db.clear_immediate_retry(window_id)
            if cached is not None:
                cached.unlink(missing_ok=True)
            stats.inc("immediate_sent")
            log_decision(
                "imediato", window_id, "enviado", "retry_payload_estavel",
                meta.event_code,
            )
        else:
            attempts = db.increment_attempt(window_id, "attempts")
            if attempts >= Config.MAX_ALERT_RETRIES:
                db.mark_immediate_done(
                    window_id, original_media or retry_media,
                    False, meta.event_code, "whatsapp_failed",
                )
                cached = db.clear_immediate_retry(window_id)
                if cached is not None:
                    cached.unlink(missing_ok=True)
                stats.inc("immediate_failed")
            else:
                logger.warning(
                    "janela=%s fase=imediato retry_payload_estavel "
                    "tentativa=%d/%d",
                    window_id, attempts + 1, Config.MAX_ALERT_RETRIES,
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
        movement_duration = duration_fmt(meta.window_end - meta.window_start)
        count_views_note: Optional[str] = None
        object_summary = immediate_object_summary(candidate)
        if candidate.engine_score is not None:
            immediate_people, immediate_animals, count_views_note = (
                candidate_count_message(candidate)
            )
            logger.info(
                "janela=%s fase=imediato mensagem_contagem pessoas=%s "
                "animais=%s superior=%d/%d inferior=%d/%d "
                "sobreposicao_possivel=%s",
                window_id, immediate_people, immediate_animals,
                candidate.upper_person_count, candidate.upper_animal_count,
                candidate.lower_person_count, candidate.lower_animal_count,
                count_views_note is not None,
            )
        else:
            logger.warning(
                "janela=%s fase=imediato contagem=pendente motivo="
                "yolo_indisponivel; mensagem_nao_reporta_zero",
                window_id,
            )
        count_views_line = f"\n{count_views_note}" if count_views_note else ""
        message = (
            f"🚨 Movimento · ID #{window_id}\n"
            f"{start}-{end} · {meta.file_count} arq "
            f"(📷{meta.photo_count} 📹{meta.video_count}) "
            f"· mov {movement_duration} · desc {discovered_fmt(discovery_age)}\n"
            f"{object_summary}{count_views_line}\n"
            f"{Config.CAMERA_LINK}"
        )
        stable_media = cache_immediate_retry_media(window_id, send_path)
        if stable_media is None:
            # Se não for possível guardar o recorte, usa a foto de origem
            # persistente para que futuras tentativas não dependam do tmp.
            stable_media = candidate.path
            send_path = candidate.path
            logger.warning(
                "janela=%s imediato sem cache do recorte; retry usará origem=%s",
                window_id, candidate.path.name,
            )
        db.set_immediate_retry(
            window_id, stable_media, message, candidate.path
        )
        if send_whatsapp(message, stable_media):
            db.mark_immediate_done(
                window_id, candidate.path, True, meta.event_code, None
            )
            cached = db.clear_immediate_retry(window_id)
            if cached is not None:
                cached.unlink(missing_ok=True)
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
            cached = db.clear_immediate_retry(window_id)
            if cached is not None:
                cached.unlink(missing_ok=True)
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
    if meta.state == "merged":
        logger.info("janela=%s fase=aprimorado ignorada motivo=merged", window_id)
        return
    # Retry de transporte: a análise já terminou numa tentativa anterior.
    # Não reextraia frames nem carregue YOLO por uma indisponibilidade do
    # WhatsApp.
    if (
        meta.enhanced_retry_media is not None
        and meta.enhanced_retry_message
        and is_valid_media_file(meta.enhanced_retry_media)
    ):
        logger.info(
            "janela=%s aprimorado retry_transporte arquivo=%s tentativa=%d",
            window_id, meta.enhanced_retry_media.name,
            meta.enhanced_attempts + 1,
        )
        if send_whatsapp(meta.enhanced_retry_message, meta.enhanced_retry_media):
            db.mark_enhanced_done(
                window_id, meta.enhanced_retry_media, meta.event_code, True, None
            )
            remove_retry_media(db.clear_enhanced_retry(window_id))
            stats.inc("enhanced_sent")
            log_decision(
                "aprimorado", window_id, "enviado", "retry_transporte",
                meta.event_code, None,
            )
            return
        attempts = db.increment_attempt(window_id, "enhanced_attempts")
        if attempts >= Config.MAX_ALERT_RETRIES:
            db.mark_enhanced_done(
                window_id, None, meta.event_code, False, "whatsapp_failed"
            )
            remove_retry_media(db.clear_enhanced_retry(window_id))
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
    event_person_count, event_animal_count, event_camera_counts = (
        consolidate_event_counts(scored_candidates, winner)
    )
    logger.info(
        "janela=%s aprimorado deteccao_evento candidatos=%d pessoas=%d "
        "animais=%d superior=%d/%d inferior=%d/%d",
        window_id, len(scored_candidates), event_person_count,
        event_animal_count,
        event_camera_counts["upper_person"],
        event_camera_counts["upper_animal"],
        event_camera_counts["lower_person"],
        event_camera_counts["lower_animal"],
    )
    if winner is not None:
        winner = replace(
            winner,
            person_count=event_person_count,
            animal_count=event_animal_count,
            object_count=max(event_person_count, event_animal_count),
            upper_person_count=event_camera_counts["upper_person"],
            lower_person_count=event_camera_counts["lower_person"],
            upper_animal_count=event_camera_counts["upper_animal"],
            lower_animal_count=event_camera_counts["lower_animal"],
            rank_group=classify_rank_group(event_person_count, event_animal_count),
        )
    motion_video: Optional[Path] = None
    motion_frames = 0
    motion_build: Optional[MotionBuild] = None
    send_path: Optional[Path] = None
    temp_path: Optional[Path] = None
    if scored_candidates:
        motion_build = build_motion_video(
            scored_candidates, window_id, frame_sets, phase="enhanced"
        )
        if motion_build:
            motion_video, motion_frames = (
                motion_build.path, motion_build.frame_count
            )
            db.set_motion_context(window_id, motion_build)
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
        person_range = format_event_count_range(event_camera_counts, "person")
        animal_range = format_event_count_range(event_camera_counts, "animal")
        cross_view_ambiguity = any(
            low != high for low, high in (
                event_count_range(event_camera_counts, "person"),
                event_count_range(event_camera_counts, "animal"),
            )
        )
        logger.info(
            "janela=%s fase=aprimorado mensagem_contagem pessoas=%s "
            "animais=%s superior=%d/%d inferior=%d/%d sobreposicao_possivel=%s",
            window_id, person_range, animal_range,
            event_camera_counts["upper_person"],
            event_camera_counts["upper_animal"],
            event_camera_counts["lower_person"],
            event_camera_counts["lower_animal"], cross_view_ambiguity,
        )
        new_score = winner.engine_score if winner else None
        movement_duration = duration_fmt(meta.window_end - meta.window_start)
        movement_age = max(0, int(time.time()) - meta.window_end)
        message = (
            f"🚨 *Resumo* · ID #{window_id}\n"
            f"{start}-{end} · {len(files)} arq "
            f"(📷{photo_count} 📹{video_count}) · mov {movement_duration}\n"
            f"👤{old_person}→{person_range} "
            f"🐾{old_animal}→{animal_range}\n"
            f"Ângulos{' (faixa considera possível sobreposição)' if cross_view_ambiguity else ''}: "
            f"sup 👤{event_camera_counts['upper_person']} "
            f"🐾{event_camera_counts['upper_animal']} · "
            f"inf 👤{event_camera_counts['lower_person']} "
            f"🐾{event_camera_counts['lower_animal']} · "
            f"score {score_fmt(old_score)}→{score_fmt(new_score)}\n"
            f"{media_note} · mov {discovered_fmt(movement_age)}\n"
            f"{Config.CAMERA_LINK}"
        )
        retry_media = cache_enhanced_retry_media(window_id, enhanced_media)
        if retry_media is not None:
            db.set_enhanced_retry(window_id, retry_media, message)
        if send_whatsapp(message, enhanced_media):
            remove_retry_media(db.clear_enhanced_retry(window_id))
            if winner is not None and should_send:
                db.update_champion(window_id, winner, "enhanced")
            supplemental_photo = (
                choose_supplemental_photo(
                    scored_candidates, champion,
                    motion_build.cut_candidates if motion_build else (),
                    motion_build.cut_intervals if motion_build else (),
                )
                if motion_video is not None else None
            )
            if supplemental_photo is not None:
                photo_path, photo_temp = prepare_photo_for_send(
                    supplemental_photo
                )
                if photo_path is None:
                    photo_path = supplemental_photo.path
                photo_message = (
                    f"📷 Foto complementar · ID #{window_id}\n"
                    f"{start}-{end} · movimento {movement_duration}\n"
                    f"{Config.CAMERA_LINK}"
                )
                photo_retry = cache_enhanced_retry_media(
                    window_id, photo_path
                )
                db.set_enhanced_retry(
                    window_id, photo_retry or supplemental_photo.path,
                    photo_message,
                )
                try:
                    photo_sent = send_whatsapp(photo_message, photo_path)
                finally:
                    if photo_temp is not None:
                        cleanup_attempt_temp([photo_temp])
                if not photo_sent:
                    attempts = db.increment_attempt(
                        window_id, "enhanced_attempts"
                    )
                    if attempts >= Config.MAX_ALERT_RETRIES:
                        db.mark_enhanced_done(
                            window_id, winner_path, meta.event_code,
                            False, "supplemental_photo_failed",
                        )
                        remove_retry_media(
                            db.clear_enhanced_retry(window_id)
                        )
                        stats.inc("enhanced_failed")
                    else:
                        logger.warning(
                            "janela=%s fase=aprimorado acao=video_enviado "
                            "foto_complementar=retry tentativa=%d/%d",
                            window_id, attempts + 1,
                            Config.MAX_ALERT_RETRIES,
                        )
                    return
                remove_retry_media(db.clear_enhanced_retry(window_id))
                logger.info(
                    "janela=%s fase=aprimorado foto_complementar=enviada "
                    "arquivo=%s",
                    window_id, supplemental_photo.path.name,
                )
            db.mark_enhanced_done(
                window_id, winner_path, meta.event_code, True, None
            )
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
            remove_retry_media(db.clear_enhanced_retry(window_id))
            stats.inc("enhanced_failed")
        else:
            logger.debug(
                "Janela %s aprimorado aguardando retry %d/%d",
                window_id, attempts, Config.MAX_ALERT_RETRIES,
            )
    finally:
        if not isinstance(sys.exc_info()[1], BackgroundPreempted):
            _clear_background_checkpoint(window_id, "enhanced")
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
    if meta.state == "merged":
        logger.info("janela=%s fase=tardio ignorada motivo=merged", window_id)
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
    built: Optional[MotionBuild] = None
    motion_video: Optional[Path] = None
    motion_frames = 0
    full_context_rebuild = False
    send_path: Optional[Path] = None
    temp_path: Optional[Path] = None
    if mode == "late_rebuild" and scored:
        built = build_motion_video(scored, window_id, frame_sets, phase=mode)
        if built:
            motion_video, motion_frames = built.path, built.frame_count
    # Uma atualização tardia pode descobrir pessoas nas duas câmeras depois
    # que já enviamos um recorte. Nesse caso, reconstrói o vídeo da origem do
    # frame vencedor em modo full, mesmo sem esperar um novo arquivo tardio.
    dual_camera_people = bool(
        candidate is not None
        and candidate.upper_person_count + candidate.upper_animal_count > 0
        and candidate.lower_person_count + candidate.lower_animal_count > 0
    )
    if (
        mode == "late"
        and dual_camera_people
        and candidate is not None
        and candidate.origin_path is not None
        and is_video(candidate.origin_path)
    ):
        generated = _extract_all_frames(candidate.origin_path, window_id, mode)
        fps = analysis_frame_rate(candidate.origin_path)
        generated, frame_times, coverage_complete = _ensure_analysis_frame_coverage(
            candidate.origin_path, generated, fps, window_id, mode,
        )
        if len(generated) >= 2:
            rebuilt_candidates = list(scored)
            for index, frame in enumerate(generated):
                frame_candidate = make_candidate(
                    frame, "frame", candidate.origin_path, frame_times[index]
                )
                if frame_candidate is not None:
                    rebuilt_candidates.append(frame_candidate)
            # Attach existing YOLO results to the corresponding decoded time;
            # never infer another time's detections across a sparse gap.
            for known in scored:
                if known.origin_path != candidate.origin_path or known.frame_sec is None:
                    continue
                index = min(range(len(frame_times)), key=lambda i: abs(frame_times[i] - known.frame_sec))
                if abs(frame_times[index] - known.frame_sec) <= 0.5 / fps:
                    rebuilt_candidates.append(replace(known, path=generated[index], frame_sec=frame_times[index]))
            rebuilt_sets = [
                MotionFrameSet(
                    candidate.origin_path, tuple(generated),
                    fps, frame_times, coverage_complete,
                )
            ]
            built = build_motion_video(
                rebuilt_candidates, window_id, rebuilt_sets, phase=mode
            )
            if built:
                motion_video, motion_frames = built.path, built.frame_count
                frame_sets = rebuilt_sets
                full_context_rebuild = True
                logger.info(
                    "janela=%d tardio contexto_duas_cameras superior=%d "
                    "inferior=%d video=full",
                    window_id,
                    candidate.upper_person_count + candidate.upper_animal_count,
                    candidate.lower_person_count + candidate.lower_animal_count,
                )
    try:
        if candidate is None and motion_video is None:
            attempts = db.increment_attempt(window_id, "late_attempts")
            if attempts >= Config.MAX_ALERT_RETRIES:
                db.mark_late_files_evaluated(window_id, late_files)
                stats.inc("late_failed")
            return
        champion = db.get_champion(window_id)
        should_send, reason = compare_candidates(candidate, champion)
        cut_best = max(built.cut_candidates, key=_late_candidate_key, default=None) if built else None
        cut_improves, _ = compare_candidates(cut_best, champion)
        new_view = bool(
            full_context_rebuild and built and built.camera_half == "full"
            and meta.motion_camera_half != "full"
            and any(c.upper_person_count + c.upper_animal_count > 0
                    and c.lower_person_count + c.lower_animal_count > 0
                    for c in built.cut_candidates)
        )
        send_video = bool(motion_video is not None and (cut_improves or new_view))
        if send_video and cut_best is not None:
            candidate = cut_best
            should_send, reason = compare_candidates(candidate, champion)
        if send_video:
            send_reason = (
                "video_full_duas_cameras"
                if full_context_rebuild else "video_reconstruido_sucessor"
            )
            media = motion_video
            media_note = (
                f"🎥 vídeo {'completo' if full_context_rebuild else 'de movimento atualizado'} "
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
        if candidate is not None:
            late_people, late_animals, late_count_note = candidate_count_message(candidate)
            logger.info(
                "janela=%s fase=tardio mensagem_contagem pessoas=%s "
                "animais=%s superior=%d/%d inferior=%d/%d "
                "sobreposicao_possivel=%s",
                window_id, late_people, late_animals,
                candidate.upper_person_count, candidate.upper_animal_count,
                candidate.lower_person_count, candidate.lower_animal_count,
                late_count_note is not None,
            )
        else:
            late_people, late_animals, late_count_note = (
                str(new_person), str(new_animal), None
            )
        late_count_line = f"{late_count_note}\n" if late_count_note else ""
        new_score = (
            candidate.engine_score if candidate is not None else None
        )
        start, end = window_message_prefix(meta)
        photo_count = sum(is_photo(path) for path in late_files)
        video_count = sum(is_video(path) for path in late_files)
        movement_duration = duration_fmt(meta.window_end - meta.window_start)
        movement_age = max(0, int(time.time()) - meta.window_end)
        message = (
            f"🔁 Atualização · ID #{window_id}\n"
            f"{start}-{end} · {len(late_files)} arq "
            f"(📷{photo_count} 📹{video_count}) · mov {movement_duration}\n"
            f"👤{old_people}→{late_people} "
            f"🐾{old_animals}→{late_animals} · "
            f"{late_count_line}"
            f"score {score_fmt(old_score)}→{score_fmt(new_score)}\n"
            f"{media_note} · mov {discovered_fmt(movement_age)}\n"
            f"{Config.CAMERA_LINK}"
        )
        if send_whatsapp(message, media):
            if candidate is not None and should_send:
                db.update_champion(
                    window_id, candidate,
                    "late_rebuild" if send_video else "late",
                )
            if send_video:
                if built is not None:
                    db.set_motion_context(window_id, built)
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
        if not isinstance(sys.exc_info()[1], BackgroundPreempted):
            _clear_background_checkpoint(window_id, mode)
            cleanup_attempt_temp(
                collect_temp_paths(candidate, motion_video, frame_sets)
                + ([temp_path] if temp_path else [])
            )


def clean_temp_files() -> None:
    if not Config.TMP_DIR.exists():
        return
    now = time.time()
    try:
        tmp_root = Config.TMP_DIR.resolve()
    except OSError:
        tmp_root = Config.TMP_DIR
    pinned_dirs: set[Path] = set()
    with BACKGROUND_CHECKPOINT_LOCK:
        checkpoints = tuple(BACKGROUND_ANALYSIS_CHECKPOINTS.values())
    for checkpoint in checkpoints:
        staged_paths = [candidate.path for candidate in checkpoint.candidates]
        staged_paths.extend(
            frame for group in checkpoint.frame_sets for frame in group.frames
        )
        for staged_path in staged_paths:
            try:
                parent = staged_path.resolve().parent
                parent.relative_to(tmp_root)
                pinned_dirs.add(parent)
            except (OSError, ValueError):
                continue
    # Checkpoints survive process restarts through manifests on disk. Their
    # staged frames must survive the same cleanup pass too, otherwise a long
    # queue makes a valid checkpoint unusable before its window is resumed.
    checkpoint_dir = Config.TMP_DIR / "background_checkpoints"
    if checkpoint_dir.is_dir():
        for manifest in checkpoint_dir.glob("*_*.json"):
            if manifest.name.startswith("yolo_"):
                continue
            try:
                window_text, mode = manifest.stem.split("_", 1)
                window_id = int(window_text)
                checkpoint = _read_checkpoint_manifest(window_id, mode)
            except (ValueError, OSError):
                continue
            if (
                checkpoint is None
                or not _checkpoint_artifacts_exist(checkpoint)
            ):
                continue
            staged_paths = [
                candidate.path for candidate in checkpoint.candidates
            ]
            staged_paths.extend(
                frame for group in checkpoint.frame_sets for frame in group.frames
            )
            for staged_path in staged_paths:
                try:
                    parent = staged_path.resolve().parent
                    parent.relative_to(tmp_root)
                    pinned_dirs.add(parent)
                except (OSError, ValueError):
                    continue
    for root in (Config.TMP_DIR / "preparation").glob("*"):
        manifests = list(root.glob("*.json"))
        if not manifests:
            continue
        try:
            if now - max(p.stat().st_mtime for p in manifests) > Config.TMP_FILE_TTL:
                shutil.rmtree(root, ignore_errors=True)
                continue
            for manifest in manifests:
                for path in _preparation_paths(json.loads(manifest.read_text())):
                    pinned_dirs.add(path.resolve().parent)
        except (OSError, ValueError, TypeError):
            continue
    for item in Config.TMP_DIR.iterdir():
        try:
            if item.is_dir() and item.name.startswith(
                ("single_", "first_", "all_", "motion_", "coverage_")
            ):
                if item.resolve() in pinned_dirs:
                    continue
                if now - item.stat().st_mtime > Config.TMP_FILE_TTL:
                    shutil.rmtree(item, ignore_errors=True)
            elif item.is_file():
                if now - item.stat().st_mtime > Config.TMP_FILE_TTL:
                    item.unlink(missing_ok=True)
        except OSError:
            logger.debug("Falha ao limpar temporário: %s", item)


BG_LOCK = threading.Lock()
# A conclusão (ou pausa cooperativa) de um fluxo em segundo plano deve acordar
# o laço principal sem aguardar o próximo poll.  Assim a próxima decisão é
# sempre tomada sobre as filas atuais, e não sobre uma fila interna antiga do
# executor.
SCHEDULER_WAKE = threading.Event()
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
    except BackgroundYOLOTimeout as exc:
        if db is not None:
            attempts = db.increment_attempt(window_id, "enhanced_attempts")
            if attempts >= Config.MAX_ALERT_RETRIES:
                meta = db.get_window_meta(window_id)
                db.mark_enhanced_done(
                    window_id, None, meta.event_code if meta else "",
                    False, "yolo_timeout",
                )
                _clear_background_checkpoint(window_id, "enhanced")
                logger.warning(
                    "janela=%s aprimorado encerrado motivo=yolo_timeout "
                    "tentativas=%d; fila liberada",
                    window_id, attempts,
                )
            else:
                logger.warning(
                    "janela=%s aprimorado timeout retentativa=%d/%d; "
                    "checkpoint preservado e janela volta ao fim da fila",
                    window_id, attempts, Config.MAX_ALERT_RETRIES,
                )
        else:
            logger.warning("janela=%s aprimorado timeout sem DB: %s", window_id, exc)
    except BackgroundPreempted as exc:
        # Não é erro nem tentativa: a janela continua pendente para retomar
        # depois que o imediato terminar.
        logger.info("janela=%s aprimorado pausado %s", window_id, exc)
    except Exception as exc:
        logger.error(
            "Erro no aprimorado background janela %s: %s",
            window_id, exc, exc_info=True,
        )
        if db is not None:
            attempts = db.increment_attempt(window_id, "enhanced_attempts")
            if attempts >= Config.MAX_ALERT_RETRIES:
                meta = db.get_window_meta(window_id)
                db.mark_enhanced_done(window_id, None, meta.event_code if meta else "",
                                      False, "background_error")
                _clear_background_checkpoint(window_id, "enhanced")
    finally:
        if db is not None:
            db.close()
        with BG_LOCK:
            ENHANCED_IN_PROGRESS.discard(window_id)
        SCHEDULER_WAKE.set()


def _bg_late(window_id: int) -> None:
    db: Optional[Database] = None
    try:
        db = Database()
        stats = CycleStats()
        worker_late(db, window_id, stats)
    except BackgroundYOLOTimeout as exc:
        if db is not None:
            attempts = db.increment_attempt(window_id, "late_attempts")
            if attempts >= Config.MAX_ALERT_RETRIES:
                db.mark_late_files_evaluated(window_id, db.get_late_files(window_id))
                _clear_background_checkpoint(window_id, "late")
                _clear_background_checkpoint(window_id, "late_rebuild")
                logger.warning(
                    "janela=%s tardio encerrado motivo=yolo_timeout "
                    "tentativas=%d; fila liberada",
                    window_id, attempts,
                )
            else:
                logger.warning(
                    "janela=%s tardio timeout retentativa=%d/%d; "
                    "checkpoint preservado e janela volta ao fim da fila",
                    window_id, attempts, Config.MAX_ALERT_RETRIES,
                )
        else:
            logger.warning("janela=%s tardio timeout sem DB: %s", window_id, exc)
    except BackgroundPreempted as exc:
        logger.info("janela=%s tardio pausado %s", window_id, exc)
    except Exception as exc:
        logger.error(
            "Erro no tardio background janela %s: %s",
            window_id, exc, exc_info=True,
        )
        if db is not None:
            attempts = db.increment_attempt(window_id, "late_attempts")
            if attempts >= Config.MAX_ALERT_RETRIES:
                db.mark_late_files_evaluated(window_id, db.get_late_files(window_id))
                _clear_background_checkpoint(window_id, "late")
                _clear_background_checkpoint(window_id, "late_rebuild")
    finally:
        if db is not None:
            db.close()
        with BG_LOCK:
            LATE_IN_PROGRESS.discard(window_id)
        SCHEDULER_WAKE.set()


def submit_background_work(db: Database) -> tuple[int, int]:
    """Despacha no máximo um fluxo de fundo, sempre pela prioridade atual.

    Não usamos a fila ilimitada do ``ThreadPoolExecutor``: reservar dezenas de
    IDs nela escondia a ordem real e deixava os tardios permanentemente atrás
    de aprimorados já enfileirados.  A cada término, o laço reconsulta o banco
    e escolhe novamente imediato -> aprimorado -> tardio.
    """
    with BG_LOCK:
        if db.pending_immediate():
            IMMEDIATE_PRESSURE.set()
            return 0, 0
        enhanced_pending = db.pending_enhanced()
        enhanced_priority = db.has_enhanced_priority()
        enhanced_active = bool(ENHANCED_IN_PROGRESS)
        late_active = bool(LATE_IN_PROGRESS)

        if enhanced_priority or enhanced_active:
            ENHANCED_PRESSURE.set()
        else:
            ENHANCED_PRESSURE.clear()

        # Um tardio ativo recebe ENHANCED_PRESSURE e se pausa no próximo ponto
        # cooperativo; não iniciamos outro processo em paralelo, poupando RAM.
        if enhanced_priority or enhanced_active:
            if enhanced_pending and not enhanced_active and not late_active:
                # Retomar um checkpoint evita repetir a extração e impede que
                # ele fique atrás de toda janela já iniciada. Os manifestos
                # também entram na seleção para sobreviver a reinícios.
                # A prioridade entre instantâneo, aprimorado e tardio continua
                # sendo aplicada acima; isto só ordena o trabalho aprimorado.
                resumable: list[int] = []
                retry_tier = db.get_window_meta(enhanced_pending[0])
                for candidate in enhanced_pending:
                    candidate_meta = db.get_window_meta(candidate)
                    if (retry_tier is not None and candidate_meta is not None
                            and candidate_meta.enhanced_attempts > retry_tier.enhanced_attempts):
                        continue
                    key = (int(candidate), "enhanced")
                    with BACKGROUND_CHECKPOINT_LOCK:
                        checkpoint = BACKGROUND_ANALYSIS_CHECKPOINTS.get(key)
                    if checkpoint is None:
                        manifest = _checkpoint_manifest_path(*key)
                        if manifest is not None and manifest.is_file():
                            checkpoint = _read_checkpoint_manifest(*key)
                    if ((checkpoint is not None and _checkpoint_artifacts_exist(checkpoint))
                            or _has_preparation(candidate, "enhanced")):
                        resumable.append(candidate)
                window_id = (resumable or enhanced_pending)[0]
                ENHANCED_IN_PROGRESS.add(window_id)
                ENHANCED_EXECUTOR.submit(_bg_enhanced, window_id)
                logger.info(
                    "scheduler: despachado aprimorado janela=%s "
                    "fila_aprimorado=%d",
                    window_id, len(enhanced_pending),
                )
                return 1, 0
            return 0, 0

        # Sem demanda de prioridade 1/2, um único tardio pode avançar.
        if late_active:
            return 0, 0
        late_pending = db.pending_late()
        if late_pending:
            window_id = late_pending[0]
            LATE_IN_PROGRESS.add(window_id)
            LATE_EXECUTOR.submit(_bg_late, window_id)
            logger.info(
                "scheduler: despachado tardio janela=%s fila_tardio=%d",
                window_id, len(late_pending),
            )
            return 0, 1
        return 0, 0


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
        if immediate_pending:
            # O sinal é levantado antes de esperar o motor: aprimorado e
            # tardio cedem o subprocesso em até BACKGROUND_PREEMPT_POLL.
            IMMEDIATE_PRESSURE.set()
        try:
            for window_id in immediate_pending:
                try:
                    worker_immediate(db, window_id, stats)
                except Exception as exc:
                    logger.error(
                        "Erro no imediato janela %s: %s",
                        window_id, exc, exc_info=True,
                    )
        finally:
            # Se houver resumo pendente, o tardio permanece cedido até ele
            # terminar; só então a fila de menor prioridade volta a avançar.
            if db.has_enhanced_priority():
                ENHANCED_PRESSURE.set()
            IMMEDIATE_PRESSURE.clear()
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
        # Limpar antes da varredura evita perder uma conclusão que ocorra
        # durante o processamento; se ela já ocorreu, a própria varredura vê
        # o item fora de progresso e agenda o próximo imediatamente.
        SCHEDULER_WAKE.clear()
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
        SCHEDULER_WAKE.wait(Config.LOOP_POLL_INTERVAL)


def main() -> None:
    Config.BASE_DIR.mkdir(parents=True, exist_ok=True)
    Config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    fix_permissions(Config.TMP_DIR, is_dir=True)
    configure_logging()
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
