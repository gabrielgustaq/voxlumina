#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# VoxLumina Bootstrapper
# Carrega variáveis de ambiente e inicia a aplicação de forma segura.
# Compatível com distros imutáveis (Fedora Silverblue, NixOS, etc.)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   VoxLumina — PDF → Audiobook        ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ── Verifica ambiente virtual ─────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo "  ✗  Ambiente virtual 'venv' não encontrado."
    echo "     Execute primeiro:"
    echo ""
    echo "       python -m venv venv"
    echo "       source venv/bin/activate"
    echo "       pip install -r requirements.txt"
    echo ""
    exit 1
fi

source venv/bin/activate
echo "  ✓  Ambiente virtual ativado."

# ── Carrega variáveis do .env ─────────────────────────────────────────────────
if [ -f ".env" ]; then
    # Ignora linhas em branco e comentários; exporta o restante
    set -a
    # shellcheck disable=SC1091
    source <(grep -v '^\s*#' .env | grep -v '^\s*$')
    set +a
    echo "  ✓  Configurações carregadas do .env"
else
    echo "  ⚠  Arquivo .env não encontrado. Usando padrões do sistema."
    echo "     Crie um .env com OLLAMA_URL, KOKORO_URL e OLLAMA_MODEL."
fi

# ── Verifica dependências externas ────────────────────────────────────────────
if ! command -v ffmpeg &> /dev/null; then
    echo ""
    echo "  ✗  FFmpeg não encontrado no PATH."
    echo "     Instale com:  sudo apt install ffmpeg"
    echo ""
    deactivate
    exit 1
fi
echo "  ✓  FFmpeg detectado."

# ── Garante estrutura de diretórios ───────────────────────────────────────────
mkdir -p input text audio configs
echo "  ✓  Diretórios verificados (input/ text/ audio/ configs/)"

echo ""

# ── Executa a aplicação ───────────────────────────────────────────────────────
python main.py

# ── Limpeza ao sair ───────────────────────────────────────────────────────────
deactivate
echo ""
echo "  VoxLumina encerrado."
