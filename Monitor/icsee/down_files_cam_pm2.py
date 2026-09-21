#!/usr/bin/env python3
"""
down_files_cam_pm2.py — Serviço contínuo de download de câmera ICSee (PM2).

Correções aplicadas:
- Nome de arquivo agora preserva begin-END para permitir dedup de incompletos.
- Eventos com início até 5s após o fim anterior são tratados como a mesma janela.
- Verificação de disco a cada 15 minutos (antes: 6h).
- Dedup de vídeos incompletos a cada 1h com lookback de 7 dias.
- Limpeza de .part órfãos (>1h) junto com verificação de disco.
- Scanning otimizado com os.walk em vez de Path.rglob.
- Graceful shutdown via threading.Event (responde a SIGTERM em segundos).
- WhatsApp loga stderr/stdout do envio para diagnóstico.

Melhorias de resiliência:
- DatabaseManager com auto-recuperação, retry e modo degradado.
- Thread watchdog para monitorar saúde do banco a cada 5 minutos.
- Tratamento robusto de exceções com recuperação automática.
- Alertas WhatsApp para corrupção/recuperação do banco.
"""
import os
import re
import sys
import time
import socket
import sqlite3
import pwd
import grp
import shutil
import subprocess
import logging
import signal
import struct
import threading
from datetime import datetime, timedelta
from pathlib import Path
from multiprocessing import Process, Queue
from queue import Empty
from typing import Optional, Dict, List, Tuple, Any

from dvrip import DVRIPCam

# ============================================================
# PATCH DA LIB DVRIP (streaming download para disco)
# ============================================================
def _patch_dvrip_streaming_download() -> None:
    """Substitui DVRIPCam.get_file/download_file por versões que escrevem os
    dados no arquivo de destino em disco à medida que chegam da câmera, em vez
    de acumular o download inteiro num bytearray em RAM."""

    def _get_file_streaming(self: DVRIPCam, first_chunk_size: int):
        out = getattr(self, "_stream_handle", None)
        if out is None:
            buf = bytearray()
            data = self.receive_with_timeout(first_chunk_size)
            if data is None:
                raise TypeError("Conexão perdida no início do download")
            buf.extend(data)
            while True:
                header = self.receive_with_timeout(20)
                if header is None:
                    raise TypeError("Conexão perdida ao ler cabeçalho")
                len_data = struct.unpack("I", header[16:])[0]
                if len_data == 0:
                    return buf
                data = self.receive_with_timeout(len_data)
                if data is None:
                    raise TypeError("Conexão perdida no meio do download")
                buf.extend(data)
        else:
            data = self.receive_with_timeout(first_chunk_size)
            if data is None:
                raise TypeError("Conexão perdida no início do download")
            out.write(data)
            out.flush()
            total = len(data)
            while True:
                header = self.receive_with_timeout(20)
                if header is None:
                    raise TypeError("Conexão perdida ao ler cabeçalho")
                len_data = struct.unpack("I", header[16:])[0]
                if len_data == 0:
                    return total
                data = self.receive_with_timeout(len_data)
                if data is None:
                    raise TypeError("Conexão perdida no meio do download")
                out.write(data)
                out.flush()
                total += len(data)

    def _download_file_streaming(
        self: DVRIPCam,
        startTime: str,
        endTime: str,
        filename: str,
        targetFilePath: str,
        download: bool = True,
    ):
        Path(targetFilePath).parent.mkdir(parents=True, exist_ok=True)
        self.logger.debug(f"Downloading (streaming): {targetFilePath}")
        self.send(
            1424,
            {
                "Name": "OPPlayBack",
                "OPPlayBack": {
                    "Action": "Claim",
                    "Parameter": {
                        "PlayMode": "ByName",
                        "FileName": filename,
                        "StreamType": 0,
                        "Value": 0,
                        "TransMode": "TCP",
                    },
                    "StartTime": startTime,
                    "EndTime": endTime,
                },
            },
        )
        actionStart = "Start"
        if download:
            actionStart = f"Download{actionStart}"
        try:
            with open(targetFilePath, "wb") as out:
                self._stream_handle = out
                try:
                    result = self.send_custom(
                        1420,
                        {
                            "Name": "OPPlayBack",
                            "OPPlayBack": {
                                "Action": actionStart,
                                "Parameter": {
                                    "PlayMode": "ByName",
                                    "FileName": filename,
                                    "StreamType": 0,
                                    "Value": 0,
                                    "TransMode": "TCP",
                                },
                                "StartTime": startTime,
                                "EndTime": endTime,
                            },
                        },
                        download=True,
                    )
                finally:
                    self._stream_handle = None
            if result is None:
                raise TypeError("Nenhuma resposta da câmera ao iniciar o download")
        except TypeError:
            Path(targetFilePath).unlink(missing_ok=True)
            self.logger.debug(f"An error occured while downloading {targetFilePath}")
            raise
        self.logger.debug(f"File successfully downloaded: {targetFilePath}")
        actionStop = "Stop"
        if download:
            actionStop = f"Download{actionStop}"
        self.send(
            1420,
            {
                "Name": "OPPlayBack",
                "OPPlayBack": {
                    "Action": actionStop,
                    "Parameter": {
                        "FileName": filename,
                        "PlayMode": "ByName",
                        "StreamType": 0,
                        "Channel": 0,
                        "Value": 0,
                        "TransMode": "TCP",
                    },
                    "StartTime": startTime,
                    "EndTime": endTime,
                },
            },
        )
        return None

    DVRIPCam.get_file = _get_file_streaming
    DVRIPCam.download_file = _download_file_streaming

_patch_dvrip_streaming_download()


# ==================== CONFIGURAÇÕES ====================
HOST: str = os.getenv("DVR_HOST", "0.0.0.0")
USER: str = os.getenv("DVR_USER", "0000")
PASSWORD: str = os.getenv("DVR_PASSWORD", "00000")
PORT: int = int(os.getenv("DVR_PORT", "34567"))
DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "gravacoes")
TIMEOUT_CONNECT: int = int(os.getenv("TIMEOUT_CONNECT", "2"))
FIRST_RUN_DAYS_BACK: int = int(os.getenv("FIRST_RUN_DAYS_BACK", "7"))
OWNER_USER: str = os.getenv("OWNER_USER", "webmaster")
OWNER_GROUP: str = os.getenv("OWNER_GROUP", "cam-app")
DIR_MODE: int = int(os.getenv("DIR_MODE", "0o2770"), 8)
FILE_MODE: int = int(os.getenv("FILE_MODE", "0o660"), 8)
DB_PATH: str = os.path.join(DOWNLOAD_DIR, ".download_cache.db")
TMP_DIR: str = os.path.join(DOWNLOAD_DIR, ".tmp")
ALERT_SCRIPT: str = os.getenv("ALERT_SCRIPT", "alert_motion.py")
STALL_TIMEOUT: int = int(os.getenv("STALL_TIMEOUT", "30"))
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "5"))
MAX_DOWNLOAD_TIME: int = int(os.getenv("MAX_DOWNLOAD_TIME", "3600"))
DOWNLOAD_TIMEOUT_PER_FILE: int = int(os.getenv("DOWNLOAD_TIMEOUT_PER_FILE", "30"))
LOGIN_MAX_RETRIES: int = int(os.getenv("LOGIN_MAX_RETRIES", "3"))
LOGIN_BACKOFF_SECONDS: int = int(os.getenv("LOGIN_BACKOFF_SECONDS", "2"))
CAMERA_IO_TIMEOUT: int = int(os.getenv("CAMERA_IO_TIMEOUT", "20"))
MAX_QUERY_CHUNK_HOURS: int = int(os.getenv("MAX_QUERY_CHUNK_HOURS", "1"))
WINDOW_TOLERANCE_SECONDS: int = int(
    os.getenv("WINDOW_TOLERANCE_SECONDS", "5")
)
SANITY_CHECK_INTERVAL_HOURS: int = int(os.getenv("SANITY_CHECK_INTERVAL_HOURS", "24"))
CB_MAX_FAILURES: int = int(os.getenv("CB_MAX_FAILURES", "3"))
CB_BACKOFF_LEVELS: List[int] = [60, 300, 900, 3600, 21600, 86400]
WHATSAPP_NUMBERS: List[str] = [
    n.strip()
    for n in os.getenv("WHATSAPP_NOTIFY_NUMBERS", "0000000000000").split(",")
    if n.strip()
]

