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
    # A VM tem ~1 GB de RAM e já operava com swap intenso durante lotes de
    # quatro quadros. Um quadro por chamada reduz o pico de memória e grava
    # checkpoints com granularidade suficiente para retomar sem repetir lotes.
    yolo_batch_size: int = 1
    yolo_imgsz: int = 640
    # Modo imediato: inferência-base enxuta; a análise completa e os recortes
    # em alta resolução ficam para o aprimorado, sem bloquear a prioridade.
    yolo_fast_batch_size: int = 1
    yolo_fast_imgsz: int = 416
    yolo_fast_crop_imgsz: int = 416
    yolo_person_min_conf: float = 0.60
    yolo_animal_min_conf: float = 0.50
    # Segundo passe da metade ainda sem objeto relevante. Cada câmera passa
    # a ocupar a imagem inteira da inferência, evitando perder uma pessoa
    # pequena quando a outra metade já teve detecção no quadro composto.
    yolo_crop_imgsz: int = 960
    # Passo por metade é confirmação de classe, não simples caça a candidatos;
    # um limiar moderado evita converter formas ambíguas em pessoas.
    yolo_crop_person_min_conf: float = 0.50
    # Objetos pequenos na câmera móvel podem vir abaixo do limiar global de
    # animais; flexibilizamos só no recorte, preservando o filtro do quadro.
    yolo_crop_animal_min_conf: float = 0.45
    # Resgate barato e conservador do ângulo inferior: roda só quando o passe
    # normal não encontrou animal algum e limita a quantidade de frames.
    yolo_lower_rescue_conf: float = 0.10
    yolo_lower_rescue_min_frames: int = 2
    yolo_lower_rescue_max_frame_gap: int = 5
    yolo_lower_rescue_min_iou: float = 0.25
    yolo_lower_rescue_max_frames: int = 16
    # Região central da câmera inferior: passe complementar em até o mesmo
    # orçamento limitado usado pelo resgate inferior de animais.
    yolo_lower_center_width: float = 0.60
    yolo_lower_center_height: float = 0.60
    yolo_lower_center_max_frames: int = 8
    # Passe de baixa confiança para gatos noturnos na câmera inferior. A
    # metade inteira preserva contexto; a contagem exige trilha em vários frames.
    yolo_lower_cat_rescue_conf: float = 0.001
    yolo_lower_cat_rescue_min_frames: int = 3
    yolo_lower_cat_rescue_max_frame_gap: int = 12
    yolo_lower_cat_rescue_min_iou: float = 0.25
    yolo_lower_cat_rescue_max_frames: int = 40
    # O limiar ultrabaixo existe para gatos noturnos. Em luz do dia, folhas
    # e sombras viram gatos com confiança baixa, então o resgate é desativado.
    yolo_lower_cat_rescue_max_mean_luma: float = 95.0
    yolo_vehicle_min_conf: float = 0.45

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
import re
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
CAT_CLASS_ID: Final[int] = 15
VEHICLE_LABELS: Final[dict[int, str]] = {
    2: "car", 3: "motorcycle", 5: "bus", 7: "truck",
}
VEHICLE_IDS: Final[frozenset[int]] = frozenset(VEHICLE_LABELS)

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
    adaptive_sweep: Optional[dict[str, Any]] = None


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
    upper_animal_count: int = 0
    lower_animal_count: int = 0
    vehicle_count: int = 0
    upper_vehicle_count: int = 0
    lower_vehicle_count: int = 0
    # Sharpness from detected person crops (mean Laplacian variance), kept
    # separate from whole-frame sharpness and the confidence-weighted score.
    person_sharpness: float = 0.0
    frame_sharpness: float = 0.0
    # Detecção fraca de gato confirmada por recorrência temporal. Continua
    # valendo para a contagem, mas não deve reposicionar o corte sozinha.
    cat_rescue: bool = False
    clear_person_count: int = 0
    upper_clear_person_count: int = 0
    lower_clear_person_count: int = 0
    clear_animal_count: int = 0
    upper_clear_animal_count: int = 0
    lower_clear_animal_count: int = 0
    person_visible_area: float = 0.0

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
            "upper_animal_count": self.upper_animal_count,
            "lower_animal_count": self.lower_animal_count,
            "vehicle_count": self.vehicle_count,
            "upper_vehicle_count": self.upper_vehicle_count,
            "lower_vehicle_count": self.lower_vehicle_count,
            "person_sharpness": self.person_sharpness,
            "frame_sharpness": self.frame_sharpness,
            "cat_rescue": self.cat_rescue,
            "clear_person_count": self.clear_person_count,
            "upper_clear_person_count": self.upper_clear_person_count,
            "lower_clear_person_count": self.lower_clear_person_count,
            "clear_animal_count": self.clear_animal_count,
            "upper_clear_animal_count": self.upper_clear_animal_count,
            "lower_clear_animal_count": self.lower_clear_animal_count,
            "person_visible_area": self.person_visible_area,
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

    # O alerta e a execução manual devem compartilhar o mesmo ambiente do
    # projeto. Criar outro venv dentro de processamento_img pode deixá-lo
    # parcialmente instalado e mascarar YOLO como HOG.
    venv_dir = (
        Path(__file__).resolve().parent.parent / CONFIG.venv_dir_name
    )
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

    # O processo chamador pode ser o Python do sistema, enquanto o venv do
    # projeto já contém YOLO. Verifique o destino antes de decidir instalar:
    # usar ``rt`` daqui reinstalaria dependências a cada chamada externa.
    probe = [str(python_bin), "-c", "import cv2"]
    if needs_yolo:
        probe[-1] += "; import ultralytics"
    venv_ready, _ = _run(probe, quiet=True, timeout=30)
    if venv_ready:
        env = _os.environ.copy()
        env["MELHOR_FOTO_VENV"] = "1"
        try:
            _os.execvpe(str(python_bin), [str(python_bin), *sys.argv], env)
        except OSError as exc:
            logger.error("Falha ao re-executar com venv: %s", exc)
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
        if not rt.has_cv2 or not rt.has_yolo:
            logger.error("YOLO solicitado, mas ultralytics/OpenCV está indisponível")
            return None
        return "yolo"
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
) -> Optional[tuple[int, str, float, float, int, int]]:
    """Retorna classe, rótulo, score, nitidez e dimensões da caixa."""
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
    elif cls_id in VEHICLE_IDS:
        if conf < CONFIG.yolo_vehicle_min_conf:
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
    label = (
        "person" if cls_id == PERSON_CLASS_ID
        else ANIMAL_LABELS.get(cls_id, VEHICLE_LABELS.get(cls_id, "animal"))
    )
    return cls_id, label, score, sharpness, x2 - x1, y2 - y1


