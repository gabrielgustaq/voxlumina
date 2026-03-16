# VoxLumina 🎙️👁️

**VoxLumina** é uma ferramenta de tecnologia assistiva desenvolvida para converter documentos complexos (literários e acadêmicos) em audiobooks dinâmicos e imersivos. 

O diferencial do projeto reside no uso de **Modelos de Linguagem de Grande Escala (LLMs) Multimodais** para a audiodescrição de elementos visuais e na síntese de voz de alta fidelidade com alternância de personas, garantindo que o conteúdo acadêmico seja tão acessível e envolvente quanto a literatura narrativa.

## 🚀 Funcionalidades

- **Extração Semântica:** Conversão de PDFs complexos para Markdown/JSON preservando a hierarquia do documento via **Docling (IBM)**.
- **Audiodescrição Assistida:** Reconhecimento e descrição de gráficos, tabelas e figuras utilizando **Llama 3.2 Vision**.
- **Narração Dinâmica:** Geração de áudio com múltiplas vozes e entonações naturais através do **KokoroTTS**.
- **Otimização de Hardware:** Pipeline desenhado para rodar localmente, utilizando um único modelo multimodal para evitar falhas de memória (context switching).

## 🛠️ Arquitetura do Sistema

1. **Camada de Ingestão:** O PDF é processado pelo Docling, que identifica blocos de texto, tabelas e imagens.
2. **Camada de Inteligência:** O texto e os recortes de imagem são enviados ao **Ollama (Llama 3.2 Vision)**. Ele organiza o conteúdo em blocos semânticos e gera descrições detalhadas para as imagens.
3. **Camada de Síntese:** Um script Python processa o JSON resultante e utiliza o **KokoroTTS** para gerar os arquivos de áudio finais.

## 📦 Instalação e Configuração

### Pré-requisitos
- Python 3.10+
- [Ollama](https://ollama.com/) instalado e rodando.
- Modelo Llama 3.2 Vision baixado:
  ```bash
  ollama pull llama3.2-vision
  ```
### Instalação do Projeto
1. Clone o repositorio:

    ```bash
    git clone ...
    cd voxlumina
    ```
2. Crie um ambiente virtual:

    ```bash
    python -m venv venv
    source venv/bin/activate  
    # No Windows: venv\Scripts\activate
    ```
3. Instale as dependencias:

    ```bash
    pip install -r requirements.txt
    ```
