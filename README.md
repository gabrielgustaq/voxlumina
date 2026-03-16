# VoxLumina 🎙️👁️

**VoxLumina** é uma ferramenta de tecnologia assistiva desenvolvida para converter documentos complexos — literários e acadêmicos — em audiobooks dinâmicos e imersivos.

O diferencial do projeto reside no uso de **Modelos de Linguagem de Grande Escala (LLMs) Multimodais** para a audiodescrição de elementos visuais e na síntese de voz de alta fidelidade com alternância de personas, garantindo que o conteúdo acadêmico seja tão acessível e envolvente quanto a literatura narrativa.

---

## 🚀 Funcionalidades

- **Extração Semântica** — Conversão de PDFs complexos para Markdown/JSON preservando a hierarquia do documento via **Docling (IBM)**.
- **Audiodescrição Assistida** — Reconhecimento e descrição de gráficos, tabelas e figuras utilizando **Llama 3.2 Vision**.
- **Narração Dinâmica** — Geração de áudio com múltiplas vozes e entonações naturais através do **KokoroTTS**.
- **Otimização de Hardware** — Pipeline desenhado para rodar localmente, utilizando um único modelo multimodal para evitar falhas de memória (*context switching*).

---

## 🛠️ Arquitetura do Sistema

```
PDF ──► [Ingestor / Docling]  ──► Markdown + Recortes de Imagem
                                          │
                                          ▼
                         [Inteligência / Ollama Vision]
                         ┌──────────────────────────────┐
                         │  Tarefa A: Audiodescrições    │
                         │  Tarefa B: Markdown → JSON    │
                         └──────────────────────────────┘
                                          │
                                          ▼
                          [Narrador / KokoroTTS + FFmpeg]
                                          │
                                          ▼
                                    Audiobook .mp3
```

| Camada | Tecnologia | Responsabilidade |
|---|---|---|
| **Ingestão** | Docling (IBM) | Parsing de PDF, layout, extração de imagens |
| **Inteligência** | Ollama + Llama 3.2 Vision | Audiodescrições e geração de roteiro JSON |
| **Síntese** | KokoroTTS + FFmpeg | TTS multi-voz e concatenação de segmentos |
| **Monitoramento** | Flask | Dashboard web em `http://localhost:8080` |

---

## 📦 Instalação e Configuração

### Pré-requisitos

- **Python 3.10+**
- **FFmpeg** instalado no sistema:
  ```bash
  sudo apt install ffmpeg
  ```