def _record_from_yolo(
    path: Path,
    image: Any,
    result: Any,
    cv2: Any,
    min_person_conf: float,
    min_animal_conf: float,
    stacked: bool = True,
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
    upper_animal_count = 0
    lower_animal_count = 0
    vehicle_count = 0
    upper_vehicle_count = 0
    lower_vehicle_count = 0
    best_person = 0.0
    best_animal = 0.0
    person_sharpness_sum = 0.0
    person_sharpness_count = 0
    detected: list[str] = []
    upper_clear_person_count = lower_clear_person_count = 0
    upper_clear_animal_count = lower_clear_animal_count = 0
    person_visible_area = 0.0
    view_height = height / 2.0 if stacked else float(height)
    view_area = max(1.0, width * view_height)

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
            cls_id, label, score, crop_sharpness, box_width, box_height = parsed
            clear_person = (box_height >= view_height * 0.14
                            and box_width >= width * 0.025
                            and crop_sharpness >= 40.0)
            clear_animal = (box_height >= view_height * 0.08
                            and box_width >= width * 0.02
                            and crop_sharpness >= 40.0)
            if cls_id == PERSON_CLASS_ID:
                person_count += 1
                person_sharpness_sum += crop_sharpness
                person_sharpness_count += 1
                person_visible_area += min(0.25, box_width * box_height / view_area)
                try:
                    coords = list(box.xyxy[0])
                    center_y = (float(coords[1]) + float(coords[3])) / 2.0
                    if center_y < height / 2.0:
                        upper_person_count += 1
                        upper_clear_person_count += int(clear_person)
                    else:
                        lower_person_count += 1
                        lower_clear_person_count += int(clear_person)
                except (AttributeError, IndexError, TypeError, ValueError):
                    lower_person_count += 1
                    lower_clear_person_count += int(clear_person)
                if "person" not in detected:
                    detected.append("person")
                best_person = max(best_person, score)
            elif cls_id in VEHICLE_IDS:
                vehicle_count += 1
                try:
                    coords = list(box.xyxy[0])
                    center_y = (float(coords[1]) + float(coords[3])) / 2.0
                    if center_y < height / 2.0:
                        upper_vehicle_count += 1
                    else:
                        lower_vehicle_count += 1
                except (AttributeError, IndexError, TypeError, ValueError):
                    lower_vehicle_count += 1
                if label not in detected:
                    detected.append(label)
                best_animal = max(best_animal, score)
            else:
                animal_count += 1
                try:
                    coords = list(box.xyxy[0])
                    center_y = (float(coords[1]) + float(coords[3])) / 2.0
                    if center_y < height / 2.0:
                        upper_animal_count += 1
                        upper_clear_animal_count += int(clear_animal)
                    else:
                        lower_animal_count += 1
                        lower_clear_animal_count += int(clear_animal)
                except (AttributeError, IndexError, TypeError, ValueError):
                    lower_animal_count += 1
                    lower_clear_animal_count += int(clear_animal)
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
    elif vehicle_count > 0:
        level, count, category = LEVEL_SHARPNESS, vehicle_count, "vehicle"
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
        upper_animal_count=upper_animal_count,
        lower_animal_count=lower_animal_count,
        vehicle_count=vehicle_count,
        upper_vehicle_count=upper_vehicle_count,
        lower_vehicle_count=lower_vehicle_count,
        clear_person_count=upper_clear_person_count + lower_clear_person_count,
        upper_clear_person_count=upper_clear_person_count,
        lower_clear_person_count=lower_clear_person_count,
        clear_animal_count=upper_clear_animal_count + lower_clear_animal_count,
        upper_clear_animal_count=upper_clear_animal_count,
        lower_clear_animal_count=lower_clear_animal_count,
        person_visible_area=person_visible_area,
        person_sharpness=(
            person_sharpness_sum / person_sharpness_count
            if person_sharpness_count else 0.0
        ),
        frame_sharpness=_sharpness_of_bgr(image, cv2),
    )


def _box_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    area_b = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0.0 else 0.0


