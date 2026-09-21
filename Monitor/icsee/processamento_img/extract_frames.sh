#!/usr/bin/env bash
# Extrai frames de um vídeo H.264 raw (ou formato suportado)
# Uso: extract_frames.sh <video_path> <output_dir> [num_frames]
#   num_frames = 1  -> extrai 1 frame central
#   num_frames = 0  -> extrai todos os frames (1 frame por segundo)
#   num_frames > 1  -> extrai até N frames (1 frame por segundo)

set -euo pipefail

command -v ffmpeg >/dev/null || { echo "ffmpeg não encontrado"; exit 1; }

INPUT="$1"
OUTPUT_DIR="$2"
NUM_FRAMES="${3:-1}"

if [[ ! -f "$INPUT" ]]; then
    echo "Arquivo de vídeo não encontrado: $INPUT"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# Detecta se é H.264 raw
if [[ "$INPUT" == *.h264 || "$INPUT" == *.H264 ]]; then
    INPUT_OPTS="-f h264 -r 10"
else
    INPUT_OPTS=""
fi

# Função para extrair 1 frame central
extract_single() {
    # Tenta obter duração, fallback para 10s
    DURATION=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$INPUT" 2>/dev/null || echo "10")
    if [[ "$DURATION" == "N/A" || -z "$DURATION" ]]; then
        DURATION="10"
    fi
    SEEK=$(echo "$DURATION / 2" | bc -l 2>/dev/null || echo "5")
    ffmpeg $INPUT_OPTS -ss "$SEEK" -i "$INPUT" -vframes 1 -q:v 2 "$OUTPUT_DIR/frame_0001.jpg" -y -loglevel error
}

# Função para extrair todos os frames (1 fps)
extract_all() {
    local max_frames="$1"
    if [[ "$max_frames" -eq 0 ]]; then
        # Sem limite
        ffmpeg $INPUT_OPTS -i "$INPUT" -vf "fps=1" -vsync 0 -q:v 2 "$OUTPUT_DIR/frame_%04d.jpg" -loglevel error
    else
        ffmpeg $INPUT_OPTS -i "$INPUT" -vf "fps=1" -vsync 0 -q:v 2 -vframes "$max_frames" "$OUTPUT_DIR/frame_%04d.jpg" -loglevel error
    fi
}

# Executa conforme NUM_FRAMES
if [[ "$NUM_FRAMES" -eq 1 ]]; then
    extract_single
else
    # NUM_FRAMES = 0 ou >1: extrai todos (ou limitado)
    extract_all "$NUM_FRAMES"
fi

# Lista os arquivos gerados, limitando se NUM_FRAMES for 0 (todos) ou um número
if [[ "$NUM_FRAMES" -eq 0 ]]; then
    ls -1 "$OUTPUT_DIR"/*.jpg 2>/dev/null || true
else
    ls -1 "$OUTPUT_DIR"/*.jpg 2>/dev/null | head -n "$NUM_FRAMES" || true
fi