- **Ollama** rodando em container ou nativo — [ollama.com](https://ollama.com/)
- **KokoroTTS Server** via Docker ou API local — [remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI)

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/voxlumina.git
cd voxlumina
```

### 2. Configure o ambiente virtual

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Crie o arquivo `.env`

Crie um arquivo `.env` na raiz do projeto com as URLs dos seus serviços:

```env
OLLAMA_URL=http://seu-ip-ollama:11434
OLLAMA_MODEL=llama3.2-vision
OLLAMA_TIMEOUT=900
KOKORO_URL=http://127.0.0.1:8880
FLASK_PORT=8080
```

### 4. Puxe o modelo no Ollama

```bash
ollama pull llama3.2-vision
```

---

## ⚡ Como Executar

O projeto inclui um script `run.sh` que carrega as variáveis de ambiente e inicia a aplicação de forma segura — compatível com distros imutáveis.

```bash
# Dê permissão de execução (apenas na primeira vez)
chmod +x run.sh

# Inicie o sistema
./run.sh
```

Ou diretamente via Python (se o venv já estiver ativo):

```bash
python main.py
```

Ao iniciar, dois modos ficam disponíveis simultaneamente:

- **Interface CLI** — menu interativo no terminal para processar PDFs e gerar áudio.
- **Interface Web** — acesse `http://localhost:8080/monitor` para monitoramento em tempo real.

---

## 📂 Estrutura de Pastas

```
voxlumina/
├── main.py               # Orquestrador principal + servidor Flask
├── run.sh                # Script de inicialização com carregamento do .env
├── requirements.txt      # Dependências Python
├── .env                  # Configuração local (não versionado)
│
├── modules/
│   ├── ingestor.py       # Camada de ingestão via Docling
│   ├── inteligencia.py   # Cliente HTTP Ollama Vision
│   └── narrador.py       # KokoroTTS + concatenação FFmpeg
│
├── input/                # ► Coloque aqui os PDFs para processamento
├── text/                 # JSONs de roteiro gerados pela IA
├── audio/                # Audiobooks .mp3 finais
└── configs/              # Perfis de voz customizados por idioma (opcional)
```

---

## 🔄 Fluxo de Trabalho

### Fluxo 1 — PDF novo (completo)

```
input/documento.pdf
  → Docling extrai Markdown + recortes de imagem
  → Ollama gera audiodescrições (Tarefa A)
  → Ollama converte tudo em blocos JSON {voice, text} (Tarefa B)
  → JSON salvo em text/documento.json
  → (opcional) encadeia direto para geração de áudio
```

### Fluxo 2 — JSON existente

```
text/roteiro.json
  → Seleciona configuração de vozes
  → KokoroTTS gera segmentos .mp3 individuais
  → FFmpeg concatena em audio/final_roteiro.mp3
```

### Formato do JSON de roteiro

```json
[
  {"voice": "narrator", "text": "Capítulo 1. Introdução ao tema."},
  {"voice": "narrator", "text": "Descrição da Figura 1: gráfico de barras mostrando..."},
  {"voice": "female",   "text": "Como afirma Silva (2023), os resultados indicam..."},
  {"voice": "male",     "text": "A hipótese central do estudo é verificada quando..."}
]
```

Valores de `voice` disponíveis: `narrator`, `female`, `male`.

---

## ⚙️ Configuração de Vozes Customizada

Crie arquivos `.json` em `configs/` para mapear IDs de voz para modelos e velocidades do Kokoro:

```json
{
  "narrator": ["pm_santa", 0.95],
  "female":   ["bf_emma",  1.05],
  "male":     ["pm_santa", 1.0]
}
```

Esses arquivos ficam disponíveis como opção no menu ao executar o Fluxo 2.

---

## 🌐 API de Monitoramento

O Flask expõe dois endpoints além da interface web:

| Endpoint | Descrição |
|---|---|
| `GET /` | Interface web KokoroStudio (audiobook_ui.html) |
| `GET /monitor` | Dashboard de status (auto-refresh 4s) |
| `GET /status` | Status completo em JSON |

---

## 🛠️ Requisitos (`requirements.txt`)

```
requests>=2.31.0
flask>=3.0.0
docling>=2.0.0
Pillow>=10.0.0
```

---

## 📋 Variáveis de Ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `OLLAMA_URL` | `http://127.0.0.1:11434` | URL do servidor Ollama |
| `OLLAMA_MODEL` | `llama3.2-vision` | Modelo multimodal a usar |
| `OLLAMA_TIMEOUT` | `900` | Timeout em segundos para geração (15 min) |
| `KOKORO_URL` | `http://127.0.0.1:8880` | URL do servidor KokoroTTS |
| `FLASK_PORT` | `8080` | Porta do dashboard web |

---

## ⚠️ Observações de Hardware

O VoxLumina foi otimizado para rodar localmente em máquinas com **32GB de RAM**. O pipeline usa um único modelo multimodal (Llama 3.2 Vision) para todas as tarefas de IA, evitando *context switching* e falhas de memória por múltiplos modelos simultâneos. O processamento de PDFs longos pode ser demorado — um documento de 20 páginas com imagens leva tipicamente 5–15 minutos na etapa de IA.

---

*Desenvolvido por Gabriel — Projeto de TCC em Tecnologia Assistiva.*