def _select_lower_rescue_records(
    records: Sequence[DetectionRecord], max_frames: int,
) -> list[DetectionRecord]:
    """Seleciona até N frames de vídeo, mantendo amostra densa no início."""
    by_source: dict[Path, list[DetectionRecord]] = {}
    for record in records:
        path = Path(record.path)
        if re.search(r"frame[_-](\d+)", path.stem, re.IGNORECASE) is None:
            continue
        if record.lower_animal_count > 0:
            continue
        by_source.setdefault(path.parent.resolve(), []).append(record)

    selected: list[DetectionRecord] = []
    limit = max(0, int(max_frames))
    if limit == 0:
        return selected
    source_items = list(by_source.items())
    base_budget, remainder = divmod(limit, max(1, len(source_items)))
    for source_index, (_source, source_records) in enumerate(source_items):
        source_limit = base_budget + (1 if source_index < remainder else 0)
        if source_limit <= 0:
            continue
        ordered = sorted(
            source_records,
            key=lambda item: int(re.search(
                r"frame[_-](\d+)", Path(item.path).stem, re.IGNORECASE
            ).group(1)),
        )
        if len(ordered) <= source_limit:
            selected.extend(ordered)
            continue
        # Aos 15 fps, dez frames cobrem os primeiros ~0,6 s: suficiente para
        # preservar eventos já em andamento no instante em que a câmera grava.
        dense_prefix = min(10, source_limit, len(ordered))
        indexes = set(range(dense_prefix))
        remaining_slots = source_limit - dense_prefix
        remaining = len(ordered) - dense_prefix
        if remaining_slots > 0 and remaining > 0:
            if remaining_slots >= remaining:
                indexes.update(range(dense_prefix, len(ordered)))
            elif remaining_slots == 1:
                indexes.add(dense_prefix + (remaining - 1) // 2)
            else:
                for slot in range(remaining_slots):
                    offset = round(slot * (remaining - 1) / (remaining_slots - 1))
                    indexes.add(dense_prefix + offset)
        selected.extend(ordered[index] for index in sorted(indexes))
    return selected


def _select_lower_cat_rescue_records(
    records: Sequence[DetectionRecord], max_frames: int,
) -> list[DetectionRecord]:
    """Distribui a verificação de gato pela timeline completa dos vídeos."""
    limit = max(0, int(max_frames))
    by_source: dict[Path, list[DetectionRecord]] = {}
    for record in records:
        path = Path(record.path)
        if re.search(r"frame[_-](\d+)", path.stem, re.IGNORECASE) is None:
            continue
        if "cat" in record.detected:
            continue
        by_source.setdefault(path.parent.resolve(), []).append(record)

    if limit == 0 or not by_source:
        return []
    sources = list(by_source.items())
    base, remainder = divmod(limit, len(sources))
    selected: list[DetectionRecord] = []
    for source_index, (_source, source_records) in enumerate(sources):
        ordered = sorted(
            source_records,
            key=lambda record: int(re.search(
                r"frame[_-](\d+)", Path(record.path).stem, re.IGNORECASE
            ).group(1)),
        )
        budget = min(len(ordered), base + (1 if source_index < remainder else 0))
        if budget <= 0:
            continue
        if budget == 1:
            indexes = {len(ordered) // 2}
        else:
            indexes = {
                round(slot * (len(ordered) - 1) / (budget - 1))
                for slot in range(budget)
            }
        selected.extend(ordered[index] for index in sorted(indexes))
    return selected


def _is_low_light_for_cat_rescue(
    mean_luma: float, max_mean_luma: float,
) -> bool:
    return float(mean_luma) <= float(max_mean_luma)


def _select_crowded_lower_records(
    records: Sequence[DetectionRecord], max_frames: int,
) -> list[DetectionRecord]:
    """Reserva recortes centrais para frames com várias pessoas na vista inferior."""
    limit = max(0, int(max_frames))
    grouped: dict[Path, list[DetectionRecord]] = {}
    for record in records:
        path = Path(record.path)
        if re.search(r"frame[_-](\d+)", path.stem, re.IGNORECASE) is None:
            continue
        grouped.setdefault(path.parent.resolve(), []).append(record)

    selected: list[DetectionRecord] = []
    groups = list(grouped.values())
    base, remainder = divmod(limit, max(1, len(groups)))
    for group_index, group in enumerate(groups):
        budget = base + (1 if group_index < remainder else 0)
        ordered = sorted(
            group,
            key=lambda record: int(re.search(
                r"frame[_-](\d+)", Path(record.path).stem, re.IGNORECASE
            ).group(1)),
        )
        if budget <= 0:
            continue
        # A amostragem YOLO também reserva um quadro perto de 87,5% do clipe.
        # Sempre faça o recorte central nele, pois a contagem preliminar ainda
        # pode não refletir pessoas pequenas ou parcialmente sobrepostas.
        late_anchor = ordered[round((len(ordered) - 1) * 0.875)]
        selected.append(late_anchor)
        budget -= 1
        crowded = [
            record for record in ordered
            if record is not late_anchor and record.lower_person_count >= 2
        ]
        if budget <= 0:
            continue
        if not crowded:
            continue
        ordered = crowded
        if len(ordered) <= budget:
            selected.extend(ordered)
            continue
        # Distribui o orçamento pelos momentos de maior lotação sem concentrar
        # todas as inferências nos primeiros frames do clipe.
        for slot in range(budget):
            start = round(slot * len(ordered) / budget)
            end = round((slot + 1) * len(ordered) / budget)
            segment = ordered[start:max(start + 1, end)]
            selected.append(max(
                segment,
                key=lambda record: (
                    record.lower_person_count,
                    record.person_sharpness,
                ),
            ))
    return selected


def _confirmed_lower_animal_tracks(
    observations: Sequence[
        tuple[Path, int, int, float, tuple[float, float, float, float]]
    ],
    min_frames: int,
    max_frame_gap: int,
    min_iou: float,
) -> dict[Path, tuple[int, float, tuple[str, ...]]]:
    """Confirma animais de baixa confiança apenas com recorrência espacial."""
    groups: dict[
        Path,
        list[tuple[Path, int, int, float, tuple[float, float, float, float]]],
    ] = {}
    for path, frame_id, class_id, confidence, box in observations:
        groups.setdefault(path.parent.resolve(), []).append(
            (path.resolve(), frame_id, class_id, confidence, box)
        )

    confirmed: dict[Path, tuple[int, float, tuple[str, ...]]] = {}
    for group in groups.values():
        group.sort(key=lambda item: item[1])
        parents = list(range(len(group)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(first: int, second: int) -> None:
            first_root, second_root = find(first), find(second)
            if first_root != second_root:
                parents[second_root] = first_root

        for first_index, first in enumerate(group):
            for second_index in range(first_index + 1, len(group)):
                second = group[second_index]
                gap = second[1] - first[1]
                if gap > max_frame_gap:
                    break
                if gap <= 0 or first[2] != second[2]:
                    continue
                if _box_iou(first[4], second[4]) >= min_iou:
                    union(first_index, second_index)

        tracks: dict[int, list[int]] = {}
        for index, _item in enumerate(group):
            tracks.setdefault(find(index), []).append(index)
        for indexes in tracks.values():
            frame_ids = {group[index][1] for index in indexes}
            if len(frame_ids) < min_frames:
                continue
            tracks_by_path: dict[Path, tuple[float, set[str]]] = {}
            for index in indexes:
                path, _frame_id, class_id, confidence, _box = group[index]
                label = ANIMAL_LABELS.get(class_id, "animal")
                path_confidence, labels = tracks_by_path.setdefault(
                    path, (0.0, set())
                )
                labels.add(label)
                tracks_by_path[path] = (max(path_confidence, confidence), labels)
            for path, (confidence, labels) in tracks_by_path.items():
                old_count, old_confidence, old_labels = confirmed.get(
                    path, (0, 0.0, ())
                )
                confirmed[path] = (
                    old_count + 1,
                    max(old_confidence, confidence),
                    tuple(dict.fromkeys((*old_labels, *sorted(labels)))),
                )
    return confirmed


def _adaptive_evidence_by_view(
    records: Sequence[DetectionRecord],
) -> dict[str, dict[tuple[str, int], int]]:
    evidence: dict[str, dict[tuple[str, int], int]] = {
        "upper_person": {}, "lower_person": {},
        "upper_animal": {}, "lower_animal": {},
        "upper_vehicle": {}, "lower_vehicle": {},
    }
    for record in records:
        path = Path(record.path)
        match = re.search(r"frame[_-](\d+)", path.stem, re.IGNORECASE)
        if match is None:
            continue
        key = (str(path.parent.resolve()), int(match.group(1)))
        for view, count in (
            ("upper_person", record.upper_person_count),
            ("lower_person", record.lower_person_count),
            ("upper_animal", record.upper_animal_count),
            ("lower_animal", record.lower_animal_count),
            ("upper_vehicle", record.upper_vehicle_count),
            ("lower_vehicle", record.lower_vehicle_count),
        ):
            if count > 0:
                evidence[view][key] = count
    return evidence


def _needs_adaptive_frame_sweep(records: Sequence[DetectionRecord]) -> bool:
    """Sweep omitted frames for missing per-class recurrence or unstable counts."""
    evidence = _adaptive_evidence_by_view(records)
    has_people_or_animals = any(
        evidence[key] for key in (
            "upper_person", "lower_person", "upper_animal", "lower_animal",
        )
    )
    recurring_vehicle_frames = (
        set(evidence["upper_vehicle"]) | set(evidence["lower_vehicle"])
    )
    if not has_people_or_animals and len(recurring_vehicle_frames) >= 2:
        # A recurring vehicle is enough to localize an arrival. An additional
        # full sweep would mostly repeat checks for unrelated people/animals.
        return False
    for half in ("upper", "lower"):
        frame_ids = set(evidence[f"{half}_person"]) | set(evidence[f"{half}_animal"])
        if len(frame_ids) < 2:
            return True
    for observations in evidence.values():
        if not observations:
            continue
        counts = list(observations.values())
        if len(counts) < 2 or max(counts, default=0) > min(counts, default=0):
            return True
    return False


def _adaptive_frame_sweep_reason(records: Sequence[DetectionRecord]) -> str:
    """Explain whether the supplemental sweep addresses recurrence or count instability."""
    evidence = _adaptive_evidence_by_view(records)
    for half in ("upper", "lower"):
        frame_ids = set(evidence[f"{half}_person"]) | set(evidence[f"{half}_animal"])
        if len(frame_ids) < 2:
            return "recorrência_temporal_insuficiente_por_metade"
    active = [counts for counts in evidence.values() if counts]
    if any(len(counts) < 2 for counts in active):
        return "recorrência_temporal_insuficiente"
    if any(len(set(counts.values())) > 1 for counts in active):
        return "contagem_instável_entre_frames"
    return "nenhum"


def _adaptive_evidence_summary(records: Sequence[DetectionRecord]) -> str:
    evidence = _adaptive_evidence_by_view(records)
    return ",".join(
        f"{view}={len(observations)}x/{max(observations.values(), default=0)}"
        for view, observations in evidence.items()
    )


def _frame_input_signature(path: Path) -> Optional[list[Any]]:
    try:
        resolved = path.resolve()
        stat = resolved.stat()
        return [
            str(resolved), stat.st_size, stat.st_mtime_ns,
            int(getattr(stat, "st_ino", 0)),
        ]
    except OSError:
        return None


def _detection_record_from_dict(value: object) -> Optional[DetectionRecord]:
    if not isinstance(value, dict):
        return None
    try:
        record = dict(value)
        record["detected"] = tuple(record.get("detected", ()))
        return DetectionRecord(**record)
    except (TypeError, ValueError):
        return None


def _load_detection_checkpoint(
    checkpoint_path: Optional[Path],
    paths: Sequence[Path],
    identity: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if checkpoint_path is None:
        return {}
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("identity") != identity
            or not isinstance(payload.get("records"), dict)
        ):
            return {}
        allowed = {str(path.resolve()) for path in paths}
        result: dict[str, dict[str, Any]] = {}
        for key, entry in payload["records"].items():
            if key not in allowed or not isinstance(entry, dict):
                continue
            path = Path(key)
            if entry.get("signature") != _frame_input_signature(path):
                continue
            base = _detection_record_from_dict(entry.get("base"))
            if base is None:
                continue
            refined = _detection_record_from_dict(entry.get("refined"))
            result[key] = {
                "signature": entry["signature"],
                "stage": entry.get("stage", "primary"),
                "base": base,
                "refined": refined,
            }
        return result
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _save_detection_checkpoint(
    checkpoint_path: Optional[Path],
    identity: dict[str, Any],
    records: dict[str, dict[str, Any]],
) -> None:
    if checkpoint_path is None:
        return
    payload = {
        "version": 1,
        "identity": identity,
        "records": {
            path: {
                "signature": entry["signature"],
                "stage": entry["stage"],
                "base": entry["base"].to_dict(),
                "refined": (
                    entry["refined"].to_dict()
                    if entry.get("refined") is not None else None
                ),
            }
            for path, entry in records.items()
        },
    }
    try:
        _atomic_write_text(
            checkpoint_path,
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        )
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Falha ao salvar checkpoint parcial YOLO: %s", exc)


def _detect_yolo(
    paths: Sequence[Path],
    model_path: str,
    min_conf: float,
    quiet: bool,
    adaptive_primary_count: int = 0,
    checkpoint_path: Optional[Path] = None,
    fast_priority: bool = False,
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

    batch_size = (
        CONFIG.yolo_fast_batch_size if fast_priority
        else CONFIG.yolo_batch_size
    )
    imgsz = CONFIG.yolo_fast_imgsz if fast_priority else CONFIG.yolo_imgsz
    device = rt.device
    half = rt.half
    min_person_conf = CONFIG.yolo_person_min_conf
    min_animal_conf = CONFIG.yolo_animal_min_conf

    logger.info(
        "YOLO: imagens=%d batch=%d imgsz=%d device=%s half=%s "
        "conf_pessoa=%.2f conf_animal=%.2f threads=%d fast_priority=%s",
        len(paths), batch_size, imgsz, device, half,
        min_person_conf, min_animal_conf, CONFIG.threads_per_process,
        fast_priority,
    )

    model_file = Path(model_path).expanduser()
    try:
        model_stat = model_file.resolve().stat()
        model_identity: list[Any] = [
            str(model_file.resolve()), model_stat.st_size,
            model_stat.st_mtime_ns, int(getattr(model_stat, "st_ino", 0)),
        ]
    except OSError:
        model_identity = [str(model_path)]
    checkpoint_identity = {
        "model": model_identity,
        "result_schema": 4,
        "paths": [str(path.resolve()) for path in paths],
        "adaptive_primary_count": max(0, int(adaptive_primary_count)),
        "fast_priority": bool(fast_priority),
        "batch_size": batch_size,
        "min_conf": float(min_conf),
        "imgsz": imgsz,
        "device": device,
        "half": half,
        "person_conf": min_person_conf,
        "animal_conf": min_animal_conf,
        "crop_imgsz": (
            CONFIG.yolo_fast_crop_imgsz if fast_priority
            else CONFIG.yolo_crop_imgsz
        ),
        "crop_person_conf": CONFIG.yolo_crop_person_min_conf,
        "crop_animal_conf": CONFIG.yolo_crop_animal_min_conf,
        "lower_animal_rescue_conf": CONFIG.yolo_lower_rescue_conf,
        "lower_animal_rescue_frames": CONFIG.yolo_lower_rescue_max_frames,
        "lower_animal_rescue_iou": CONFIG.yolo_lower_rescue_min_iou,
        "lower_center_width": CONFIG.yolo_lower_center_width,
        "lower_center_height": CONFIG.yolo_lower_center_height,
        "lower_center_max_frames": CONFIG.yolo_lower_center_max_frames,
        "lower_cat_rescue_conf": CONFIG.yolo_lower_cat_rescue_conf,
        "lower_cat_rescue_min_frames": CONFIG.yolo_lower_cat_rescue_min_frames,
        "lower_cat_rescue_max_frame_gap": CONFIG.yolo_lower_cat_rescue_max_frame_gap,
        "lower_cat_rescue_min_iou": CONFIG.yolo_lower_cat_rescue_min_iou,
        "lower_cat_rescue_max_frames": CONFIG.yolo_lower_cat_rescue_max_frames,
        "lower_cat_rescue_max_mean_luma": CONFIG.yolo_lower_cat_rescue_max_mean_luma,
        "vehicle_min_conf": CONFIG.yolo_vehicle_min_conf,
    }
    cached_records = _load_detection_checkpoint(
        checkpoint_path, paths, checkpoint_identity,
    )
    def _path_key(path: Path) -> str:
        return str(path.resolve())

    def _cached_base(path: Path, stage: str) -> Optional[DetectionRecord]:
        entry = cached_records.get(_path_key(path))
        if entry is None or entry.get("stage") != stage:
            return None
        return entry.get("base")

    def _remember_base(path: Path, rec: Optional[DetectionRecord], stage: str) -> None:
        signature = _frame_input_signature(path)
        if rec is None or signature is None:
            return
        cached_records[_path_key(path)] = {
            "signature": signature, "stage": stage,
            "base": rec, "refined": None,
        }

    def _ordered_records(stage_paths: Sequence[Path], stage: str) -> list[DetectionRecord]:
        result: list[DetectionRecord] = []
        for path in stage_paths:
            record = _cached_base(path, stage)
            if record is not None:
                record = _detection_record_from_dict(record.to_dict())
                if record is None:
                    continue
                record.frame_index = len(result)
                result.append(record)
        return result

    primary_count = min(max(0, int(adaptive_primary_count)), len(paths))
    primary_paths = paths[:primary_count] if primary_count > 0 else paths
    adaptive_paths = paths[primary_count:] if primary_count > 0 else []
    adaptive_stats: dict[str, Any] = {
        "triggered": False,
        "reason": "nao_necessaria",
        "primary_count": len(primary_paths),
        "additional_available": len(adaptive_paths),
        "additional_processed": 0,
        "duration_seconds": 0.0,
        "evidence_before": None,
        "evidence_after": None,
    }

    primary_cached = sum(
        _cached_base(path, "primary") is not None for path in primary_paths
    )
    if primary_cached:
        logger.info(
            "YOLO checkpoint parcial retomado primários=%d/%d",
            primary_cached, len(primary_paths),
        )
    for start in range(0, len(primary_paths), batch_size):
        chunk = [
            path for path in primary_paths[start:start + batch_size]
            if _cached_base(path, "primary") is None
        ]
        if not chunk:
            continue
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

        if results is None:
            results = []
        if len(results) != len(arrays) or any(result is None for result in results):
            # A inferência em lote preserva os resultados que já retornaram;
            # só repete individualmente as posições ausentes/falhas.
            results = list(results[:len(arrays)])
            for index, (arr, p) in enumerate(zip(arrays, kept)):
                if index < len(results) and results[index] is not None:
                    continue
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
                while len(results) <= index:
                    results.append(None)
                results[index] = single_list[0] if single_list else None
        for arr, p, res in zip(arrays, kept, results):
            if res is None:
                continue
            _remember_base(
                p,
                _record_from_yolo(
                    p, arr, res, cv2, min_person_conf, min_animal_conf,
                ),
                "primary",
            )
        _save_detection_checkpoint(
            checkpoint_path, checkpoint_identity, cached_records,
        )

    primary_records = _ordered_records(primary_paths, "primary")
    records = list(primary_records)

    if adaptive_paths and _needs_adaptive_frame_sweep(records):
        adaptive_started = time.monotonic()
        adaptive_records_before = len(records)
        adaptive_evidence_before = _adaptive_evidence_summary(records)
        adaptive_frames_inferred = 0
        adaptive_frames_reused = sum(
            _cached_base(path, "adaptive") is not None
            for path in adaptive_paths
        )
        adaptive_reason = _adaptive_frame_sweep_reason(records)
        adaptive_stats.update({
            "triggered": True,
            "reason": adaptive_reason,
            "evidence_before": adaptive_evidence_before,
        })
        logger.info(
            "YOLO varredura_adaptativa necessária primários=%d adicionais=%d "
            "motivo=%s",
            len(primary_paths), len(adaptive_paths),
            adaptive_reason,
        )
        for start in range(0, len(adaptive_paths), batch_size):
            chunk = [
                path for path in adaptive_paths[start:start + batch_size]
                if _cached_base(path, "adaptive") is None
            ]
            if not chunk:
                continue
            arrays: list[Any] = []
            kept: list[Path] = []
            for path in chunk:
                image = _load_bgr(cv2, path)
                if image is not None:
                    arrays.append(image)
                    kept.append(path)
            if not arrays:
                continue
            adaptive_frames_inferred += len(arrays)
            try:
                with redirect_stdout(sys.stderr):
                    results = list(model(
                        arrays, verbose=False, conf=min_conf, imgsz=imgsz,
                        device=device, half=half,
                    ))
            except Exception as exc:
                logger.warning("Lote adaptativo YOLO falhou: %s", exc)
                results = []
            if len(results) != len(arrays) or any(result is None for result in results):
                results = list(results[:len(arrays)])
                for index, image in enumerate(arrays):
                    if index < len(results) and results[index] is not None:
                        continue
                    try:
                        with redirect_stdout(sys.stderr):
                            single = list(model(
                                image, verbose=False, conf=min_conf,
                                imgsz=imgsz, device=device, half=half,
                            ))
                        while len(results) <= index:
                            results.append(None)
                        results[index] = single[0] if single else None
                    except Exception as exc:
                        logger.warning("Fallback adaptativo YOLO falhou: %s", exc)
                        while len(results) <= index:
                            results.append(None)
                        results[index] = None
            for image, path, result in zip(arrays, kept, results):
                if result is None:
                    continue
                _remember_base(
                    path,
                    _record_from_yolo(
                        path, image, result, cv2,
                        min_person_conf, min_animal_conf,
                    ),
                    "adaptive",
                )
            _save_detection_checkpoint(
                checkpoint_path, checkpoint_identity, cached_records,
            )
        adaptive_records = _ordered_records(adaptive_paths, "adaptive")
        for record in adaptive_records:
            record.frame_index = len(records)
            records.append(record)
        logger.info(
            "YOLO varredura_adaptativa concluída frames=%d "
            "reutilizados=%d registros_novos=%d duração=%.2fs evidência_antes={%s} "
            "evidência_depois={%s}",
            adaptive_frames_inferred, adaptive_frames_reused,
            len(records) - adaptive_records_before,
            time.monotonic() - adaptive_started,
            adaptive_evidence_before,
            _adaptive_evidence_summary(records),
        )
        adaptive_stats.update({
            "additional_processed": len(adaptive_records),
            "additional_reused": adaptive_frames_reused,
            "duration_seconds": round(time.monotonic() - adaptive_started, 3),
            "evidence_after": _adaptive_evidence_summary(records),
        })

    # Objetos pequenos nas duas câmeras empilhadas frequentemente não ocupam
    # pixels suficientes para o modelo nano. Reanalisa metades sem detecção e
    # confirma ambas as metades em fotos e frames terminais em resolução maior.
    # Nesses casos, o passe por metade substitui a classe do quadro composto;
    # assim, um falso positivo de pessoa no composto pode ser corrigido.
    center_candidates = (
        _select_crowded_lower_records(
            records, CONFIG.yolo_lower_center_max_frames,
        )
        + _select_lower_rescue_records(
            records, CONFIG.yolo_lower_center_max_frames,
        )
    )
    center_rescue_paths: set[Path] = set()
    for candidate in center_candidates:
        if len(center_rescue_paths) >= max(
            0, int(CONFIG.yolo_lower_center_max_frames)
        ):
            break
        center_rescue_paths.add(Path(candidate.path).resolve())
    remaining_center_budget = max(
        0, int(CONFIG.yolo_lower_center_max_frames)
        - len(center_rescue_paths)
    )
    for record in records:
        record_path = Path(record.path)
        if (
            remaining_center_budget > 0
            and re.search(r"frame[_-](\d+)", record_path.stem, re.IGNORECASE)
            is None
        ):
            center_rescue_paths.add(record_path.resolve())
            remaining_center_budget -= 1
    lower_animal_rescue_paths = {
        Path(record.path).resolve()
        for record in _select_lower_rescue_records(
            records, CONFIG.yolo_lower_rescue_max_frames,
        )
    }
    best: Optional[DetectionRecord] = None
    if records:
        crop_person_conf = CONFIG.yolo_crop_person_min_conf
        crop_animal_conf = CONFIG.yolo_crop_animal_min_conf
        crop_model_conf = min(crop_person_conf, crop_animal_conf)
        crop_imgsz = (
            CONFIG.yolo_fast_crop_imgsz if fast_priority
            else CONFIG.yolo_crop_imgsz
        )
        frame_numbers: dict[Path, int] = {}
        max_frame_by_parent: dict[Path, int] = {}
        for record in records:
            frame_path = Path(record.path)
            match = re.search(r"frame[_-](\d+)", frame_path.stem, re.IGNORECASE)
            if match is None:
                continue
            number = int(match.group(1))
            parent = frame_path.parent.resolve()
            frame_numbers[frame_path.resolve()] = number
            max_frame_by_parent[parent] = max(
                number, max_frame_by_parent.get(parent, number)
            )
        logger.info(
            "YOLO confirmação por câmera: imagens=%d imgsz=%d "
            "conf_pessoa=%.2f conf_animal=%.2f",
            len(records), crop_imgsz, crop_person_conf, crop_animal_conf,
        )
        for record_index, record in enumerate(records):
            path = Path(record.path)
            entry = cached_records.get(_path_key(path))
            if entry is not None and entry.get("refined") is not None:
                restored = entry["refined"]
                restored.frame_index = record_index
                records[record_index] = restored
                continue
            image = _load_bgr(cv2, path)
            if image is None or image.shape[0] < 2:
                continue
            midpoint = image.shape[0] // 2
            frame_key = path.resolve()
            is_terminal_frame = (
                frame_key in frame_numbers
                and frame_numbers[frame_key]
                == max_frame_by_parent.get(path.parent.resolve())
            )
            if fast_priority and frame_numbers and not is_terminal_frame:
                # O imediato confirma as duas vistas apenas no último quadro
                # do vídeo; não refina todos os frames, como o aprimorado.
                continue
            # Quadros extraídos também são JPG. Apenas fotos independentes e
            # o último quadro exigem confirmação integral das duas câmeras.
            camera_refinement = is_terminal_frame or frame_key not in frame_numbers
            halves: list[tuple[str, Any]] = []
            if camera_refinement or (
                record.upper_person_count + record.upper_animal_count
                + record.upper_vehicle_count == 0
                or frame_key in center_rescue_paths
            ):
                halves.append(("upper", image[:midpoint, :]))
            if camera_refinement or (
                record.lower_person_count + record.lower_animal_count
                + record.lower_vehicle_count == 0
                or frame_key in lower_animal_rescue_paths
                or frame_key in center_rescue_paths
            ):
                halves.append(("lower", image[midpoint:, :]))
            before_tail_counts = (
                record.upper_person_count, record.lower_person_count
            )
            refined_labels: list[str] = []
            refined_person_sharpness = 0.0
            refined_score = 0.0
            if camera_refinement:
                record.upper_person_count = 0
                record.lower_person_count = 0
                record.clear_person_count = 0
                record.upper_clear_person_count = 0
                record.lower_clear_person_count = 0
                record.clear_animal_count = 0
                record.upper_clear_animal_count = 0
                record.lower_clear_animal_count = 0
                record.person_visible_area = 0.0
                record.upper_animal_count = 0
                record.lower_animal_count = 0
                record.vehicle_count = 0
                record.upper_vehicle_count = 0
                record.lower_vehicle_count = 0
                record.detected = ()
            valid_halves = [
                (half_name, crop) for half_name, crop in halves
                if crop is not None and getattr(crop, "size", 0) > 0
            ]
            crop_results: list[Any] = []
            if valid_halves:
                try:
                    # Inferir superior/inferior em lote evita chamadas
                    # serializadas. O imediato confirma a 416 px; o
                    # aprimorado mantém 960 px para objetos pequenos.
                    with redirect_stdout(sys.stderr):
                        raw = model(
                            [crop for _, crop in valid_halves],
                            verbose=False, conf=crop_model_conf,
                            imgsz=crop_imgsz, device=device, half=half,
                        )
                    crop_results = list(raw) if raw is not None else []
                except Exception as exc:
                    logger.warning(
                        "YOLO fallback em lote falhou em %s: %s",
                        path.name, exc,
                    )
            if (
                len(crop_results) != len(valid_halves)
                or any(result is None for result in crop_results)
            ):
                # Fallback compatível: se a versão do Ultralytics ou uma
                # imagem específica não aceitar o lote, tenta cada metade
                # como antes, sem perder essa câmera.
                crop_results = list(crop_results[:len(valid_halves)])
                for index, (half_name, crop) in enumerate(valid_halves):
                    if index < len(crop_results) and crop_results[index] is not None:
                        continue
                    try:
                        with redirect_stdout(sys.stderr):
                            raw = model(
                                crop, verbose=False, conf=crop_model_conf,
                                imgsz=crop_imgsz, device=device, half=half,
                            )
                        result_list = list(raw) if raw is not None else []
                        while len(crop_results) <= index:
                            crop_results.append(None)
                        crop_results[index] = (
                            result_list[0] if result_list else None
                        )
                    except Exception as exc:
                        logger.warning(
                            "YOLO fallback %s falhou em %s: %s",
                            half_name, path.name, exc,
                        )
                        while len(crop_results) <= index:
                            crop_results.append(None)
                        crop_results[index] = None

            # A câmera inferior tende a manter a região de interesse no
            # centro. Além dos frames sem detecção, reservamos recortes para
            # cenas com pelo menos duas pessoas na metade inferior: esse passe
            # pode separar pessoas próximas que o quadro inteiro funde.
            lower_index = next(
                (index for index, (name, _) in enumerate(valid_halves)
                 if name == "lower"),
                None,
            )
            lower_result = (
                crop_results[lower_index]
                if lower_index is not None and lower_index < len(crop_results)
                else None
            )
            if (
                lower_index is not None
                and path.resolve() in center_rescue_paths
            ):
                lower_image = valid_halves[lower_index][1]
                lower_height, lower_width = lower_image.shape[:2]
                center_width = min(1.0, max(0.2,
                    float(CONFIG.yolo_lower_center_width)))
                center_height = min(1.0, max(0.2,
                    float(CONFIG.yolo_lower_center_height)))
                roi_width = max(1, int(lower_width * center_width))
                roi_height = max(1, int(lower_height * center_height))
                x1 = max(0, (lower_width - roi_width) // 2)
                y1 = max(0, (lower_height - roi_height) // 2)
                center_roi = lower_image[
                    y1:y1 + roi_height, x1:x1 + roi_width,
                ]
                try:
                    with redirect_stdout(sys.stderr):
                        center_raw = model(
                            center_roi, verbose=False, conf=crop_model_conf,
                            imgsz=crop_imgsz, device=device, half=half,
                        )
                    center_list = list(center_raw) if center_raw is not None else []
                    center_result = center_list[0] if center_list else None
                except Exception as exc:
                    logger.warning(
                        "YOLO confirmação central inferior falhou em %s: %s",
                        path.name, exc,
                    )
                    center_result = None
                if center_result is not None:
                    valid_halves.append(("lower_center", center_roi))
                    crop_results.append(center_result)
                    logger.info(
                        "YOLO confirmação região_central_inferior frame=%s "
                        "roi=%dx%d imgsz=%d",
                        path.name, roi_width, roi_height, crop_imgsz,
                    )

            for (half_name, crop), crop_result in zip(
                valid_halves, crop_results
            ):
                if crop_result is None:
                    continue
                crop_record = _record_from_yolo(
                    path, crop, crop_result, cv2, crop_person_conf,
                    crop_animal_conf, stacked=False,
                )
                if crop_record is None:
                    continue
                if (
                    not camera_refinement
                    and crop_record.person_count <= 0
                    and crop_record.animal_count <= 0
                    and crop_record.vehicle_count <= 0
                ):
                    continue
                old_total_people = record.person_count
                if half_name == "upper":
                    old_half_people = record.upper_person_count
                    record.upper_person_count = (
                        crop_record.person_count if camera_refinement else
                        max(old_half_people, crop_record.person_count)
                    )
                    record.upper_clear_person_count = (
                        crop_record.clear_person_count if camera_refinement else
                        max(record.upper_clear_person_count, crop_record.clear_person_count)
                    )
                    record.upper_clear_animal_count = (
                        crop_record.clear_animal_count if camera_refinement else
                        max(record.upper_clear_animal_count, crop_record.clear_animal_count)
                    )
                    record.upper_animal_count = (
                        crop_record.animal_count if camera_refinement else
                        max(record.upper_animal_count, crop_record.animal_count)
                    )
                else:
                    old_half_people = record.lower_person_count
                    record.lower_person_count = (
                        crop_record.person_count if camera_refinement else
                        max(old_half_people, crop_record.person_count)
                    )
                    record.lower_clear_person_count = (
                        crop_record.clear_person_count if camera_refinement else
                        max(record.lower_clear_person_count, crop_record.clear_person_count)
                    )
                    record.lower_clear_animal_count = (
                        crop_record.clear_animal_count if camera_refinement else
                        max(record.lower_clear_animal_count, crop_record.clear_animal_count)
                    )
                    record.lower_animal_count = (
                        crop_record.animal_count if camera_refinement else
                        max(record.lower_animal_count, crop_record.animal_count)
                    )
                if half_name == "upper":
                    record.upper_vehicle_count = (
                        crop_record.vehicle_count if camera_refinement else
                        max(record.upper_vehicle_count, crop_record.vehicle_count)
                    )
                else:
                    record.lower_vehicle_count = (
                        crop_record.vehicle_count if camera_refinement else
                        max(record.lower_vehicle_count, crop_record.vehicle_count)
                    )
                record.vehicle_count = max(
                    record.upper_vehicle_count, record.lower_vehicle_count,
                )
                record.clear_person_count = max(record.upper_clear_person_count,
                                                record.lower_clear_person_count)
                record.clear_animal_count = max(record.upper_clear_animal_count,
                                                record.lower_clear_animal_count)
                if camera_refinement:
                    record.person_visible_area += crop_record.person_visible_area
                else:
                    record.person_visible_area = max(record.person_visible_area,
                                                     crop_record.person_visible_area)
                new_half_people = (
                    record.upper_person_count
                    if half_name == "upper" else record.lower_person_count
                )
                added_people = max(0, new_half_people - old_half_people)
                record.person_count = max(
                    record.upper_person_count, record.lower_person_count
                )
                record.animal_count = max(
                    record.upper_animal_count, record.lower_animal_count
                )
                if added_people > 0:
                    record.person_sharpness = (
                        record.person_sharpness * old_total_people
                        + crop_record.person_sharpness * added_people
                    ) / max(1, old_total_people + added_people)
                elif crop_record.person_count > 0:
                    record.person_sharpness = max(
                        record.person_sharpness, crop_record.person_sharpness
                    )
                if record.person_count > 1:
                    record.object_count = record.person_count
                    record.category = "multiple_people"
                    record.level = LEVEL_MULTIPLE_PEOPLE
                elif record.person_count == 1:
                    record.object_count = 1
                    record.category = "person"
                    record.level = LEVEL_PERSON
                elif record.animal_count > 0:
                    record.object_count = record.animal_count
                    record.category = "animal"
                    record.level = LEVEL_ANIMAL
                elif record.vehicle_count > 0:
                    record.object_count = record.vehicle_count
                    record.category = "vehicle"
                    record.level = LEVEL_SHARPNESS
                else:
                    record.object_count = 0
                    record.category = "sharpness"
                    record.level = LEVEL_SHARPNESS
                record.score = max(record.score, crop_record.score)
                record.sharpness = max(record.sharpness, crop_record.sharpness)
                record.detected = tuple(
                    item for item in (*record.detected, *crop_record.detected)
                    if item != "sharpness"
                ) or (
                    ("person",) if record.person_count else
                    ("vehicle",) if record.vehicle_count else
                    ("animal",) if record.animal_count else ()
                )
                refined_labels.extend(crop_record.detected)
                refined_person_sharpness = max(
                    refined_person_sharpness, crop_record.person_sharpness
                )
                refined_score = max(refined_score, crop_record.score)
                logger.info(
                    "YOLO confirmação detectou pessoas=%d animais=%d em %s "
                    "camera=%s verificacao_final=%s",
                    crop_record.person_count, crop_record.animal_count,
                    path.name, half_name, is_terminal_frame,
                )
            if camera_refinement:
                record.person_count = max(
                    record.upper_person_count, record.lower_person_count
                )
                record.animal_count = max(
                    record.upper_animal_count, record.lower_animal_count
                )
                record.person_sharpness = refined_person_sharpness
                record.score = refined_score or record.frame_sharpness
                if record.person_count > 1:
                    record.category = "multiple_people"
                    record.object_count = record.person_count
                    record.level = LEVEL_MULTIPLE_PEOPLE
                elif record.person_count == 1:
                    record.category = "person"
                    record.object_count = 1
                    record.level = LEVEL_PERSON
                elif record.animal_count > 0:
                    record.category = "animal"
                    record.object_count = record.animal_count
                    record.level = LEVEL_ANIMAL
                elif record.vehicle_count > 0:
                    record.category = "vehicle"
                    record.object_count = record.vehicle_count
                    record.level = LEVEL_SHARPNESS
                elif record.vehicle_count > 0:
                    record.category = "vehicle"
                    record.object_count = record.vehicle_count
                    record.level = LEVEL_SHARPNESS
                else:
                    record.category = "sharpness"
                    record.object_count = 0
                    record.level = LEVEL_SHARPNESS
                record.detected = tuple(dict.fromkeys(
                    label for label in refined_labels if label != "sharpness"
                )) or (("sharpness",) if record.level == LEVEL_SHARPNESS else ())
            if is_terminal_frame:
                logger.info(
                    "YOLO frame_final_refinado frame=%s pessoas_por_camera=%d/%d "
                    "antes=%d/%d",
                    path.name, record.upper_person_count,
                    record.lower_person_count, *before_tail_counts,
                )

            entry = cached_records.get(_path_key(path))
            if entry is not None:
                entry["refined"] = record
                _save_detection_checkpoint(
                    checkpoint_path, checkpoint_identity, cached_records,
                )

    # Resgate de baixa confiança, limitado à câmera inferior e só quando o
    # passe normal não confirmou animal nela. A confirmação exige a mesma
    # classe/caixa em pelo menos dois frames próximos, evitando contar ruído.
    recurring_vehicle_paths = {
        Path(record.path).resolve()
        for record in records if record.vehicle_count > 0
    }
    vehicle_event = len(recurring_vehicle_paths) >= 2
    if vehicle_event and not any(
        record.upper_animal_count + record.lower_animal_count > 0
        for record in records
    ):
        logger.info(
            "YOLO resgate_inferior mantido motivo=veiculo_nao_exclui_animais "
            "frames_veiculo=%d",
            len(recurring_vehicle_paths),
        )
    # A moving camera can track an animal while also seeing a parked vehicle.
    # Recurrent vehicle boxes therefore cannot prove that the lower view has
    # no animals; keep the bounded low-confidence rescue whenever the normal
    # passes found none, including vehicle events.
    if not fast_priority and records:
        rescue_records = _select_lower_rescue_records(
            records, CONFIG.yolo_lower_rescue_max_frames,
        )
        rescue_started = time.monotonic()
        rescue_observations: list[
            tuple[Path, int, int, float, tuple[float, float, float, float]]
        ] = []
        for start in range(0, len(rescue_records), batch_size):
            record_chunk = rescue_records[start:start + batch_size]
            crops: list[Any] = []
            chunk_paths: list[Path] = []
            for record in record_chunk:
                image = _load_bgr(cv2, Path(record.path))
                if image is None or image.shape[0] < 2:
                    continue
                crops.append(image[image.shape[0] // 2:, :])
                chunk_paths.append(Path(record.path))
            if not crops:
                continue
            try:
                with redirect_stdout(sys.stderr):
                    raw_results = list(model(
                        crops,
                        verbose=False,
                        conf=CONFIG.yolo_lower_rescue_conf,
                        imgsz=crop_imgsz,
                        device=device,
                        half=half,
                        classes=sorted(ANIMAL_IDS),
                    ))
            except Exception as exc:
                logger.warning(
                    "YOLO resgate inferior falhou no lote: %s", exc,
                )
                raw_results = []
            if (
                len(raw_results) != len(crops)
                or any(result is None for result in raw_results)
            ):
                raw_results = list(raw_results[:len(crops)])
                for index, crop in enumerate(crops):
                    if index < len(raw_results) and raw_results[index] is not None:
                        continue
                    try:
                        with redirect_stdout(sys.stderr):
                            single = list(model(
                                crop,
                                verbose=False,
                                conf=CONFIG.yolo_lower_rescue_conf,
                                imgsz=crop_imgsz,
                                device=device,
                                half=half,
                                classes=sorted(ANIMAL_IDS),
                            ))
                        while len(raw_results) <= index:
                            raw_results.append(None)
                        raw_results[index] = single[0] if single else None
                    except Exception as exc:
                        logger.warning(
                            "YOLO resgate inferior individual falhou: %s", exc,
                        )
                        while len(raw_results) <= index:
                            raw_results.append(None)
                        raw_results[index] = None
            for path, result in zip(chunk_paths, raw_results):
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    continue
                match = re.search(
                    r"frame[_-](\d+)", path.stem, re.IGNORECASE,
                )
                if match is None:
                    continue
                frame_id = int(match.group(1))
                for box in boxes:
                    try:
                        class_id = int(float(box.cls[0]))
                        confidence = float(box.conf[0])
                        coords = tuple(float(value) for value in box.xyxy[0])
                    except (AttributeError, IndexError, TypeError, ValueError):
                        continue
                    if (
                        class_id not in ANIMAL_IDS
                        or confidence < CONFIG.yolo_lower_rescue_conf
                        or confidence >= crop_animal_conf
                        or len(coords) < 4
                    ):
                        continue
                    rescue_observations.append((
                        path, frame_id, class_id, confidence,
                        (coords[0], coords[1], coords[2], coords[3]),
                    ))

        confirmed_tracks = _confirmed_lower_animal_tracks(
            rescue_observations,
            min_frames=CONFIG.yolo_lower_rescue_min_frames,
            max_frame_gap=CONFIG.yolo_lower_rescue_max_frame_gap,
            min_iou=CONFIG.yolo_lower_rescue_min_iou,
        )
        rescued_frames = 0
        rescued_tracks = 0
        for record in rescue_records:
            path = Path(record.path).resolve()
            evidence = confirmed_tracks.get(path)
            if evidence is None:
                continue
            animal_count, confidence, labels = evidence
            if animal_count <= 0:
                continue
            record.lower_animal_count = max(
                record.lower_animal_count, animal_count,
            )
            record.animal_count = max(
                record.upper_animal_count, record.lower_animal_count,
            )
            record.detected = tuple(dict.fromkeys((
                *(label for label in record.detected if label != "sharpness"),
                *labels,
            )))
            record.score = max(
                record.score, record.frame_sharpness * confidence,
            )
            if record.person_count > 1:
                record.category = "multiple_people"
                record.object_count = record.person_count
                record.level = LEVEL_MULTIPLE_PEOPLE
            elif record.person_count == 1:
                record.category = "person"
                record.object_count = 1
                record.level = LEVEL_PERSON
            else:
                record.category = "animal"
                record.object_count = record.animal_count
                record.level = LEVEL_ANIMAL
            rescued_frames += 1
            rescued_tracks = max(rescued_tracks, animal_count)
            entry = cached_records.get(_path_key(Path(record.path)))
            if entry is not None:
                entry["refined"] = record

        if rescued_frames:
            _save_detection_checkpoint(
                checkpoint_path, checkpoint_identity, cached_records,
            )
        logger.info(
            "YOLO resgate_inferior frames=%d/%d propostas=%d "
            "frames_confirmados=%d animais_max=%d conf=%.2f "
            "iou=%.2f duração=%.2fs",
            len(rescue_records), len(records), len(rescue_observations),
            rescued_frames, rescued_tracks, CONFIG.yolo_lower_rescue_conf,
            CONFIG.yolo_lower_rescue_min_iou,
            time.monotonic() - rescue_started,
        )

    # YOLOv8n sometimes marks a night cat only at <1% confidence. Keep the
    # normal thresholds for every other class; this pass looks for cats only,
    # on the full lower camera view, then requires a stable multi-frame track.
    has_lower_cat = any(
        record.lower_animal_count > 0 and "cat" in record.detected
        for record in records
    )
    if not fast_priority and records and not has_lower_cat:
        cat_records = _select_lower_cat_rescue_records(
            records, CONFIG.yolo_lower_cat_rescue_max_frames,
        )
        cat_started = time.monotonic()
        cat_observations: list[
            tuple[Path, int, int, float, tuple[float, float, float, float]]
        ] = []
        cat_eligible_paths: set[Path] = set()
        cat_bright_frames_skipped = 0
        for start in range(0, len(cat_records), batch_size):
            record_chunk = cat_records[start:start + batch_size]
            crops: list[Any] = []
            chunk_paths: list[Path] = []
            for record in record_chunk:
                image = _load_bgr(cv2, Path(record.path))
                if image is None or image.shape[0] < 2:
                    continue
                lower_crop = image[image.shape[0] // 2:, :]
                try:
                    gray_lower = cv2.cvtColor(lower_crop, cv2.COLOR_BGR2GRAY)
                    mean_luma = float(cv2.mean(gray_lower)[0])
                except Exception:
                    # Se não for possível medir a luz, não aplicar o passe
                    # ultrabaixo que é específico para cenas noturnas.
                    cat_bright_frames_skipped += 1
                    continue
                if not _is_low_light_for_cat_rescue(
                    mean_luma, CONFIG.yolo_lower_cat_rescue_max_mean_luma,
                ):
                    cat_bright_frames_skipped += 1
                    continue
                crops.append(lower_crop)
                chunk_paths.append(Path(record.path))
                cat_eligible_paths.add(Path(record.path).resolve())
            if not crops:
                continue
            try:
                with redirect_stdout(sys.stderr):
                    raw_results = list(model(
                        crops,
                        verbose=False,
                        conf=CONFIG.yolo_lower_cat_rescue_conf,
                        imgsz=crop_imgsz,
                        device=device,
                        half=half,
                        classes=[CAT_CLASS_ID],
                    ))
            except Exception as exc:
                logger.warning("YOLO resgate gato inferior falhou: %s", exc)
                raw_results = []
            raw_results = list(raw_results[:len(crops)])
            for index, crop in enumerate(crops):
                if index < len(raw_results) and raw_results[index] is not None:
                    continue
                try:
                    with redirect_stdout(sys.stderr):
                        single = list(model(
                            crop,
                            verbose=False,
                            conf=CONFIG.yolo_lower_cat_rescue_conf,
                            imgsz=crop_imgsz,
                            device=device,
                            half=half,
                            classes=[CAT_CLASS_ID],
                        ))
                    while len(raw_results) <= index:
                        raw_results.append(None)
                    raw_results[index] = single[0] if single else None
                except Exception as exc:
                    logger.warning(
                        "YOLO resgate gato individual falhou: %s", exc,
                    )
                    while len(raw_results) <= index:
                        raw_results.append(None)
                    raw_results[index] = None

            for path, result in zip(chunk_paths, raw_results):
                boxes = getattr(result, "boxes", None)
                match = re.search(
                    r"frame[_-](\d+)", path.stem, re.IGNORECASE,
                )
                if boxes is None or match is None:
                    continue
                frame_id = int(match.group(1))
                for box in boxes:
                    try:
                        class_id = int(float(box.cls[0]))
                        confidence = float(box.conf[0])
                        coords = tuple(float(value) for value in box.xyxy[0])
                    except (AttributeError, IndexError, TypeError, ValueError):
                        continue
                    if (
                        class_id != CAT_CLASS_ID
                        or confidence < CONFIG.yolo_lower_cat_rescue_conf
                        or len(coords) < 4
                    ):
                        continue
                    cat_observations.append((
                        path, frame_id, class_id, confidence,
                        (coords[0], coords[1], coords[2], coords[3]),
                    ))

        confirmed_cat_tracks = _confirmed_lower_animal_tracks(
            cat_observations,
            min_frames=CONFIG.yolo_lower_cat_rescue_min_frames,
            max_frame_gap=CONFIG.yolo_lower_cat_rescue_max_frame_gap,
            min_iou=CONFIG.yolo_lower_cat_rescue_min_iou,
        )
        cat_rescued_frames = 0
        cat_rescued_tracks = 0
        for record in cat_records:
            path = Path(record.path).resolve()
            evidence = confirmed_cat_tracks.get(path)
            if evidence is None:
                continue
            animal_count, confidence, labels = evidence
            if animal_count <= 0:
                continue
            record.lower_animal_count = max(
                record.lower_animal_count, animal_count,
            )
            record.cat_rescue = True
            record.animal_count = max(
                record.upper_animal_count, record.lower_animal_count,
            )
            record.detected = tuple(dict.fromkeys((
                *(label for label in record.detected if label != "sharpness"),
                *labels,
            )))
            record.score = max(
                record.score, record.frame_sharpness * confidence,
            )
            if record.person_count > 1:
                record.category = "multiple_people"
                record.object_count = record.person_count
                record.level = LEVEL_MULTIPLE_PEOPLE
            elif record.person_count == 1:
                record.category = "person"
                record.object_count = 1
                record.level = LEVEL_PERSON
            else:
                record.category = "animal"
                record.object_count = record.animal_count
                record.level = LEVEL_ANIMAL
            cat_rescued_frames += 1
            cat_rescued_tracks = max(cat_rescued_tracks, animal_count)
            entry = cached_records.get(_path_key(Path(record.path)))
            if entry is not None:
                entry["refined"] = record
        if cat_rescued_frames:
            _save_detection_checkpoint(
                checkpoint_path, checkpoint_identity, cached_records,
            )
        logger.info(
            "YOLO resgate_gato_inferior frames=%d/%d propostas=%d "
            "amostrados=%d claros_pulados=%d frames_confirmados=%d "
            "animais_max=%d conf_min=%.3f "
            "min_frames=%d gap=%d iou=%.2f duração=%.2fs",
            len(cat_eligible_paths), len(records), len(cat_observations),
            len(cat_records), cat_bright_frames_skipped,
            cat_rescued_frames, cat_rescued_tracks,
            CONFIG.yolo_lower_cat_rescue_conf,
            CONFIG.yolo_lower_cat_rescue_min_frames,
            CONFIG.yolo_lower_cat_rescue_max_frame_gap,
            CONFIG.yolo_lower_cat_rescue_min_iou,
            time.monotonic() - cat_started,
        )

    if records:
        best = max(records, key=lambda record: record.ranking_key)

    if best is None:
        logger.info("YOLO não produziu candidatos válidos.")
        return None, -1.0, None, records

    result = SelectionResult(
        category=best.category,
        object_count=best.object_count,
        detections=best.detected,
        adaptive_sweep=adaptive_stats,
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
        if result is not None and result.adaptive_sweep is not None:
            payload["analysis"] = {
                "adaptive_sweep": result.adaptive_sweep,
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
    parser.add_argument(
        "--adaptive-primary-count", type=int, default=0,
        help="Avalia candidatos primários primeiro e só usa os finais como varredura adaptativa.",
    )
    parser.add_argument(
        "--checkpoint-file", default=None,
        help="Checkpoint atômico de resultados YOLO por imagem para retomada após preempção.",
    )
    parser.add_argument(
        "--fast-priority", action="store_true",
        help="Usa inferência-base leve e adia confirmações por recorte para alertas urgentes.",
    )
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
                adaptive_primary_count=args.adaptive_primary_count,
                fast_priority=args.fast_priority,
                checkpoint_path=(
                    Path(args.checkpoint_file).expanduser().resolve()
                    if args.checkpoint_file else None
                ),
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