# ==================== CONFIGURAÇÕES PM2 ====================
SLEEP_ONLINE: int = int(os.getenv("SLEEP_ONLINE", "10"))
SLEEP_IDLE: int = int(os.getenv("SLEEP_IDLE", "30"))
SLEEP_OFFLINE: int = int(os.getenv("SLEEP_OFFLINE", "60"))
SLEEP_ERROR: int = int(os.getenv("SLEEP_ERROR", "120"))
INIT_MAX_RETRIES: int = int(os.getenv("INIT_MAX_RETRIES", "5"))
INIT_RETRY_DELAY: int = int(os.getenv("INIT_RETRY_DELAY", "10"))

# ==================== CONFIGURAÇÕES DE DISCO (15 min) ====================
DISK_CHECK_INTERVAL_MINUTES: int = int(os.getenv("DISK_CHECK_INTERVAL_MINUTES", "15"))
MAX_DISK_MB: int = int(os.getenv("MAX_DISK_MB", "4096"))
DISK_CLEANUP_PERCENT: int = int(os.getenv("DISK_CLEANUP_PERCENT", "10"))
STALE_PART_MAX_AGE_SECONDS: int = int(os.getenv("STALE_PART_MAX_AGE_SECONDS", "3600"))
MAX_DISK_BYTES: int = MAX_DISK_MB * 1024 * 1024
DISK_CLEANUP_BYTES: int = int(MAX_DISK_BYTES * DISK_CLEANUP_PERCENT / 100)

# ==================== CONFIGURAÇÕES DE DEDUP (1h) ====================
DEDUP_CHECK_INTERVAL_MINUTES: int = int(os.getenv("DEDUP_CHECK_INTERVAL_MINUTES", "60"))
DEDUP_MIN_FILE_AGE_SECONDS: int = int(os.getenv("DEDUP_MIN_FILE_AGE_SECONDS", "600"))
DEDUP_LOOKBACK_DAYS: int = int(os.getenv("DEDUP_LOOKBACK_DAYS", "7"))

JPEG_SOI: bytes = b"\xff\xd8\xff"
H264_NAL_LONG: bytes = b"\x00\x00\x00\x01"
H264_NAL_SHORT: bytes = b"\x00\x00\x01"
H264_MIN_SIZE: int = 64
H264_HEADER_SIZE: int = 8192

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log: logging.Logger = logging.getLogger(__name__)

# ==================== ESTADO GLOBAL ====================
_stop_event: threading.Event = threading.Event()
_cam: Optional[DVRIPCam] = None
_active_proc: Optional[Process] = None

# ==================== GERENCIADOR DE BANCO DE DADOS (RESILIENTE) ====================
DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT UNIQUE NOT NULL,
    file_size INTEGER,
    download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS camera_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS file_failures (
    rel_path TEXT PRIMARY KEY,
    failure_count INTEGER DEFAULT 0,
    last_failure_at TEXT,
    quarantined_until TEXT,
    permanent INTEGER DEFAULT 0,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
INSERT OR IGNORE INTO camera_status (id, status) VALUES (1, 0);
"""

class DatabaseManager:
    """Gerencia a conexão SQLite com auto-recuperação e modo degradado."""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self.degraded = False
        self._recreate_attempts = 0
        self._last_recreate_error: Optional[str] = None

    def _connect(self) -> sqlite3.Connection:
        """Tenta abrir a conexão, recriando o banco se necessário."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            # Testa integridade
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            if cursor.fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Integrity check failed")
            # Verifica se as tabelas existem
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='downloads'")
            if not cursor.fetchone():
                raise sqlite3.DatabaseError("Tabela 'downloads' não encontrada")
            # Se chegou aqui, está ok
            if self.degraded:
                log.info("💾 Banco recuperado, saindo do modo degradado.")
                self.degraded = False
                self._recreate_attempts = 0
                self._last_recreate_error = None
            return conn
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            log.warning(f"Erro ao abrir banco: {e}. Tentando recriar...")
            self._recreate_db()
            return self._connect()

    def _recreate_db(self) -> None:
        """Recria o banco a partir do esquema, com controle de tentativas."""
        with self._lock:
            self._recreate_attempts += 1
            log.warning(f"🔄 Recriando banco (tentativa {self._recreate_attempts})...")
            # Fecha conexão existente
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            # Remove arquivo antigo se existir
            if os.path.exists(self.db_path):
                try:
                    os.remove(self.db_path)
                    log.info(f"Arquivo antigo removido: {self.db_path}")
                except OSError as e:
                    log.error(f"Não foi possível remover {self.db_path}: {e}")
                    self._last_recreate_error = str(e)
                    self._enter_degraded_mode()
                    return
            # Cria diretório pai se necessário
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            try:
                conn = sqlite3.connect(self.db_path)
                conn.executescript(DB_SCHEMA)
                conn.commit()
                conn.close()
                fix_perms(self.db_path)
                log.info("✅ Banco recriado com sucesso.")
                self._recreate_attempts = 0
                self._last_recreate_error = None
                if self.degraded:
                    # notifica recuperação via WhatsApp
                    try:
                        WhatsAppNotifier().send_connectivity("db_online", "db_offline", dur=0)
                    except Exception:
                        pass
                    self.degraded = False
            except Exception as e:
                log.error(f"Falha ao recriar banco: {e}")
                self._last_recreate_error = str(e)
                self._enter_degraded_mode()

    def _enter_degraded_mode(self) -> None:
        """Entra em modo degradado (operações com banco desabilitadas)."""
        if not self.degraded:
            self.degraded = True
            log.critical("⚠️ Banco em MODO DEGRADADO — operações de cache desabilitadas.")
            # Alerta WhatsApp
            try:
                msg = (
                    "⚠️ *ALERTA: Banco de dados corrompido!*\n"
                    "O sistema entrou em modo degradado.\n"
                    "Downloads continuam, mas cache de arquivos baixados está DESATIVADO.\n"
                    "Verifique o disco e permissões."
                )
                WhatsAppNotifier()._send(msg)
            except Exception:
                pass

    def get_connection(self) -> sqlite3.Connection:
        """Retorna a conexão ativa, reconectando se necessário."""
        with self._lock:
            if self.degraded:
                raise sqlite3.DatabaseError("Banco em modo degradado")
            if not os.path.isdir(os.path.dirname(self.db_path)):
                os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            if self._conn is not None and not os.path.exists(self.db_path):
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
                self._recreate_db()
            if self._conn is None:
                self._conn = self._connect()
            # Testa se a conexão está viva
            try:
                self._conn.cursor().execute("SELECT 1").fetchone()
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                self._conn = None
                self._conn = self._connect()
            return self._conn

    def execute(self, query: str, params=(), retries: int = 3) -> Any:
        """Executa uma query com retry automático e tratamento de falhas."""
        if self.degraded:
            # Se em modo degradado, apenas loga e retorna None ou vazio
            log.warning(f"Modo degradado: query ignorada: {query[:50]}...")
            return None

        for attempt in range(retries):
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                return cursor
            except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                if attempt == retries - 1:
                    log.error(f"Falha na query após {retries} tentativas: {e}")
                    self._recreate_db()
                    if self.degraded:
                        return None
                    # Tenta novamente após recriação
                    try:
                        conn = self.get_connection()
                        cursor = conn.cursor()
                        cursor.execute(query, params)
                        conn.commit()
                        return cursor
                    except Exception as final_e:
                        log.error(f"Falha final na query: {final_e}")
                        self._enter_degraded_mode()
                        return None
                time.sleep(0.5 * (attempt + 1))
                # Força reconexão
                self._conn = None
        return None

# Instância global do gerenciador
db_manager = DatabaseManager(DB_PATH)

# ==================== UTILITÁRIOS ====================
def sanitize(s: Any, max_len: int = 300) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]", "␃", str(s))
    return s[:max_len] + "…[truncado]" if len(s) > max_len else s

