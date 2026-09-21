#!/usr/bin/env python3
"""melhor_foto.py — seleção determinística da melhor imagem.

Hierarquia: multiple_people > person > animal > sharpness.
Motores: YOLO (padrão), HOG e fallback por nitidez.
Saída: text | path | json.

Toda a configuração fica em `Config` (abaixo). Edite os valores
diretamente conforme o hardware — não há leitura de .env nem de
variáveis de ambiente de configuração.
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# 1. Configuração central (valores literais, editáveis)
# ─────────────────────────────────────────────────────────────────────────────
from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True, slots=True)
class Config:
    # ── Concorrência / threads (aplicado ANTES de importar torch) ────────────
    threads_per_process: int = 1

    # ── Tempos (segundos) ────────────────────────────────────────────────────
    subprocess_timeout: int = 900
    pip_timeout: int = 600
    venv_timeout: int = 300

    # ── YOLO ─────────────────────────────────────────────────────────────────
    # Batch reduzido para caber em VM com ~1 GB de RAM.
    yolo_batch_size: int = 4
    yolo_imgsz: int = 640
    yolo_person_min_conf: float = 0.60
    yolo_animal_min_conf: float = 0.50

    # ── Bootstrap automático de dependências ─────────────────────────────────
    auto_install: bool = True
    venv_dir_name: str = ".venv-cam"

    # ── Diversos ─────────────────────────────────────────────────────────────
    default_model_name: str = "yolov8n.pt"
    active_camera_only: bool = True
    min_image_bytes: int = 128
    image_suffixes: frozenset[str] = field(
        default_factory=lambda: frozenset({".jpg", ".jpeg", ".png"})
    )


CONFIG: Final[Config] = Config()

# Aplica variáveis de threads — DEVE ocorrer antes de qualquer import de
# torch/ultralytics, senão o OpenMP/MKL já inicializa com o default do
# sistema (nproc), causando oversubscription em VMs pequenas.
import os as _os

_THREADS_STR: str = str(CONFIG.threads_per_process)
for _key in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    _os.environ.setdefault(_key, _THREADS_STR)
_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
del _key, _THREADS_STR

# ─────────────────────────────────────────────────────────────────────────────
# 2. Imports
# ─────────────────────────────────────────────────────────────────────────────
import argparse
import json
import logging
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from dataclasses import dataclass as _dataclass_alias  # já importado acima
from pathlib import Path
from typing import Any, Literal, Optional, Sequence, cast

__version__: Final[str] = "2.1.0"

EngineName = Literal["auto", "yolo", "hog"]
ConcreteEngine = Literal["yolo", "hog"]
OutputFormat = Literal["text", "path", "json"]
Category = Literal["multiple_people", "person", "animal", "sharpness"]

logger = logging.getLogger("melhor_foto")
logger.addHandler(logging.NullHandler())

# ─────────────────────────────────────────────────────────────────────────────
# 3. Constantes de domínio
# ─────────────────────────────────────────────────────────────────────────────
JPEG_MAGIC: Final[bytes] = b"\xff\xd8\xff"
PNG_MAGIC: Final[bytes] = b"\x89PNG\r\n\x1a\n"

ANIMAL_LABELS: Final[dict[int, str]] = {
    14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep",
    19: "cow", 20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe",
}
ANIMAL_IDS: Final[frozenset[int]] = frozenset(ANIMAL_LABELS)
PERSON_CLASS_ID: Final[int] = 0

LEVEL_MULTIPLE_PEOPLE: Final[int] = 4
LEVEL_PERSON: Final[int] = 3
LEVEL_ANIMAL: Final[int] = 2
LEVEL_SHARPNESS: Final[int] = 1
LEVEL_NONE: Final[int] = 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. Dataclasses de resultado
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(slots=True, frozen=True)
class SelectionResult:
    category: str
    object_count: int
    detections: tuple[str, ...]


@dataclass(slots=True)
class DetectionRecord:
    path: str
    score: float
    sharpness: float
    category: str
    object_count: int
    person_count: int
    animal_count: int
    detected: tuple[str, ...]
    level: int
    width: int
    height: int
    frame_index: int = 0
    upper_person_count: int = 0
    lower_person_count: int = 0

    @property
    def ranking_key(self) -> tuple[int, int, float]:
        return (self.level, self.object_count, self.score)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "score": self.score,
            "sharpness": self.sharpness,
            "category": self.category,
            "object_count": self.object_count,
            "person_count": self.person_count,
            "animal_count": self.animal_count,
            "detected": list(self.detected),
            "level": self.level,
            "width": self.width,
            "height": self.height,
            "frame_index": self.frame_index,
            "upper_person_count": self.upper_person_count,
            "lower_person_count": self.lower_person_count,
        }


@dataclass(slots=True)
class Runtime:
    cv2: Any = None
    yolo_cls: Any = None
    has_cv2: bool = False
    has_yolo: bool = False
    device: str = "cpu"
    half: bool = False


_RUNTIME: Optional[Runtime] = None


def get_runtime(force_reload: bool = False) -> Runtime:
    """Detecta (uma vez) cv2, ultralytics e CUDA. Cacheado em _RUNTIME."""
    global _RUNTIME
    if _RUNTIME is not None and not force_reload:
        return _RUNTIME

    rt = Runtime()
    try:
        import cv2 as _cv2  # type: ignore
        rt.cv2 = _cv2
        rt.has_cv2 = True
    except Exception:
        pass

    try:
        from ultralytics import YOLO as _YOLO  # type: ignore
        rt.yolo_cls = _YOLO
        rt.has_yolo = True
    except Exception:
        pass

    if rt.has_yolo:
        try:
            import torch as _torch  # type: ignore
            if bool(_torch.cuda.is_available()):
                rt.device = "0"
                rt.half = True
        except Exception:
            pass

    _RUNTIME = rt
    return rt


# ─────────────────────────────────────────────────────────────────────────────
# 5. Subprocesso / bootstrap de venv
# ─────────────────────────────────────────────────────────────────────────────
def _run(
    command: Sequence[str],
    quiet: bool,
    timeout: Optional[int] = None,
) -> tuple[bool, str]:
    effective = CONFIG.subprocess_timeout if timeout is None else timeout
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=effective,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)

    output = (result.stderr or "") + (result.stdout or "")
    if result.returncode != 0:
        if not quiet:
            if result.stdout:
                sys.stdout.write(result.stdout)
            if result.stderr:
                sys.stderr.write(result.stderr)
        return False, output

    if not quiet:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
    return True, output


def _install_packages(
    python_bin: str, packages: Sequence[str], quiet: bool
) -> bool:
    args = [
        python_bin, "-m", "pip", "install",
        "--no-input", "--disable-pip-version-check",
    ]
    if quiet:
        args.append("--quiet")
    args.extend(packages)
    ok, _ = _run(args, quiet=quiet, timeout=CONFIG.pip_timeout)
    return ok


def _maybe_bootstrap_venv(requested: EngineName, quiet: bool) -> None:
    """Cria venv local e re-executa se dependências faltam.

    Usa `MELHOR_FOTO_VENV=1` apenas como marcador de re-execução
    (não é parâmetro de configuração). Se a re-execução for bem-sucedida
    o processo é substituído via os.execvpe e esta função não retorna.
    """
    if _os.environ.get("MELHOR_FOTO_VENV") == "1":
        return
    if not CONFIG.auto_install:
        return

    rt = get_runtime()
    needs_yolo = requested in ("auto", "yolo")
    if rt.has_cv2 and (rt.has_yolo or not needs_yolo):
        return

    venv_dir = Path(__file__).resolve().parent / CONFIG.venv_dir_name
    python_bin = venv_dir / "bin" / "python"

    if not python_bin.exists():
        logger.info("Criando venv em %s", venv_dir)
        ok, _ = _run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            quiet=quiet,
            timeout=CONFIG.venv_timeout,
        )
        if not ok:
            logger.warning("Falha ao criar venv; seguindo sem bootstrap")
            return

    missing: list[str] = []
    if not rt.has_cv2:
        missing.append("opencv-python-headless")
    if needs_yolo and not rt.has_yolo:
        missing.append("ultralytics")

    if missing:
        logger.info("Instalando no venv: %s", ", ".join(missing))
        if not _install_packages(str(python_bin), missing, quiet):
            logger.warning("Falha ao instalar %s; seguindo sem bootstrap", missing)
            return

    env = _os.environ.copy()
    env["MELHOR_FOTO_VENV"] = "1"
    try:
        _os.execvpe(str(python_bin), [str(python_bin), *sys.argv], env)
    except OSError as exc:
        logger.error("Falha ao re-executar com venv: %s", exc)


def _prepare_engine(requested: EngineName, quiet: bool) -> Optional[ConcreteEngine]:
    """Resolve motor concreto. Pode criar venv e re-executar (não retorna)."""
    _maybe_bootstrap_venv(requested, quiet)
    rt = get_runtime()

    if requested == "hog":
        return "hog" if rt.has_cv2 else None
    if requested == "yolo":
        if not rt.has_cv2:
            return None
        return "yolo" if rt.has_yolo else "hog"
    # auto
    if rt.has_cv2 and rt.has_yolo:
        return "yolo"
    if rt.has_cv2:
        return "hog"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Coleta de imagens
# ─────────────────────────────────────────────────────────────────────────────
def _has_valid_header(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size < CONFIG.min_image_bytes:
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return False
    return head.startswith(JPEG_MAGIC) or head.startswith(PNG_MAGIC)


def _collect_images(args: argparse.Namespace) -> list[Path]:
    raw_paths: list[str] = []

    if args.list_file:
        list_path = Path(args.list_file).expanduser().resolve()
        if not list_path.is_file():
            raise FileNotFoundError(f"Arquivo de lista não encontrado: {list_path}")
        with list_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                candidate = line.strip()
                if candidate:
                    raw_paths.append(candidate)

    if args.paths:
        raw_paths.extend(args.paths)

    if not raw_paths:
        base = Path(args.dir or _os.getcwd()).expanduser().resolve()
        if base.is_dir():
            for entry in sorted(base.iterdir()):
                if (
                    entry.is_file()
                    and entry.suffix.lower() in CONFIG.image_suffixes
                ):
                    raw_paths.append(str(entry))

    seen: set[Path] = set()
    images: list[Path] = []
    for raw in raw_paths:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        if not resolved.is_file():
            continue
        if resolved.suffix.lower() not in CONFIG.image_suffixes:
            continue
        if not _has_valid_header(resolved):
            logger.warning("Cabeçalho inválido, pulando: %s", resolved.name)
            continue
        seen.add(resolved)
        images.append(resolved)
    return images


def _resolve_model_path(user_model: Optional[str], engine: ConcreteEngine) -> Optional[str]:
    if engine != "yolo":
        return None
    if user_model:
        return user_model
    local = Path(__file__).resolve().parent / CONFIG.default_model_name
    return str(local) if local.exists() else CONFIG.default_model_name


# ─────────────────────────────────────────────────────────────────────────────
# 7. Nitidez
# ─────────────────────────────────────────────────────────────────────────────
def _compute_sharpness(cv2: Any, gray: Any) -> float:
    try:
        value = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return 0.0
    if value != value or value in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, value)


def _sharpness_of_bgr(image: Any, cv2: Any) -> float:
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    except Exception:
        return 0.0
    return _compute_sharpness(cv2, gray)


def _load_bgr(cv2: Any, path: Path) -> Optional[Any]:
    try:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    except Exception as exc:
        logger.warning("imread falhou em %s: %s", path.name, exc)
        return None
    if image is None or getattr(image, "size", 0) == 0:
        return None
    return image


# ─────────────────────────────────────────────────────────────────────────────
# 8. Motor YOLO
# ─────────────────────────────────────────────────────────────────────────────
def _parse_box(
    box: Any,
    image: Any,
    cv2: Any,
    width: int,
    height: int,
    min_person_conf: float,
    min_animal_conf: float,
) -> Optional[tuple[int, str, float]]:
    """Retorna (class_id, label, score) ou None se inválido/irrelevante."""
    try:
        cls_id = int(float(box.cls[0]))
        conf = float(box.conf[0])
        coords = list(box.xyxy[0])
    except Exception:
        return None
    if len(coords) < 4:
        return None

    if cls_id == PERSON_CLASS_ID:
        if conf < min_person_conf:
            return None
    elif cls_id in ANIMAL_IDS:
        if conf < min_animal_conf:
            return None
    else:
        return None

    try:
        x1 = max(0, min(width, int(float(coords[0]))))
        y1 = max(0, min(height, int(float(coords[1]))))
        x2 = max(0, min(width, int(float(coords[2]))))
        y2 = max(0, min(height, int(float(coords[3]))))
    except Exception:
        return None
    if x2 <= x1 or y2 <= y1:
        return None

    roi = image[y1:y2, x1:x2]
    if roi is None or getattr(roi, "size", 0) == 0:
        return None

    try:
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    except Exception:
        return None

    sharpness = _compute_sharpness(cv2, roi_gray)
    score = sharpness * conf
    label = "person" if cls_id == PERSON_CLASS_ID else ANIMAL_LABELS.get(cls_id, "animal")
    return cls_id, label, score


def _record_from_yolo(
    path: Path,
    image: Any,
    result: Any,
    cv2: Any,
    min_person_conf: float,
    min_animal_conf: float,
) -> Optional[DetectionRecord]:
    if result is None or image is None:
        return None
    try:
        height, width = int(image.shape[0]), int(image.shape[1])
    except Exception:
        return None

    person_count = 0
    upper_person_count = 0
    lower_person_count = 0
    animal_count = 0
    best_person = 0.0
    best_animal = 0.0
    detected: list[str] = []

    boxes = getattr(result, "boxes", None)
    if boxes is not None:
        try:
            iterator = iter(boxes)
        except TypeError:
            iterator = iter(())
        for box in iterator:
            parsed = _parse_box(
                box, image, cv2, width, height,
                min_person_conf, min_animal_conf,
            )
            if parsed is None:
                continue
            cls_id, label, score = parsed
            if cls_id == PERSON_CLASS_ID:
                person_count += 1
                try:
                    coords = list(box.xyxy[0])
                    center_y = (float(coords[1]) + float(coords[3])) / 2.0
                    if center_y < height / 2.0:
                        upper_person_count += 1
                    else:
                        lower_person_count += 1
                except (AttributeError, IndexError, TypeError, ValueError):
                    lower_person_count += 1
                if "person" not in detected:
                    detected.append("person")
                best_person = max(best_person, score)
            else:
                animal_count += 1
                if label not in detected:
                    detected.append(label)
                best_animal = max(best_animal, score)

    if person_count > 1:
        level, count, category = LEVEL_MULTIPLE_PEOPLE, person_count, "multiple_people"
        score = best_person
    elif person_count == 1:
        level, count, category = LEVEL_PERSON, 1, "person"
        score = best_person
    elif animal_count > 0:
        level, count, category = LEVEL_ANIMAL, animal_count, "animal"
        score = best_animal
    else:
        level, count, category = LEVEL_SHARPNESS, 0, "sharpness"
        detected = ["sharpness"]
        score = 0.0

    if score <= 0.0:
        score = _sharpness_of_bgr(image, cv2)

    return DetectionRecord(
        path=str(path),
        score=float(score),
        sharpness=float(score),
        category=category,
        object_count=int(count),
        person_count=int(person_count),
        animal_count=int(animal_count),
        detected=tuple(detected),
        level=int(level),
        width=width,
        height=height,
        upper_person_count=upper_person_count,
        lower_person_count=lower_person_count,
    )


def _detect_yolo(
    paths: Sequence[Path],
    model_path: str,
    min_conf: float,
    quiet: bool,
) -> tuple[Optional[Path], float, Optional[SelectionResult], list[DetectionRecord]]:
    rt = get_runtime()
    if not rt.has_yolo or not rt.has_cv2:
        logger.error("YOLO/OpenCV indisponíveis")
        return None, -1.0, None, []

    cv2 = rt.cv2
    try:
        with redirect_stdout(sys.stderr):
            model = rt.yolo_cls(model_path)
    except Exception as exc:
        logger.error("Falha ao carregar modelo YOLO %s: %s", model_path, exc)
        return None, -1.0, None, []

    batch_size = CONFIG.yolo_batch_size
    imgsz = CONFIG.yolo_imgsz
    device = rt.device
    half = rt.half
    min_person_conf = CONFIG.yolo_person_min_conf
    min_animal_conf = CONFIG.yolo_animal_min_conf

    logger.info(
        "YOLO: imagens=%d batch=%d imgsz=%d device=%s half=%s "
        "conf_pessoa=%.2f conf_animal=%.2f threads=%d",
        len(paths), batch_size, imgsz, device, half,
        min_person_conf, min_animal_conf, CONFIG.threads_per_process,
    )

    records: list[DetectionRecord] = []
    best: Optional[DetectionRecord] = None

    def _absorb(rec: Optional[DetectionRecord]) -> None:
        nonlocal best
        if rec is None:
            return
        rec.frame_index = len(records)
        records.append(rec)
        if best is None or rec.ranking_key > best.ranking_key:
            best = rec

    for start in range(0, len(paths), batch_size):
        chunk = list(paths[start:start + batch_size])
        arrays: list[Any] = []
        kept: list[Path] = []
        for p in chunk:
            arr = _load_bgr(cv2, p)
            if arr is None:
                continue
            arrays.append(arr)
            kept.append(p)

        if not arrays:
            continue

        results: Optional[Sequence[Any]] = None
        try:
            with redirect_stdout(sys.stderr):
                raw = model(
                    arrays,
                    verbose=False,
                    conf=min_conf,
                    imgsz=imgsz,
                    device=device,
                    half=half,
                )
            results = list(raw) if raw is not None else None
        except Exception as exc:
            logger.warning("Lote YOLO falhou (%d itens): %s", len(arrays), exc)
            results = None

        if results is None or len(results) != len(arrays):
            # Fallback individual — garante que itens bons não são perdidos
            for arr, p in zip(arrays, kept):
                try:
                    with redirect_stdout(sys.stderr):
                        single = model(
                            arr,
                            verbose=False,
                            conf=min_conf,
                            imgsz=imgsz,
                            device=device,
                            half=half,
                        )
                    single_list = list(single) if single is not None else []
                except Exception as exc:
                    logger.warning("YOLO individual falhou em %s: %s", p.name, exc)
                    continue
                if not single_list:
                    continue
                _absorb(
                    _record_from_yolo(
                        p, arr, single_list[0], cv2,
                        min_person_conf, min_animal_conf,
                    )
                )
            continue

        for arr, p, res in zip(arrays, kept, results):
            _absorb(
                _record_from_yolo(
                    p, arr, res, cv2,
                    min_person_conf, min_animal_conf,
                )
            )

    if best is None:
        logger.info("YOLO não produziu candidatos válidos.")
        return None, -1.0, None, records

    result = SelectionResult(
        category=best.category,
        object_count=best.object_count,
        detections=best.detected,
    )
    return Path(best.path), best.score, result, records


# ─────────────────────────────────────────────────────────────────────────────
# 9. Motor HOG
# ─────────────────────────────────────────────────────────────────────────────
def _detect_hog(
    paths: Sequence[Path],
    quiet: bool,
) -> tuple[Optional[Path], float, Optional[SelectionResult], list[DetectionRecord]]:
    rt = get_runtime()
    if not rt.has_cv2:
        logger.error("OpenCV indisponível para HOG")
        return None, -1.0, None, []

    cv2 = rt.cv2
    try:
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    except Exception as exc:
        logger.error("Falha ao inicializar HOG: %s", exc)
        return None, -1.0, None, []

    logger.info("HOG: %d imagem(ns)", len(paths))

    records: list[DetectionRecord] = []
    best: Optional[DetectionRecord] = None

    for path in paths:
        image = _load_bgr(cv2, path)
        if image is None:
            logger.warning("Imagem ilegível: %s", path.name)
            continue

        try:
            rects, weights = hog.detectMultiScale(
                image, winStride=(4, 4), padding=(8, 8), scale=1.05
            )
        except Exception as exc:
            logger.warning("HOG falhou em %s: %s", path.name, exc)
            rects, weights = [], []

        person_count = int(len(rects))
        height, width = int(image.shape[0]), int(image.shape[1])

        if person_count == 0:
            score = _sharpness_of_bgr(image, cv2)
            record = DetectionRecord(
                path=str(path),
                score=float(score),
                sharpness=float(score),
                category="sharpness",
                object_count=0,
                person_count=0,
                animal_count=0,
                detected=("sharpness",),
                level=LEVEL_SHARPNESS,
                width=width,
                height=height,
            )
        else:
            best_score = 0.0
            weights_list = list(weights) if weights is not None else []
            for index, rect in enumerate(rects):
                try:
                    x, y, w, h = map(int, rect)
                except Exception:
                    continue
                if w <= 0 or h <= 0:
                    continue
                roi = image[y:y + h, x:x + w]
                if roi is None or getattr(roi, "size", 0) == 0:
                    continue
                try:
                    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                except Exception:
                    continue
                weight = 1.0
                if index < len(weights_list):
                    try:
                        weight = float(weights_list[index])
                    except (TypeError, ValueError):
                        weight = 1.0
                score_current = _compute_sharpness(cv2, roi_gray) * max(0.0, weight)
                best_score = max(best_score, score_current)

            if best_score <= 0.0:
                best_score = _sharpness_of_bgr(image, cv2)

            level = LEVEL_MULTIPLE_PEOPLE if person_count > 1 else LEVEL_PERSON
            record = DetectionRecord(
                path=str(path),
                score=float(best_score),
                sharpness=float(best_score),
                category="multiple_people" if level == LEVEL_MULTIPLE_PEOPLE else "person",
                object_count=person_count,
                person_count=person_count,
                animal_count=0,
                detected=("person",),
                level=level,
                width=width,
                height=height,
            )

        record.frame_index = len(records)
        records.append(record)
        if best is None or record.ranking_key > best.ranking_key:
            best = record

    if best is None:
        logger.info("HOG não produziu candidatos válidos.")
        return None, -1.0, None, records

    result = SelectionResult(
        category=best.category,
        object_count=best.object_count,
        detections=best.detected,
    )
    return Path(best.path), best.score, result, records


# ─────────────────────────────────────────────────────────────────────────────
# 10. Fallback por nitidez
# ─────────────────────────────────────────────────────────────────────────────
def _detect_sharpness(
    paths: Sequence[Path],
) -> tuple[Optional[Path], float, Optional[SelectionResult], list[DetectionRecord]]:
    rt = get_runtime()
    if not rt.has_cv2:
        logger.error("OpenCV indisponível para fallback por nitidez")
        return None, -1.0, None, []

    cv2 = rt.cv2
    logger.info("Nitidez: %d imagem(ns)", len(paths))

    records: list[DetectionRecord] = []
    best: Optional[DetectionRecord] = None

    for path in paths:
        try:
            gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if gray is not None and CONFIG.active_camera_only:
                height = gray.shape[0]
                if height >= 2:
                    gray = gray[height // 2:, :]
        except Exception:
            gray = None
        if gray is None or getattr(gray, "size", 0) == 0:
            logger.warning("Imagem ilegível: %s", path.name)
            continue

        score = _compute_sharpness(cv2, gray)
        height, width = int(gray.shape[0]), int(gray.shape[1])
        record = DetectionRecord(
            path=str(path),
            score=float(score),
            sharpness=float(score),
            category="sharpness",
            object_count=0,
            person_count=0,
            animal_count=0,
            detected=("sharpness",),
            level=LEVEL_SHARPNESS,
            width=width,
            height=height,
            frame_index=len(records),
        )
        records.append(record)
        if best is None or record.ranking_key > best.ranking_key:
            best = record

    if best is None:
        logger.info("Fallback por nitidez não produziu candidatos.")
        return None, -1.0, None, records

    result = SelectionResult(
        category="sharpness",
        object_count=0,
        detections=("sharpness",),
    )
    return Path(best.path), best.score, result, records


# ─────────────────────────────────────────────────────────────────────────────
# 11. Saída
# ─────────────────────────────────────────────────────────────────────────────
def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        _os.replace(tmp_name, path)
    except Exception:
        try:
            _os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _emit_result(
    args: argparse.Namespace,
    winner: Path,
    score: float,
    result: Optional[SelectionResult],
    records: Sequence[DetectionRecord],
) -> None:
    category = result.category if result else "sharpness"
    obj_count = result.object_count if result else 0
    detections = list(result.detections) if result else ["sharpness"]

    if args.score_output:
        try:
            target = Path(args.score_output).expanduser().resolve()
            _atomic_write_text(target, f"{score:.6f}\n")
        except Exception as exc:
            logger.warning("Falha ao gravar score em %s: %s", args.score_output, exc)

    fmt: OutputFormat = args.output
    if fmt == "path":
        print(str(winner))
    elif fmt == "json":
        payload: dict[str, Any] = {
            "path": str(winner),
            "score": float(score),
            "category": category,
            "object_count": int(obj_count),
            "detected": detections,
            "candidates": [r.to_dict() for r in records],
        }
        print(json.dumps(payload, ensure_ascii=True))
    else:
        print(f"VENCEDORA: {winner.name} | Categoria: {category} | Qtd: {obj_count}")


# ─────────────────────────────────────────────────────────────────────────────
# 12. CLI
# ─────────────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seleciona a melhor foto com hierarquia rígida.",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.add_argument("--list-file",
                        help="Arquivo com lista de caminhos de imagens.")
    parser.add_argument("--dir", help="Diretório para buscar imagens.")
    parser.add_argument("--model", default=None,
                        help="Caminho/nome do modelo YOLO.")
    parser.add_argument("--min-conf", type=float, default=0.5,
                        help="Confiança mínima do YOLO.")
    parser.add_argument("--engine", choices=["auto", "yolo", "hog"], default="auto")
    parser.add_argument("--output", choices=["text", "path", "json"], default="text")
    parser.add_argument("--score-output",
                        help="Arquivo para gravar a pontuação.")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--log-file", default=None)
    parser.add_argument("paths", nargs="*")
    return parser


def _configure_logging(log_file: Optional[str]) -> None:
    if not log_file:
        logger.setLevel(logging.WARNING)
        return
    path = Path(log_file).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(path), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


# ─────────────────────────────────────────────────────────────────────────────
# 13. Main
# ─────────────────────────────────────────────────────────────────────────────
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.log_file)
    quiet: bool = bool(args.quiet) or args.output in {"path", "json"}
    requested: EngineName = cast(EngineName, args.engine)

    engine = _prepare_engine(requested, quiet)
    if engine is None:
        msg = "Nenhum motor disponível (cv2/ultralytics ausentes)."
        logger.error(msg)
        print(msg, file=sys.stderr)
        return 1

    try:
        images = _collect_images(args)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 1

    if not images:
        logger.warning("Nenhuma imagem encontrada.")
        return 2

    model_path = _resolve_model_path(args.model, engine)

    winner: Optional[Path] = None
    best_score: float = -1.0
    result: Optional[SelectionResult] = None
    records: list[DetectionRecord] = []

    start = time.monotonic()
    try:
        if engine == "yolo":
            winner, best_score, result, records = _detect_yolo(
                images,
                model_path or CONFIG.default_model_name,
                args.min_conf,
                quiet,
            )
        else:
            winner, best_score, result, records = _detect_hog(images, quiet)
    except Exception as exc:
        logger.exception("Erro no motor %s: %s", engine, exc)

    if winner is None:
        logger.info("Nenhuma detecção — aplicando fallback por nitidez.")
        try:
            winner, best_score, result, records = _detect_sharpness(images)
        except Exception as exc:
            logger.exception("Erro no fallback por nitidez: %s", exc)
            return 1

    if winner is None:
        logger.error("Nenhuma imagem válida encontrada.")
        return 2

    elapsed = time.monotonic() - start
    logger.info(
        "Motor=%s candidatos=%d vencedor=%s score=%.2f categoria=%s tempo=%.1fs",
        engine, len(images), winner.name, best_score,
        result.category if result else "sharpness", elapsed,
    )

    _emit_result(args, winner, best_score, result, records)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(130)