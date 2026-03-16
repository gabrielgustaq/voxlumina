"""
main.py — VoxLumina
─────────────────────────────────────────────────────────────────────────────
Sistema de acessibilidade: PDF Acadêmico → Audiobook Dinâmico

Fluxos disponíveis:
  1. PDF → (Docling) → Markdown + Imagens
          → (Ollama Vision) → Audiodescrições + Blocos JSON → salva em text/
  2. JSON existente → (Kokoro TTS + FFmpeg) → MP3 final

Interface Web de monitoramento: http://127.0.0.1:8080/monitor
─────────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import os
import subprocess
import threading
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string, send_from_directory

from modules.ingestor import Ingestor
from modules.inteligencia import InteligenciaOllama
from modules.narrador import NarradorKokoro, VOZES_PADRAO

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("VoxLumina")

# ─── Configurações (sobrepõe com variáveis de ambiente) ───────────────────────
KOKORO_URL   = os.getenv("KOKORO_URL",   "http://127.0.0.1:8880")
OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2-vision")
FLASK_PORT   = int(os.getenv("FLASK_PORT", 8080))

# Diretórios do projeto
DIR_INPUT  = Path("input")    # PDFs de entrada
DIR_TEXT   = Path("text")     # JSONs de roteiro
DIR_AUDIO  = Path("audio")    # MP3s finais
DIR_CONFIG = Path("configs")  # Configs de voz customizadas (legado)

for d in [DIR_INPUT, DIR_TEXT, DIR_AUDIO, DIR_CONFIG]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Estado global (compartilhado com Flask) ──────────────────────────────────
_estado = {
    "pipeline":    "idle",
    "ultimo_pdf":  "—",
    "ultimo_json": "—",
    "ultimo_audio":"—",
    "progresso":   "",
    "erros":       [],
}

# ─── Instâncias dos módulos ────────────────────────────────────────────────────
_ia       = InteligenciaOllama(OLLAMA_URL, OLLAMA_MODEL)
_narrador = NarradorKokoro(KOKORO_URL)
_ingestor = Ingestor(imagens_dir=Path("temp_imagens"))


# ═══════════════════════════════════════════════════════════════════════════════
# FLASK — Interface Web
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)


@app.route("/")
def serve_ui():
    """Serve o audiobook_ui.html original (pipeline web cliente-side)."""
    if Path("audiobook_ui.html").exists():
        return send_from_directory(".", "audiobook_ui.html")
    return "<h2>VoxLumina — coloque audiobook_ui.html neste diretório.</h2>"


@app.route("/status")
def api_status():
    """Endpoint JSON com status completo do sistema."""
    return jsonify({
        "estado": _estado,
        "servicos": {
            "kokoro": _narrador.health_check(),
            "ollama": "ONLINE" if _ia.health_check() else "OFFLINE",
        },
        "arquivos": {
            "pdfs_input":     [f.name for f in sorted(DIR_INPUT.glob("*.pdf"))],
            "jsons_text":     [f.name for f in sorted(DIR_TEXT.glob("*.json"))],
            "audios_gerados": [f.name for f in sorted(DIR_AUDIO.glob("*.mp3"))],
        },
    })


@app.route("/monitor")
def monitor_dashboard():
    """Dashboard HTML de monitoramento (auto-refresh 4s)."""
    kokoro_status = _narrador.health_check()
    ollama_ok     = _ia.health_check()
    dot_k = "🟢" if kokoro_status in ("ONLINE", "REACHABLE") else "🔴"
    dot_o = "🟢" if ollama_ok else "🔴"
    erros_html = "".join(
        f'<li style="color:#ef4444">{e}</li>' for e in _estado["erros"][-5:]
    ) or "<li>Nenhum erro recente</li>"

    return render_template_string(f"""<!DOCTYPE html><html lang="pt-BR"><head>
    <meta charset="UTF-8"><meta http-equiv="refresh" content="4">
    <title>VoxLumina Monitor</title>
    <style>
      body{{font-family:monospace;background:#0d0d0f;color:#f0f0f5;
           padding:2rem;max-width:720px;margin:0 auto}}
      h1{{color:#e8ff47;font-size:1.5rem;margin-bottom:1.5rem}}
      table{{width:100%;border-collapse:collapse;margin-bottom:1.5rem}}
      td,th{{padding:.5rem .75rem;border:1px solid #2a2a32;text-align:left}}
      th{{background:#141417;color:#6b6b80;font-size:.7rem;letter-spacing:.1em;
          text-transform:uppercase}}
      ul{{padding-left:1.2rem;font-size:.85rem}}
      .badge{{display:inline-block;padding:.2rem .6rem;border-radius:999px;
              font-size:.75rem;background:#1c1c21;border:1px solid #2a2a32}}
      a{{color:#47b3ff}}
    </style></head><body>
    <h1>⚡ VoxLumina Monitor</h1>
    <table>
      <tr><th>Serviço</th><th>Status</th><th>URL</th></tr>
      <tr><td>Kokoro TTS</td><td>{dot_k} {kokoro_status}</td><td>{KOKORO_URL}</td></tr>
      <tr><td>Ollama Vision</td><td>{dot_o} {"ONLINE" if ollama_ok else "OFFLINE"}</td>
          <td>{OLLAMA_URL}</td></tr>
    </table>
    <table>
      <tr><th>Campo</th><th>Valor</th></tr>
      <tr><td>Pipeline</td><td><span class="badge">{_estado["pipeline"]}</span></td></tr>
      <tr><td>Último PDF</td><td>{_estado["ultimo_pdf"]}</td></tr>
      <tr><td>Último JSON</td><td>{_estado["ultimo_json"]}</td></tr>
      <tr><td>Último Áudio</td><td>{_estado["ultimo_audio"]}</td></tr>
      <tr><td>Progresso</td><td>{_estado["progresso"]}</td></tr>
    </table>
    <p style="color:#6b6b80;font-size:.75rem;margin-bottom:.4rem">ERROS RECENTES</p>
    <ul>{erros_html}</ul>
    <p style="color:#2a2a32;font-size:.7rem;margin-top:2rem">
      Auto-refresh 4s &nbsp;·&nbsp; <a href="/status">/status JSON</a>
    </p></body></html>""")


def _run_web_server():
    import logging as _log
    _log.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS DE MENU
# ═══════════════════════════════════════════════════════════════════════════════

def mostrar_cabecalho():
    os.system("cls" if os.name == "nt" else "clear")
    kokoro_status = _narrador.health_check()
    ollama_ok     = _ia.health_check()
    print("=" * 56)
    print("       VOXLUMINA  —  PDF → AUDIOBOOK ACESSÍVEL")
    print("=" * 56)
    print(f"  KOKORO TTS  : {kokoro_status:<10}  ({KOKORO_URL})")
    print(f"  OLLAMA VIS  : {'ONLINE' if ollama_ok else 'OFFLINE':<10}  ({OLLAMA_URL})")
    print(f"  MONITOR     : http://127.0.0.1:{FLASK_PORT}/monitor")
    print("=" * 56)


def selecionar_item(diretorio: Path, extensao: str, titulo: str):
    """
    Lista arquivos e retorna o Path selecionado.
    Retorna "RELOAD" se o usuário digitar 0, ou None em caso de erro.
    """
    arquivos = sorted(diretorio.glob(f"*{extensao}"))
    if not arquivos:
        print(f"\n  ⚠  Nenhum arquivo {extensao} em {diretorio}/")
        return None

    print(f"\n  ── {titulo} ──")
    for i, arq in enumerate(arquivos, 1):
        kb = arq.stat().st_size // 1024
        print(f"  {i}. {arq.name}  ({kb} KB)")

    try:
        escolha = int(input("\n  Nº do arquivo (0 = voltar): ")) - 1
        if escolha == -1:
            return "RELOAD"
        return arquivos[escolha]
    except (ValueError, IndexError):
        print("  Seleção inválida.")
        return None


def selecionar_config_voz() -> dict:
    """
    Carrega mapa de vozes de configs/*.json ou usa os padrões embutidos.
    Mantém compatibilidade com o formato original {id: [nome, speed]}.
    """
    configs = sorted(DIR_CONFIG.glob("*.json"))

    print("\n  ── Configuração de Vozes ──")
    print("  0. Português BR (padrão interno)")
    print("  1. English US   (padrão interno)")
    for i, cfg in enumerate(configs, 2):
        print(f"  {i}. {cfg.name}")

    try:
        escolha = int(input("\n  Escolha: "))
        if escolha == 0:
            return VOZES_PADRAO["ptbr"]
        elif escolha == 1:
            return VOZES_PADRAO["eng"]
        else:
            cfg_path = configs[escolha - 2]
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (ValueError, IndexError, OSError, json.JSONDecodeError):
        log.warning("Seleção inválida, usando ptbr padrão.")
        return VOZES_PADRAO["ptbr"]


def _verificar_ffmpeg() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# FLUXO 1 — PDF → JSON
# ═══════════════════════════════════════════════════════════════════════════════

def pipeline_pdf_para_json() -> Path | None:
    """
    Orquestra: Seleção de PDF → Docling → Ollama Vision → JSON em text/
    Retorna o Path do JSON gerado (para encadear com áudio), ou None.
    """
    mostrar_cabecalho()
    print("\n  ══ FLUXO 1: Processar Novo PDF ══\n")

    # Health check obrigatório antes de processar
    if not _ia.health_check():
        print(f"\n  ✗ Ollama está OFFLINE ({OLLAMA_URL}).")
        print("    Verifique se o container está rodando:")
        print("    docker ps | grep ollama")
        input("\n  Enter para voltar...")
        return None

    # Seleciona PDF
    pdf_path = selecionar_item(DIR_INPUT, ".pdf", "PDFs disponíveis em input/")
    if not pdf_path or pdf_path == "RELOAD":
        return None

    _estado["pipeline"]   = "ingerindo_pdf"
    _estado["ultimo_pdf"] = pdf_path.name
    print(f"\n  Arquivo selecionado: {pdf_path.name}")

    # ── Etapa 1: Docling ─────────────────────────────────────────────────────
    print("\n  [1/3] Convertendo PDF com Docling...")
    print("        (OCR + detecção de layout — pode levar alguns minutos)")
    _estado["progresso"] = "1/3 Docling"

    resultado_docling = _ingestor.processar(pdf_path)
    if resultado_docling is None:
        msg = f"Falha na ingestão Docling: {pdf_path.name}"
        _estado["erros"].append(msg)
        _estado["pipeline"] = "idle"
        print(f"\n  ✗ {msg}")
        input("  Enter para voltar...")
        return None

    markdown = resultado_docling["markdown"]
    imagens  = resultado_docling["imagens"]
    print(f"     ✓ Markdown: {len(markdown):,} chars  |  Imagens encontradas: {len(imagens)}")

    # ── Etapa 2: Audiodescrições via Ollama Vision ────────────────────────────
    print(f"\n  [2/3] Gerando audiodescrições ({len(imagens)} imagem(ns) via Ollama Vision)...")
    _estado["progresso"] = "2/3 Audiodescrições"

    descricoes = []
    for idx, img in enumerate(imagens, 1):
        print(f"     Imagem {idx}/{len(imagens)}  (pág. {img['pagina']})... ", end="", flush=True)
        desc = _ia.descrever_imagem(img["caminho"])
        if desc:
            descricoes.append({"pagina": img["pagina"], "descricao": desc})
            print("✓")
        else:
            print("⚠ sem descrição")

    if not imagens:
        print("     (nenhuma imagem identificada no documento)")

    # ── Etapa 3: Markdown + descrições → Blocos JSON ──────────────────────────
    print("\n  [3/3] Convertendo conteúdo em blocos JSON via Ollama...")
    print("        (geração de roteiro — pode demorar conforme tamanho do doc)")
    _estado["progresso"] = "3/3 Gerando JSON"

    blocos = _ia.markdown_para_blocos(markdown, descricoes)
    if not blocos:
        msg = "Ollama não retornou blocos JSON válidos"
        _estado["erros"].append(msg)
        _estado["pipeline"] = "idle"
        print(f"\n  ✗ {msg}")
        input("  Enter para voltar...")
        return None

    # ── Salva JSON em text/ ───────────────────────────────────────────────────
    nome_json    = pdf_path.stem + ".json"
    caminho_json = DIR_TEXT / nome_json

    with open(caminho_json, "w", encoding="utf-8") as f:
        json.dump(blocos, f, ensure_ascii=False, indent=2)

    _estado["ultimo_json"] = nome_json
    _estado["pipeline"]    = "idle"
    _estado["progresso"]   = "concluído"

    print(f"\n  ✓ JSON salvo: {caminho_json}")
    print(f"    {len(blocos)} blocos de narração gerados")

    # Oferta de encadear com geração de áudio
    resp = input("\n  Gerar áudio agora? (s/N): ").strip().lower()
    return caminho_json if resp == "s" else None


# ═══════════════════════════════════════════════════════════════════════════════
# FLUXO 2 — JSON → ÁUDIO  (preserva lógica original do main.py)
# ═══════════════════════════════════════════════════════════════════════════════

def pipeline_json_para_audio(json_path: Path | None = None):
    """
    Orquestra: Seleção de JSON → KokoroTTS → FFmpeg → MP3
    Aceita json_path diretamente (quando encadeado do Fluxo 1).
    Lógica equivalente à processar_audiobook() do main.py original.
    """
    mostrar_cabecalho()
    print("\n  ══ FLUXO 2: Gerar Áudio de JSON ══\n")

    # Health check Kokoro
    kokoro_status = _narrador.health_check()
    if kokoro_status == "OFFLINE":
        print(f"\n  ✗ Kokoro TTS está OFFLINE ({KOKORO_URL}).")
        print("    Verifique se o container está rodando.")
        input("\n  Enter para voltar...")
        return

    # Seleciona configuração de vozes
    config_vozes = selecionar_config_voz()

    # Seleciona ou recebe JSON
    if json_path is None:
        json_path = selecionar_item(DIR_TEXT, ".json", "Roteiros disponíveis em text/")
    if not json_path or json_path == "RELOAD":
        return

    _estado["pipeline"]    = "gerando_audio"
    _estado["ultimo_json"] = json_path.name
    _estado["progresso"]   = "iniciando TTS"

    print(f"\n  Iniciando conversão: {json_path.name}\n")

    resultado = _narrador.processar_audiobook(
        json_path=json_path,
        output_dir=DIR_AUDIO,
        config_vozes=config_vozes,
    )

    _estado["pipeline"]  = "idle"
    _estado["progresso"] = ""

    if resultado:
        _estado["ultimo_audio"] = resultado.name
        print(f"\n\n  ✓ Sucesso! Salvo em: {resultado}")
    else:
        msg = f"Falha ao gerar áudio de {json_path.name}"
        _estado["erros"].append(msg)
        print(f"\n  ✗ {msg}")

    input("\n  Pressione Enter para voltar ao menu...")


# ═══════════════════════════════════════════════════════════════════════════════
# MENU PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def menu_principal():
    while True:
        mostrar_cabecalho()
        print("""
  O QUE DESEJA FAZER?

  1. Processar novo PDF      (input/ → Docling → Ollama → text/)
  2. Gerar áudio de JSON     (text/ → Kokoro TTS → audio/)
  3. Status detalhado
  0. Sair
""")
        try:
            opcao = input("  Opção: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if opcao == "1":
            json_gerado = pipeline_pdf_para_json()
            # Encadeia direto para áudio se o usuário confirmou
            if json_gerado:
                pipeline_json_para_audio(json_path=json_gerado)

        elif opcao == "2":
            pipeline_json_para_audio()

        elif opcao == "3":
            mostrar_cabecalho()
            kokoro = _narrador.health_check()
            ollama = "ONLINE" if _ia.health_check() else "OFFLINE"
            model_ok = _ia.verificar_modelo_disponivel()
            ffmpeg_ok = _verificar_ffmpeg()

            print(f"""
  ── SERVIÇOS ──────────────────────────────────────────
  Kokoro TTS   : {kokoro}
  Ollama       : {ollama}
  Modelo       : {OLLAMA_MODEL}  {'✓ disponível' if model_ok else '✗ não encontrado (execute: ollama pull ' + OLLAMA_MODEL + ')'}
  FFmpeg       : {'✓ instalado' if ffmpeg_ok else '✗ NÃO ENCONTRADO  →  sudo apt install ffmpeg'}

  ── DIRETÓRIOS ────────────────────────────────────────
  input/       : {len(list(DIR_INPUT.glob('*.pdf')))} PDF(s) aguardando
  text/        : {len(list(DIR_TEXT.glob('*.json')))} JSON(s) de roteiro
  audio/       : {len(list(DIR_AUDIO.glob('*.mp3')))} MP3(s) gerados

  ── ÚLTIMA OPERAÇÃO ───────────────────────────────────
  PDF     : {_estado['ultimo_pdf']}
  JSON    : {_estado['ultimo_json']}
  Áudio   : {_estado['ultimo_audio']}
""")
            if _estado["erros"]:
                print("  ── ERROS RECENTES ──")
                for e in _estado["erros"][-5:]:
                    print(f"  • {e}")
            input("\n  Enter para voltar...")

        elif opcao == "0":
            break


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Inicia servidor web em thread daemon
    web_thread = threading.Thread(target=_run_web_server, daemon=True)
    web_thread.start()
    log.info(f"VoxLumina iniciado · Monitor: http://127.0.0.1:{FLASK_PORT}/monitor")

    try:
        menu_principal()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n  Encerrando VoxLumina...")
        _ingestor.limpar_tudo()