def _uid_gid() -> Tuple[Optional[int], Optional[int]]:
    try:
        return pwd.getpwnam(OWNER_USER).pw_uid, grp.getgrnam(OWNER_GROUP).gr_gid
    except KeyError:
        return None, None

def fix_perms(path: str, is_dir: bool = False) -> None:
    uid, gid = _uid_gid()
    if uid is not None:
        try:
            os.chown(path, uid, gid)
        except Exception:
            pass
    try:
        os.chmod(path, DIR_MODE if is_dir else FILE_MODE)
    except Exception:
        pass

def _safe_close(cam: Optional[DVRIPCam]) -> None:
    if cam:
        try:
            cam.close()
        except Exception:
            pass

def format_hhmmss(total_seconds: float) -> str:
    total_seconds = int(max(0, total_seconds))
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ==================== BANCO DE DADOS (FUNÇÕES ADAPTADAS) ====================
def init_db() -> None:
    """Cria diretórios e recria banco se necessário."""
    for d in [DOWNLOAD_DIR, TMP_DIR]:
        os.makedirs(d, exist_ok=True)
        fix_perms(d, True)
    db_manager._recreate_db()


def ensure_storage_dirs() -> None:
    """Recria os diretórios de armazenamento se forem removidos em runtime."""
    for directory in (DOWNLOAD_DIR, TMP_DIR):
        os.makedirs(directory, exist_ok=True)
        fix_perms(directory, True)

def get_meta(key: str) -> Optional[datetime]:
    """Retorna valor de metadado ou None."""
    if db_manager.degraded:
        return None
    cursor = db_manager.execute("SELECT value FROM metadata WHERE key = ?", (key,))
    if cursor:
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
    return None

def set_meta(key: str, dt: datetime) -> None:
    if db_manager.degraded:
        return
    db_manager.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        (key, dt.strftime("%Y-%m-%d %H:%M:%S")),
    )

def get_camera_db_status() -> int:
    if db_manager.degraded:
        return 0  # assume offline se degradado
    cursor = db_manager.execute("SELECT status FROM camera_status WHERE id = 1")
    if cursor:
        row = cursor.fetchone()
        return row[0] if row else 0
    return 0

def get_camera_status_changed_at() -> Optional[datetime]:
    if db_manager.degraded:
        return None
    cursor = db_manager.execute("SELECT updated_at FROM camera_status WHERE id = 1")
    if cursor:
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
    return None

def set_camera_db_status(status: int) -> None:
    if db_manager.degraded:
        return
    db_manager.execute(
        "UPDATE camera_status SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
        (status,),
    )

def is_downloaded(rel_path: str) -> bool:
    """Verifica se o arquivo já foi baixado (usando banco e sistema de arquivos)."""
    abs_path = os.path.join(DOWNLOAD_DIR, rel_path)
    # Primeiro, verifica se o arquivo existe fisicamente
    if os.path.isfile(abs_path):
        # Se banco está degradado, retorna True apenas baseado no arquivo
        if db_manager.degraded:
            return True
        # Tenta inserir no banco se não estiver registrado
        cursor = db_manager.execute("SELECT 1 FROM downloads WHERE rel_path = ?", (rel_path,))
        if cursor and not cursor.fetchone():
            db_manager.execute(
                "INSERT OR REPLACE INTO downloads (rel_path, file_size) VALUES (?, ?)",
                (rel_path, os.path.getsize(abs_path)),
            )
        return True
    else:
        # Se arquivo sumiu, remove do banco
        if not db_manager.degraded:
            db_manager.execute("DELETE FROM downloads WHERE rel_path = ?", (rel_path,))
        return False

def mark_downloaded(rel_path: str, size: int) -> None:
    if db_manager.degraded:
        return
    db_manager.execute(
        "INSERT OR REPLACE INTO downloads (rel_path, file_size) VALUES (?, ?)",
        (rel_path, size),
    )
    fix_perms(DB_PATH)

def remove_download_record(rel_path: str) -> None:
    if db_manager.degraded:
        return
    db_manager.execute("DELETE FROM downloads WHERE rel_path = ?", (rel_path,))

def clean_tmp() -> None:
    ensure_storage_dirs()
    for f in Path(TMP_DIR).glob("*.part"):
        try:
            f.unlink()
        except Exception:
            pass

# ==================== CIRCUIT BREAKER (ADAPTADO) ====================
class CircuitBreaker:
    def is_available(self, rel_path: str) -> bool:
        if db_manager.degraded:
            return True  # Em modo degradado, tenta baixar sempre
        cursor = db_manager.execute(
            "SELECT permanent, quarantined_until FROM file_failures WHERE rel_path = ?",
            (rel_path,),
        )
        if cursor:
            row = cursor.fetchone()
            if not row:
                return True
            if row[0]:
                return False
            if row[1]:
                try:
                    if datetime.now() < datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S"):
                        return False
                except Exception:
                    pass
        return True

    def record_failure(self, rel_path: str, error: str) -> None:
        if db_manager.degraded:
            return
        cursor = db_manager.execute("SELECT failure_count FROM file_failures WHERE rel_path = ?", (rel_path,))
        count = 0
        if cursor:
            row = cursor.fetchone()
            count = (row[0] + 1) if row else 1
        else:
            count = 1
        now = datetime.now()
        err = (error or "")[:500]
        if count >= CB_MAX_FAILURES:
            db_manager.execute(
                "INSERT OR REPLACE INTO file_failures "
                "(rel_path, failure_count, last_failure_at, quarantined_until, permanent, last_error) "
                "VALUES (?, ?, ?, NULL, 1, ?)",
                (rel_path, count, now.isoformat(), err),
            )
            log.warning(f"Circuit breaker: {sanitize(rel_path)} PERMANENTE após {count} falhas")
        else:
            backoff = CB_BACKOFF_LEVELS[min(count - 1, len(CB_BACKOFF_LEVELS) - 1)]
            until = (now + timedelta(seconds=backoff)).strftime("%Y-%m-%d %H:%M:%S")
            db_manager.execute(
                "INSERT OR REPLACE INTO file_failures "
                "(rel_path, failure_count, last_failure_at, quarantined_until, permanent, last_error) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (rel_path, count, now.isoformat(), until, err),
            )

    def record_success(self, rel_path: str) -> None:
        if db_manager.degraded:
            return
        db_manager.execute("DELETE FROM file_failures WHERE rel_path = ?", (rel_path,))

