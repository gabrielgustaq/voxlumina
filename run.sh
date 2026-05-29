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
# O main.py usa python-dotenv para carregar o .env automaticamente.
# O bloco abaixo exporta as variáveis também para o shell (útil para scripts
# auxiliares ou se python-dotenv não estiver instalado).
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source <(grep -v '^\s*#' .env | grep -v '^\s*$')
    set +a
    echo "  ✓  .env carregado (shell + python-dotenv)"
else
    echo "  ⚠  Arquivo .env não encontrado."
    echo "     Crie o arquivo ou exporte as variáveis manualmente:"
    echo "     OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, KOKORO_URL, FLASK_PORT"
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