# ==================== WHATSAPP ====================
class WhatsAppNotifier:
    def __init__(self) -> None:
        self.script = Path("/var/www/opt/scripts/msg/send_whatsapp_media.py")

    def _send_blocking(self, msg: str) -> bool:
        cmd = [sys.executable, str(self.script), "-n", ",".join(WHATSAPP_NUMBERS), "-m", msg]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
            if result.returncode != 0:
                err_detail = sanitize(result.stderr or result.stdout or "sem detalhes")
                log.warning(f"WhatsApp: envio falhou (código {result.returncode}): {err_detail}")
                return False
            return True
        except Exception as e:
            log.error(f"WhatsApp: {e}")
            return False

    def _send(self, msg: str) -> bool:
        if not self.script.exists() or not WHATSAPP_NUMBERS:
            return False
        threading.Thread(target=self._send_blocking, args=(msg,), daemon=False).start()
        return True

    def send_connectivity(self, state: str, prev: Optional[str] = None, dur: Optional[int] = None) -> bool:
        if state == "db_online":
            label = "BANCO RECUPERADO ✅"
            prev_line = ""
        elif state == "db_offline":
            label = "BANCO EM MODO DEGRADADO ⚠️"
            prev_line = "\nVerifique permissões e espaço em disco."
        else:
            label = "ONLINE ✅" if state == "online" else "OFFLINE ❌"
            dur_str = f" — permaneceu por {format_hhmmss(dur)}" if dur and dur > 0 else ""
            prev_line = f"\nEstado anterior: {prev.upper()}{dur_str}" if prev else ""
            dur = None
        return self._send(
            f"🔔 Status da câmera: *{label}*{prev_line}\n"
            f"🕒 {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}\n"
            f"🔗 https://cam.svox.com.br/\n"
            f"_Sistema de Monitoramento SVOX_"
        )

    def send_download(self, baixados: List[Dict], total: int) -> bool:
        videos = sum(1 for p in baixados if p.get("tipo") == "video")
        fotos = sum(1 for p in baixados if p.get("tipo") == "foto")
        interval = "N/A"
        if baixados:
            begins = [p["BeginTime"] for p in baixados if p.get("BeginTime")]
            ends = [
                p.get("EndTime", p.get("BeginTime", ""))
                for p in baixados
                if p.get("EndTime") or p.get("BeginTime")
            ]
            if begins and ends:
                try:
                    dt_first = datetime.strptime(min(begins), "%Y-%m-%d %H:%M:%S")
                    dt_last = datetime.strptime(max(ends), "%Y-%m-%d %H:%M:%S")
                    interval = (
                        f"{dt_first.strftime('%d/%m/%Y %H:%M:%S')} até "
                        f"{dt_last.strftime('%d/%m/%Y %H:%M:%S')}"
                    )
                except Exception:
                    pass
        return self._send(
            f"🚨 *Movimento na Câmera*\n"
            f"🕒 Intervalo: *{interval}*\n"
            f"📹 Vídeos: *{videos}*\n"
            f"📷 Fotos: *{fotos}*\n"
            f"📦 Total: *{total}*\n"
            f"Download concluído. ({datetime.now().strftime('%d/%m/%Y às %H:%M:%S')})"
        )

# ==================== CONECTIVIDADE ====================
class ConnectivityMonitor:
    @staticmethod
    def is_online() -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TIMEOUT_CONNECT)
            result = sock.connect_ex((HOST, PORT))
            sock.close()
            return result == 0
        except Exception:
            return False

# ==================== CÂMERA ====================
def conectar(silent: bool = False) -> Optional[DVRIPCam]:
    retries = 1 if silent else LOGIN_MAX_RETRIES
    for attempt in range(1, retries + 1):
        cam_obj: Optional[DVRIPCam] = None
        login_ok: bool = False

        def target() -> None:
            nonlocal cam_obj, login_ok
            try:
                cam_obj = DVRIPCam(HOST, user=USER, password=PASSWORD, port=PORT)
                login_ok = cam_obj.login()
            except Exception as e:
                if not silent:
                    log.warning(f"Tentativa {attempt}/{retries}: {e}")

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(15)
        if t.is_alive():
            if not silent:
                log.warning(f"Tentativa {attempt}/{retries}: Timeout")
            if cam_obj:
                try:
                    cam_obj.close()
                except Exception:
                    pass
            cam_obj, login_ok = None, False
        if login_ok and cam_obj:
            if not silent:
                log.info("Login OK!")
            return cam_obj
        if attempt < retries:
            time.sleep(LOGIN_BACKOFF_SECONDS * attempt)
    if not silent:
        log.error(f"Falha no login após {retries} tentativas")
    return None

def listar_com_timeout(cam: DVRIPCam, tipo: str, start: str, end: str) -> List[Dict]:
    result: List[Dict] = []

    def target() -> None:
        nonlocal result
        try:
            files = cam.list_local_files(start, end, tipo, channel=0)
            result = files if isinstance(files, list) else []
        except Exception as e:
            log.error(f"Erro ao listar {tipo}: {sanitize(e)}")

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(CAMERA_IO_TIMEOUT)
    if t.is_alive():
        log.warning(f"⏱️ Timeout ao listar {tipo}. Encerrando ciclo.")
        _safe_close(cam)
    return result

def listar_fatiado(cam: DVRIPCam, tipo: str, start_dt: datetime, end_dt: datetime) -> List[Dict]:
    arquivos: List[Dict] = []
    current = start_dt
    while current < end_dt:
        next_dt = min(current + timedelta(hours=MAX_QUERY_CHUNK_HOURS), end_dt)
        arquivos.extend(
            listar_com_timeout(
                cam,
                tipo,
                current.strftime("%Y-%m-%d %H:%M:%S"),
                next_dt.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        current = next_dt
    return arquivos

# ==================== LISTAGEM E FILTRAGEM ====================
def extrair_tag(filename: str) -> str:
    m = re.search(r"\[(\w+)\]", filename)
    return m.group(1) if m else ""

def filtrar_m(lista: List[Dict]) -> List[Dict]:
    return [item for item in lista if extrair_tag(item.get("FileName", "")) == "M"]

def extrair_id(filename: str) -> Optional[str]:
    m = re.search(r"\[(_[0-9a-fA-F]+)\]", filename)
    return m.group(1) if m else None

def deduplicar(lista: List[Dict]) -> List[Dict]:
    grupos: Dict[str, List[Dict]] = {}
    for item in lista:
        aid = extrair_id(item.get("FileName", ""))
        if not aid:
            grupos.setdefault(f"file:{item['FileName']}", []).append(item)
            continue
        grupos.setdefault(aid, []).append(item)

    result: List[Dict] = []
    for items in grupos.values():
        ordered = sorted(items, key=lambda item: item.get("BeginTime", ""))
        clusters: List[List[Dict]] = []
        previous_begin: Optional[datetime] = None
        for item in ordered:
            try:
                begin = datetime.strptime(
                    item["BeginTime"], "%Y-%m-%d %H:%M:%S"
                )
            except (KeyError, TypeError, ValueError):
                begin = None
            if (
                not clusters
                or begin is None
                or previous_begin is None
                or (begin - previous_begin).total_seconds()
                > WINDOW_TOLERANCE_SECONDS
            ):
                clusters.append([])
            clusters[-1].append(item)
            previous_begin = begin

        for cluster in clusters:
            # A consulta da câmera pode retornar uma versão curta e depois a
            # versão completa do mesmo evento. Baixar somente a maior janela
            # evita mover a gravação incompleta para o nome final.
            result.append(max(
                cluster,
                key=lambda item: (
                    item.get("EndTime", item.get("BeginTime", "")),
                    item.get("BeginTime", ""),
                ),
            ))
    return result

def mesclar_ordenar(videos: List[Dict], fotos: List[Dict]) -> List[Dict]:
    for v in videos:
        v["tipo"] = "video"
    for f in fotos:
        f["tipo"] = "foto"
    return sorted(deduplicar(videos + fotos), key=lambda x: x.get("BeginTime", ""))

def extrair_lote(pendentes: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    lote: List[Dict] = []
    restantes: List[Dict] = []
    total = 0
    videos = 0
    for item in pendentes:
        if total >= 10 or videos >= 3:
            restantes.append(item)
            continue
        lote.append(item)
        total += 1
        if item.get("tipo") == "video":
            videos += 1
    return lote, restantes

# ==================== VALIDAÇÃO DE MÍDIA ====================
def corrigir_jpeg(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            data = f.read()
        if not data:
            return False
        pos = data.find(JPEG_SOI)
        if pos < 0:
            return False
        if pos > 0:
            with open(path, "wb") as f:
                f.write(data[pos:])
        return True
    except Exception:
        return False

def validar_h264(path: str) -> bool:
    try:
        if os.path.getsize(path) < H264_MIN_SIZE:
            return False
        with open(path, "rb") as f:
            header = f.read(H264_HEADER_SIZE)
        return H264_NAL_LONG in header or H264_NAL_SHORT in header
    except Exception:
        return False

def validar_arquivo(path: str, tipo: str) -> bool:
    if tipo == "foto":
        return corrigir_jpeg(path)
    if tipo == "video":
        return validar_h264(path)
    return True

# ==================== DOWNLOAD UTILS ====================
def criar_dirs(item: Dict) -> str:
    dt = datetime.strptime(item["BeginTime"], "%Y-%m-%d %H:%M:%S")
    path = os.path.join(
        DOWNLOAD_DIR,
        dt.strftime("%Y-%m-%d"),
        "video" if item["tipo"] == "video" else "fotos",
    )
    os.makedirs(path, exist_ok=True)
    fix_perms(path, True)
    fix_perms(os.path.dirname(path), True)
    return path

# Regex para o nome ORIGINAL da câmera (com begin-end)
_FILENAME_RE = re.compile(
    r"^(?P<begin>\d{2}\.\d{2}\.\d{2})-(?P<end>\d{2}\.\d{2}\.\d{2})"
    r"\[(?P<tag>\w+)\]\[(?P<id>_[0-9a-fA-F]+)\]\[(?P<ch>\d+)\]\.(?P<ext>\w+)$"
)

def obter_destino(item: Dict) -> Tuple[str, str, str]:
    """
    ⭐ CORREÇÃO: Preserva begin-END no nome do arquivo.
    Antes o end era descartado, fazendo com que versões incompleta e completa
    do mesmo evento gerassem o mesmo nome → a completa nunca era baixada.
    Agora: 19.41.20-19.42.00[M][_4d1][0].h264  (incompleto, 40s)
           19.41.20-19.43.40[M][_4d1][0].h264  (completo, 140s)
    Ambos coexistem no disco até o DuplicateVideoCleaner remover o incompleto.
    """
    destino = criar_dirs(item)
    basename = os.path.basename(item["FileName"])
    m = _FILENAME_RE.match(basename)
    if m:
        # Preserva begin-end para permitir dedup de incompletos
        canonico = (
            f"{m.group('begin')}-{m.group('end')}[{m.group('tag')}]"
            f"[{m.group('id')}][{m.group('ch')}].{m.group('ext')}"
        )
        safe = re.sub(r"[^\w\-\.\[\]]", "_", canonico)
    else:
        safe = re.sub(r"[^\w\-\.\[\]]", "_", basename)
    rel_path = os.path.relpath(os.path.join(destino, safe), DOWNLOAD_DIR)
    return os.path.join(DOWNLOAD_DIR, rel_path), rel_path, safe

# ==================== WORKER DE DOWNLOAD ====================
def _download_timeout(
    cam: DVRIPCam, begin: str, end: str, filename: str, tmp_path: str, timeout: int
) -> None:
    cam.download_file(begin, end, filename, tmp_path)

def _reconectar(host: str, user: str, password: str, port: int) -> DVRIPCam:
    novo = DVRIPCam(host, user=user, password=password, port=port)
    if not novo.login():
        raise RuntimeError("Falha ao reconectar após erro no download")
    return novo

def _batch_worker(
    host: str,
    user: str,
    password: str,
    port: int,
    plano: List[Tuple[Dict, str, str, str]],
    progress_q: Queue,
    control_q: Queue,
    timeout: int,
) -> None:
    try:
        cam = DVRIPCam(host, user=user, password=password, port=port)
        if not cam.login():
            progress_q.put(("fatal", "Falha no login"))
            return
    except Exception as e:
        progress_q.put(("fatal", f"Erro: {e}"))
        return

    sucessos: List[Tuple[str, str, str, int, Dict]] = []
    try:
        for idx, (item, tmp_path, target, rel_path) in enumerate(plano, 1):
            try:
                control_q.get_nowait()
                break
            except Empty:
                pass
            progress_q.put(("start", rel_path, tmp_path))
            try:
                begin = item["BeginTime"]
                end = begin if item["tipo"] == "foto" else item["EndTime"]
                _download_timeout(cam, begin, end, item["FileName"], tmp_path, timeout)
                if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                    raise RuntimeError("Arquivo vazio")
                if not validar_arquivo(tmp_path, item["tipo"]):
                    raise RuntimeError("Arquivo corrompido")
                sucessos.append((tmp_path, target, rel_path, os.path.getsize(tmp_path), item))
                progress_q.put(("file_downloaded", rel_path))
            except Exception as e:
                progress_q.put(("file_error", rel_path, str(e)))
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                try:
                    cam.close()
                except Exception:
                    pass
                try:
                    cam = _reconectar(host, user, password, port)
                except Exception as reconnect_err:
                    progress_q.put(("fatal", f"Reconexão falhou: {reconnect_err}"))
                    break
    finally:
        try:
            cam.close()
        except Exception:
            pass

    if sucessos:
        moved: List[Tuple[str, int, Dict]] = []
        for tmp_path, target, rel_path, size, item in sucessos:
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(tmp_path, target)
                fix_perms(target)
                moved.append((rel_path, size, item))
            except Exception as e:
                log.error(f"Falha ao mover {rel_path}: {e}")
        progress_q.put(("files_moved", moved))
    progress_q.put(("batch_done", None))

def baixar_lote(
    host: str,
    user: str,
    password: str,
    port: int,
    pendentes: List[Dict],
    cb: CircuitBreaker,
    timeout: int,
) -> Tuple[int, int, List[Dict]]:
    global _active_proc
    ensure_storage_dirs()
    plano: List[Tuple[Dict, str, str, str]] = []
    for item in pendentes:
        target, rel_path, safe = obter_destino(item)
        tmp_path = os.path.join(TMP_DIR, f"{safe}.part")
        for p in [target, tmp_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        plano.append((item, tmp_path, target, rel_path))

    progress_q: Queue = Queue()
    control_q: Queue = Queue()
    proc = Process(
        target=_batch_worker,
        args=(host, user, password, port, plano, progress_q, control_q, timeout),
    )
    proc.start()
    _active_proc = proc

    sucessos = 0
    falhas = 0
    itens_sucesso: List[Dict] = []
    current_rel: Optional[str] = None
    current_tmp: Optional[str] = None
    last_size = 0
    stall_start = time.time()
    batch_start = time.time()
    file_start = time.time()
    max_gap = 0.0

    def drain() -> None:
        nonlocal sucessos, falhas, current_rel, current_tmp, last_size, stall_start
        nonlocal file_start, max_gap
        while True:
            try:
                msg = progress_q.get_nowait()
            except Empty:
                return
            kind = msg[0]
            if kind == "start":
                current_rel, current_tmp = msg[1], msg[2]
                last_size, stall_start = 0, time.time()
                file_start, max_gap = time.time(), 0.0
            elif kind == "file_downloaded":
                dur = time.time() - file_start
                log.info(
                    f"📊 Stall-stats: {sanitize(current_rel)} | maior pausa sem "
                    f"crescimento: {max_gap:.1f}s | duração total: {dur:.1f}s | "
                    f"tamanho final: {last_size} bytes"
                )
                current_rel, current_tmp = None, None
            elif kind == "files_moved":
                for rel, size, item in msg[1]:
                    target_path = os.path.join(DOWNLOAD_DIR, rel)
                    if not os.path.isfile(target_path):
                        log.warning(
                            f"🗑️ Arquivo removido antes da confirmação: {sanitize(rel)}"
                        )
                        cb.record_failure(rel, "Arquivo final removido durante o download")
                        falhas += 1
                        continue
                    mark_downloaded(rel, size)
                    cb.record_success(rel)
                    sucessos += 1
                    itens_sucesso.append(item)
            elif kind == "file_error":
                rel, err = msg[1], msg[2]
                dur = time.time() - file_start
                log.info(
                    f"📊 Stall-stats (falhou): {sanitize(rel)} | maior pausa sem "
                    f"crescimento: {max_gap:.1f}s | duração total: {dur:.1f}s | "
                    f"último tamanho visto: {last_size} bytes"
                )
                falhas += 1
                cb.record_failure(rel, str(err))
                current_rel, current_tmp = None, None

    while proc.is_alive():
        time.sleep(CHECK_INTERVAL)
        drain()
        now = time.time()
        if _stop_event.is_set():
            log.info("🛑 Shutdown solicitado, abortando download ativo")
            try:
                control_q.put_nowait("cancel")
            except Exception:
                pass
            break
        if current_tmp:
            if os.path.exists(current_tmp):
                size = os.path.getsize(current_tmp)
                if size > last_size:
                    gap = now - stall_start
                    if gap > max_gap:
                        max_gap = gap
                    last_size, stall_start = size, now
                elif (now - stall_start) > STALL_TIMEOUT:
                    log.warning(
                        f"⏱️ Download sem progresso por {STALL_TIMEOUT}s: "
                        f"{sanitize(current_rel)}"
                    )
                    break
            elif (now - file_start) > STALL_TIMEOUT:
                log.warning(
                    f"🗑️ Arquivo temporário removido durante download: "
                    f"{sanitize(current_rel)}"
                )
                break
        if (now - batch_start) > MAX_DOWNLOAD_TIME:
            break

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=3)
    else:
        proc.join(timeout=2)
    drain()
    _active_proc = None

    if current_tmp and os.path.exists(current_tmp):
        try:
            os.remove(current_tmp)
        except Exception:
            pass

    return sucessos, falhas, itens_sucesso

# ==================== ALERTA DE MOVIMENTO ====================
def executar_alert_motion() -> None:
    script = ALERT_SCRIPT
    if not os.path.isabs(script):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
    if not os.path.exists(script):
        return
    try:
        subprocess.Popen(
            [sys.executable, script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        log.info(f"alert_motion.py disparado em background.")
    except Exception as e:
        log.error(f"Falha ao iniciar alert_motion.py: {e}")

# ==================== CONSULTA INTELIGENTE ====================
def determinar_janela(
    last_mv: Optional[datetime], last_sanity: Optional[datetime]
) -> Tuple[datetime, datetime, bool]:
    now = datetime.now()
    needs_sanity = (
        not last_sanity
        or (now - last_sanity).total_seconds() / 3600 >= SANITY_CHECK_INTERVAL_HOURS
    )
    if needs_sanity:
        start = last_mv or (now - timedelta(days=FIRST_RUN_DAYS_BACK))
        start = max(start, now - timedelta(days=FIRST_RUN_DAYS_BACK))
        log.info(f"Sanidade: {start.strftime('%Y-%m-%d %H:%M:%S')} até agora (fatiado por 1h)")
    else:
        if last_mv:
            start = last_mv - timedelta(hours=4)
        else:
            start = now - timedelta(hours=4)
        log.info(f"Rápida: {start.strftime('%Y-%m-%d %H:%M:%S')} até agora")
    return start, now, needs_sanity

# ==================== DEDUPLICAÇÃO DE VÍDEOS (adaptado) ====================
class DuplicateVideoCleaner:
    """
    Remove vídeos duplicados do mesmo evento de movimento.
    Adaptado para lidar com falhas de banco sem interromper a limpeza.
    """
    MONITORED_EXTENSIONS = {".h264", ".mp4", ".avi"}

    def __init__(self, check_interval_minutes: int = 60):
        self.check_interval_minutes = check_interval_minutes
        self._last_check: Optional[datetime] = None

    def should_check(self) -> bool:
        if self._last_check is None:
            return True
        elapsed = (datetime.now() - self._last_check).total_seconds() / 60
        return elapsed >= self.check_interval_minutes

    def check_and_clean(self) -> Tuple[int, int]:
        self._last_check = datetime.now()
        now = datetime.now()
        video_dir = Path(DOWNLOAD_DIR)
        if not video_dir.exists():
            return 0, 0

        cutoff_date = (now - timedelta(days=DEDUP_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        videos: List[Path] = []
        for entry in os.scandir(str(video_dir)):
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith('.'):
                continue
            if len(entry.name) == 10 and entry.name < cutoff_date:
                continue

            video_subdir = os.path.join(entry.path, "video")
            if not os.path.isdir(video_subdir):
                continue

            for fentry in os.scandir(video_subdir):
                if not fentry.is_file(follow_symlinks=False):
                    continue
                if fentry.name.startswith('.') or fentry.name.endswith('.part'):
                    continue
                ext = os.path.splitext(fentry.name)[1].lower()
                if ext not in self.MONITORED_EXTENSIONS:
                    continue
                try:
                    stat = fentry.stat()
                    age = now.timestamp() - stat.st_mtime
                    if age < DEDUP_MIN_FILE_AGE_SECONDS:
                        continue
                except Exception:
                    continue
                videos.append(Path(fentry.path))

        if not videos:
            return 0, 0

        groups: Dict[Tuple[str, str, str], List[Path]] = {}
        for v in videos:
            m = _FILENAME_RE.match(v.name)
            if not m:
                continue
            # O mesmo evento pode ser retornado pela câmera com início alguns
            # segundos diferente quando a gravação é consultada novamente.
            # Mantém a data no agrupamento para não misturar eventos de dias
            # distintos com o mesmo identificador.
            date_key = v.parent.parent.name
            key = (date_key, m.group('id'), m.group('ch'))
            if key not in groups:
                groups[key] = []
            groups[key].append(v)

        removed_count = 0
        freed_bytes = 0
        for files in groups.values():
            ordered = sorted(files, key=self._begin_seconds)
            clusters: List[List[Path]] = []
            for filepath in ordered:
                if (
                    not clusters
                    or self._begin_seconds(filepath)
                    - self._begin_seconds(clusters[-1][-1])
                    > WINDOW_TOLERANCE_SECONDS
                ):
                    clusters.append([])
                clusters[-1].append(filepath)

            for cluster in clusters:
                if len(cluster) < 2:
                    continue
                best = self._select_best(cluster)
                for f in cluster:
                    if f == best:
                        continue
                    try:
                        file_size = f.stat().st_size
                    except Exception:
                        continue
                    rel_path = os.path.relpath(str(f), DOWNLOAD_DIR)
                    try:
                        f.unlink()
                        removed_count += 1
                        freed_bytes += file_size
                        # Tenta remover do banco, mas não interrompe se falhar
                        try:
                            remove_download_record(rel_path)
                        except Exception as e:
                            log.warning(f"Não foi possível remover do banco: {e}")
                        log.info(
                            f"🧹 Dedup removido: {rel_path} "
                            f"({file_size / 1024:.0f}KB) — mantido: {best.name}"
                        )
                    except Exception as e:
                        log.warning(f"🧹 Falha ao remover duplicata {rel_path}: {e}")

        if removed_count > 0:
            log.info(
                f"🧹 ✅ Dedup concluído: {removed_count} duplicata(s), "
                f"{freed_bytes / (1024*1024):.1f}MB liberados"
            )
        return removed_count, freed_bytes

    def _select_best(self, files: List[Path]) -> Path:
        def sort_key(f: Path) -> Tuple[int, int]:
            m = _FILENAME_RE.match(f.name)
            if not m:
                return (0, 0)
            try:
                begin = datetime.strptime(m.group('begin'), "%H.%M.%S")
                end = datetime.strptime(m.group('end'), "%H.%M.%S")
                duration = int((end - begin).total_seconds())
                if duration < 0:
                    duration += 86400
            except Exception:
                duration = 0
            try:
                size = f.stat().st_size
            except Exception:
                size = 0
            return (duration, size)

        return max(files, key=sort_key)

    @staticmethod
    def _begin_seconds(path: Path) -> int:
        match = _FILENAME_RE.match(path.name)
        if not match:
            return 0
        try:
            value = datetime.strptime(match.group("begin"), "%H.%M.%S")
            return value.hour * 3600 + value.minute * 60 + value.second
        except ValueError:
            return 0

# ==================== GESTÃO DE ESPAÇO EM DISCO (adaptado) ====================
class DiskSpaceManager:
    """
    Rodízio de arquivos para controle de espaço em disco.
    Adaptado para lidar com falhas de banco sem interromper a limpeza.
    """
    MONITORED_EXTENSIONS = {".jpg", ".h264", ".mp4", ".avi"}

    def __init__(self):
        self._last_check: Optional[datetime] = None

    def should_check(self) -> bool:
        if self._last_check is None:
            return True
        elapsed = (datetime.now() - self._last_check).total_seconds() / 60
        return elapsed >= DISK_CHECK_INTERVAL_MINUTES

    def check_and_rotate(self) -> Tuple[int, int]:
        self._last_check = datetime.now()

        stale_removed = self._clean_stale_parts()
        if stale_removed > 0:
            log.info(f"💾 🧹 {stale_removed} arquivo(s) .part órfão(s) removido(s)")

        media_files = self._collect_media_files()
        total_bytes = sum(f.stat().st_size for f in media_files)
        log.info(
            f"💾 Disco: {total_bytes / (1024*1024):.1f}MB / "
            f"{MAX_DISK_MB}MB ({len(media_files)} arquivos de mídia)"
        )

        if total_bytes <= MAX_DISK_BYTES:
            removed_dirs = self._remove_empty_dirs()
            if removed_dirs:
                log.info(f"💾 {removed_dirs} diretório(s) vazio(s) removido(s)")
            return 0, 0

        bytes_to_free = DISK_CLEANUP_BYTES
        log.warning(
            f"💾 ⚠️ Limite excedido! Liberando ~{bytes_to_free / (1024*1024):.0f}MB "
            f"({DISK_CLEANUP_PERCENT}% de {MAX_DISK_MB}MB)"
        )

        media_files.sort(key=lambda f: f.stat().st_mtime)
        removed_count = 0
        freed_bytes = 0
        for filepath in media_files:
            if freed_bytes >= bytes_to_free:
                break
            file_size = filepath.stat().st_size
            rel_path = os.path.relpath(str(filepath), DOWNLOAD_DIR)
            try:
                filepath.unlink()
                removed_count += 1
                freed_bytes += file_size
                try:
                    remove_download_record(rel_path)
                except Exception as e:
                    log.warning(f"Não foi possível remover do banco: {e}")
                log.info(f"💾 🗑️ Removido: {rel_path} ({file_size / 1024:.0f}KB)")
            except Exception as e:
                log.warning(f"💾 Falha ao remover {rel_path}: {e}")

        removed_dirs = self._remove_empty_dirs()
        log.info(
            f"💾 ✅ Rodízio concluído: {removed_count} arquivo(s), "
            f"{freed_bytes / (1024*1024):.1f}MB liberado(s), "
            f"{removed_dirs} diretório(s) vazio(s)"
        )
        return removed_count, freed_bytes

    def _clean_stale_parts(self) -> int:
        removed = 0
        tmp_dir = TMP_DIR
        if not os.path.isdir(tmp_dir):
            return 0
        now = time.time()
        try:
            for entry in os.scandir(tmp_dir):
                if entry.is_file(follow_symlinks=False) and entry.name.endswith('.part'):
                    try:
                        age = now - entry.stat().st_mtime
                        if age > STALE_PART_MAX_AGE_SECONDS:
                            os.unlink(entry.path)
                            removed += 1
                            log.debug(f"💾 .part órfão removido: {entry.name} ({age:.0f}s)")
                    except Exception:
                        pass
        except Exception:
            pass
        return removed

    def _collect_media_files(self) -> List[Path]:
        media_files: List[Path] = []
        ext_set = self.MONITORED_EXTENSIONS
        base = DOWNLOAD_DIR
        try:
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for name in files:
                    if name.startswith('.') or name.endswith('.part'):
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if ext in ext_set:
                        media_files.append(Path(root) / name)
        except Exception:
            pass
        return media_files

    def _remove_empty_dirs(self) -> int:
        removed = 0
        base = DOWNLOAD_DIR
        try:
            for dirpath, dirnames, filenames in os.walk(base, topdown=False):
                if dirpath == base or dirpath == TMP_DIR:
                    continue
                if not filenames and not dirnames:
                    try:
                        os.rmdir(dirpath)
                        removed += 1
                    except OSError:
                        pass
        except Exception:
            pass
        return removed

# ==================== WATCHDOG DO BANCO ====================
def db_watchdog_thread() -> None:
    """Thread que monitora a saúde do banco e tenta recuperá-lo periodicamente."""
    log.info("🐕 Watchdog do banco iniciado (verifica a cada 5min).")
    while not _stop_event.is_set():
        try:
            # Tenta obter conexão; se falhar, entra em modo degradado e recria
            if not db_manager.degraded:
                try:
                    conn = db_manager.get_connection()
                    conn.cursor().execute("SELECT 1").fetchone()
                except Exception as e:
                    log.error(f"Watchdog detectou problema no banco: {e}")
                    db_manager._recreate_db()
        except Exception as e:
            log.error(f"Erro no watchdog: {e}")
        # Aguarda 5 minutos ou até receber sinal de parada
        _stop_event.wait(timeout=300)

# ==================== CICLO DE EXECUÇÃO ====================
def run_cycle(
    cb: CircuitBreaker,
    disk_mgr: DiskSpaceManager,
    dedup_cleaner: DuplicateVideoCleaner,
) -> int:
    global _cam
    ensure_storage_dirs()

    # 1. Verificação TCP
    is_tcp_online = ConnectivityMonitor.is_online()
    db_status = get_camera_db_status()
    if not is_tcp_online:
        if db_status != 0:
            changed_at = get_camera_status_changed_at()
            dur = (datetime.utcnow() - changed_at).total_seconds() if changed_at else 0
            set_camera_db_status(0)
            log.warning(
                f"Câmera offline (porta fechada). Estava ONLINE há {format_hhmmss(dur)}."
            )
            try:
                WhatsAppNotifier().send_connectivity("offline", "online", dur=int(dur))
            except Exception:
                pass
        else:
            log.debug("Câmera offline (porta fechada). Aguardando.")
        return SLEEP_OFFLINE

    # 2. Modo silencioso se o DB já sabe que está offline
    known_offline = (db_status == 0)

    # 3. Conexão
    _cam = conectar(silent=known_offline)
    if not _cam:
        if not known_offline:
            changed_at = get_camera_status_changed_at()
            dur = (datetime.utcnow() - changed_at).total_seconds() if changed_at else 0
            set_camera_db_status(0)
            log.error(
                f"Câmera inoperante (porta aberta, DVRIP morto). Marcando OFFLINE. "
                f"Estava ONLINE há {format_hhmmss(dur)}."
            )
            try:
                WhatsAppNotifier().send_connectivity("offline", "online", dur=int(dur))
            except Exception:
                pass
        return SLEEP_OFFLINE

    # 4. Notificar recuperação se necessário
    if known_offline:
        changed_at = get_camera_status_changed_at()
        dur = (datetime.utcnow() - changed_at).total_seconds() if changed_at else 0
        log.info(f"Câmera recuperou. Marcando ONLINE. Estava OFFLINE há {format_hhmmss(dur)}.")
        try:
            WhatsAppNotifier().send_connectivity("online", "offline", dur=int(dur))
        except Exception:
            pass
        set_camera_db_status(1)

    # 5. Ciclo principal
    cam = _cam
    todos_sucessos_run: List[Dict] = []
    try:
        while not _stop_event.is_set():
            last_mv = get_meta("last_movement_time")
            last_sanity = get_meta("last_sanity_check")
            start_time, end_time, is_sanity = determinar_janela(last_mv, last_sanity)

            videos = filtrar_m(listar_fatiado(cam, "h264", start_time, end_time))
            fotos = filtrar_m(listar_fatiado(cam, "jpg", start_time, end_time))
            todos = mesclar_ordenar(videos, fotos)

            if todos:
                max_begin = max((item.get("BeginTime", "") for item in todos), default="")
                if max_begin:
                    try:
                        dt_max = datetime.strptime(max_begin, "%Y-%m-%d %H:%M:%S")
                        if not last_mv or dt_max > last_mv:
                            set_meta("last_movement_time", dt_max)
                    except Exception:
                        pass
                if is_sanity:
                    set_meta("last_sanity_check", datetime.now())
                    log.info("Sanidade concluída.")

            if not todos:
                log.info("Nenhum arquivo [M]. Aguardando.")
                break

            pendentes: List[Dict] = []
            for item in todos:
                _, rel_path, _ = obter_destino(item)
                target = os.path.join(DOWNLOAD_DIR, rel_path)
                if (
                    not (os.path.exists(target) and os.path.getsize(target) > 0)
                    and not is_downloaded(rel_path)
                ):
                    if cb.is_available(rel_path):
                        pendentes.append(item)

            if not pendentes:
                log.info("✅ Todos baixados. Aguardando.")
                break

            # Loop de lotes
            while pendentes and not _stop_event.is_set():
                lote, restantes = extrair_lote(pendentes)
                log.info(f"Baixando lote de {len(lote)} ({len(restantes)} restantes)")
                sucessos, falhas, itens_sucesso = baixar_lote(
                    HOST, USER, PASSWORD, PORT, lote, cb, DOWNLOAD_TIMEOUT_PER_FILE
                )
                log.info(f"Lote: {sucessos} sucesso(s), {falhas} falha(s)")
                if itens_sucesso:
                    todos_sucessos_run.extend(itens_sucesso)
                    executar_alert_motion()
                pendentes = restantes
                if _stop_event.is_set():
                    break

            log.info("Fila esgotada. Relistando para novos arquivos...")
            if _stop_event.wait(timeout=2):
                break

    except Exception as e:
        log.error(f"Erro no ciclo: {sanitize(e)}")
        _safe_close(cam)
        _cam = None
        return SLEEP_ERROR

    _safe_close(cam)
    _cam = None

    # Envia resumo WhatsApp
    if todos_sucessos_run and not _stop_event.is_set():
        try:
            WhatsAppNotifier().send_download(todos_sucessos_run, len(todos_sucessos_run))
        except Exception as e:
            log.warning(f"Falha ao enviar alerta: {e}")

    # Dedup de vídeos (roda ANTES do disk check para liberar duplicatas primeiro)
    if dedup_cleaner.should_check() and not _stop_event.is_set():
        try:
            dedup_cleaner.check_and_clean()
        except Exception as e:
            log.error(f"🧹 Erro no dedup de vídeos: {e}")

    # Verificação de espaço em disco (a cada 15 minutos)
    if disk_mgr.should_check() and not _stop_event.is_set():
        try:
            removed, freed = disk_mgr.check_and_rotate()
            if removed > 0:
                log.info(f"💾 Rodízio: {removed} arquivos, {freed/(1024*1024):.1f}MB liberados")
        except Exception as e:
            log.error(f"💾 Erro no rodízio de disco: {e}")

    if todos_sucessos_run:
        return SLEEP_ONLINE
    return SLEEP_IDLE

# ==================== INICIALIZAÇÃO COM RETRY ====================
def _init_with_retry() -> bool:
    for attempt in range(1, INIT_MAX_RETRIES + 1):
        try:
            clean_tmp()
            init_db()
            return True
        except Exception as e:
            log.error(f"Falha na inicialização (tentativa {attempt}/{INIT_MAX_RETRIES}): {e}")
            if attempt < INIT_MAX_RETRIES:
                time.sleep(INIT_RETRY_DELAY)
    return False

# ==================== SHUTDOWN GRACEFUL ====================
def _shutdown() -> None:
    global _cam, _active_proc
    log.info("🛑 Shutdown graceful iniciado...")
    if _active_proc and _active_proc.is_alive():
        try:
            _active_proc.terminate()
            _active_proc.join(timeout=5)
            if _active_proc.is_alive():
                _active_proc.kill()
                _active_proc.join(timeout=3)
        except Exception:
            pass
    _safe_close(_cam)
    _cam = None
    clean_tmp()
    log.info("✅ Serviço finalizado com sucesso")

# ==================== MAIN ====================
def main() -> None:
    global _cam
    log.info("🚀 Serviço iniciado (modo contínuo PM2)")
    if not _init_with_retry():
        log.critical("❌ Falha fatal na inicialização. Encerrando.")
        sys.exit(1)

    # Inicia thread watchdog do banco
    watchdog = threading.Thread(target=db_watchdog_thread, daemon=True)
    watchdog.start()

    cb = CircuitBreaker()
    disk_mgr = DiskSpaceManager()
    dedup_cleaner = DuplicateVideoCleaner(DEDUP_CHECK_INTERVAL_MINUTES)

    while not _stop_event.is_set():
        try:
            sleep_time = run_cycle(cb, disk_mgr, dedup_cleaner)
        except KeyboardInterrupt:
            break
        except sqlite3.DatabaseError as e:
            log.critical(f"Erro crítico no banco: {e}. Tentando recriar...")
            db_manager._recreate_db()
            sleep_time = SLEEP_ERROR
        except Exception as e:
            log.exception(f"Erro não tratado no ciclo: {e}")
            sleep_time = SLEEP_ERROR
        _safe_close(_cam)
        _cam = None
        if _stop_event.wait(timeout=sleep_time):
            break

    _shutdown()

# ============================================================
# ENTRYPOINT
# ============================================================
if __name__ == "__main__":
    def signal_handler(sig: int, frame: Any) -> None:
        sig_name = signal.Signals(sig).name
        log.info(f"Sinal {sig_name} recebido. Finalizando…")
        _stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    main()