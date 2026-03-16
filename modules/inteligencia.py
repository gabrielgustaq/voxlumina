"""
modules/inteligencia.py
─────────────────────────────────────────────────────────────────────────────
Responsabilidade: Cliente HTTP para Ollama Vision.
                  Tarefa A → Audiodescrição de imagem (base64 → texto)
                  Tarefa B → Markdown + descrições → blocos JSON {voice, text}

Comunicação: exclusivamente via requests HTTP (sem SDK Ollama).
Endpoint:    http://<IP_OLLAMA>:11434/api/generate
─────────────────────────────────────────────────────────────────────────────
"""

import base64
import json
import logging
import re
import time
from pathlib import Path

import requests

log = logging.getLogger("VoxLumina.Inteligencia")

# ─── Prompts do sistema ────────────────────────────────────────────────────────

PROMPT_AUDIODESCRICAO = """\
Você é um especialista em acessibilidade para documentos acadêmicos.
Analise a imagem fornecida e crie uma audiodescrição técnica e objetiva em português.

Sua resposta DEVE:
- Descrever o tipo de elemento visual (gráfico, diagrama, equação, tabela, foto)
- Explicar os dados ou conceitos apresentados
- Mencionar legendas, eixos, unidades de medida quando presentes
- Ser autocontida (o ouvinte não verá a imagem)
- Ter entre 2 e 6 frases
- NÃO usar frases como "a imagem mostra" ou "na figura vemos"

Responda SOMENTE com a audiodescrição, sem explicações adicionais."""

PROMPT_MARKDOWN_PARA_JSON = """\
Você é um conversor especializado em transformar conteúdo acadêmico em roteiros de audiobook acessíveis.

Recebe:
1. Um documento acadêmico em Markdown
2. Uma lista de audiodescrições de imagens no formato: [Página X] descrição...

Sua tarefa é converter TUDO em um array JSON com blocos de narração.

REGRAS OBRIGATÓRIAS:
- Retorne SOMENTE o array JSON válido, sem markdown (sem ```json), sem explicações
- Cada bloco tem exatamente dois campos: "voice" e "text"
- Valores de "voice" permitidos: "narrator", "female", "male"
- Use "narrator" para: títulos, subtítulos, transições, audiodescrições de imagens
- Use "female" ou "male" para: citações diretas, exemplos, definições específicas
- Quebre textos longos (>400 chars) em múltiplos blocos do mesmo voice
- Substitua cada imagem pelo bloco de audiodescrição correspondente
- Ignore referências bibliográficas cruas (ex: [1], [2]) exceto quando parte de frase
- Preserve a estrutura lógica do documento (introdução → desenvolvimento → conclusão)
- NÃO inclua URLs, DOIs, ou metadados de formatação LaTeX

FORMATO DE SAÍDA (exemplo):
[
  {"voice": "narrator", "text": "Capítulo 1. Introdução ao tema."},
  {"voice": "narrator", "text": "Este estudo investiga os efeitos do clima..."},
  {"voice": "narrator", "text": "Descrição da Figura 1: Gráfico de barras mostrando..."},
  {"voice": "female", "text": "Como afirma Silva, 2023, os resultados indicam..."}
]

Agora converta o conteúdo abaixo:
"""


class InteligenciaOllama:
    """
    Cliente HTTP para o servidor Ollama Vision.
    Todas as chamadas usam requests diretamente (sem biblioteca ollama).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        modelo: str = "llama3.2-vision",
        timeout_geracao: int = 900,   # padrão 15 min; sobreposto por OLLAMA_TIMEOUT no main.py
        timeout_health: int = 5,
    ):
        self.base_url = base_url.rstrip("/")
        self.modelo = modelo
        self.timeout_geracao = timeout_geracao
        self.timeout_health = timeout_health
        self._url_generate = f"{self.base_url}/api/generate"
        self._url_health   = f"{self.base_url}/api/tags"  # endpoint estável para health

    # ─── Health Check ─────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Retorna True se o servidor Ollama estiver respondendo."""
        try:
            r = requests.get(self._url_health, timeout=self.timeout_health)
            return r.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def verificar_modelo_disponivel(self) -> bool:
        """Verifica se o modelo configurado está puxado no Ollama."""
        try:
            r = requests.get(self._url_health, timeout=self.timeout_health)
            if r.status_code != 200:
                return False
            dados = r.json()
            modelos = [m.get("name", "") for m in dados.get("models", [])]
            # Verifica se o modelo (sem tag) existe na lista
            modelo_base = self.modelo.split(":")[0]
            return any(modelo_base in m for m in modelos)
        except (requests.exceptions.RequestException, ValueError):
            return False

    # ─── Tarefa A: Audiodescrição de Imagem ───────────────────────────────────

    def descrever_imagem(self, caminho_imagem: Path) -> str | None:
        """
        Envia uma imagem em base64 ao Ollama Vision e retorna a audiodescrição.

        Args:
            caminho_imagem: Path para o arquivo PNG/JPG do recorte

        Returns:
            str com a audiodescrição, ou None em caso de falha
        """
        if not caminho_imagem.exists():
            log.error(f"Imagem não encontrada: {caminho_imagem}")
            return None

        # Codifica imagem em base64
        try:
            with open(caminho_imagem, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")
        except OSError as e:
            log.error(f"Erro ao ler imagem {caminho_imagem}: {e}")
            return None

        payload = {
            "model": self.modelo,
            "prompt": PROMPT_AUDIODESCRICAO,
            "images": [img_b64],
            "stream": False,
            "options": {
                "temperature": 0.3,    # Baixo para descrições factuais
                "num_predict": 512,
                "num_ctx": 4096,
            },
        }

        log.debug(f"  Enviando imagem para Ollama: {caminho_imagem.name} "
                  f"({len(img_b64) // 1024}KB base64)")

        resposta = self._chamar_api(payload, descricao=f"audiodesc:{caminho_imagem.name}")
        if resposta:
            return resposta.strip()
        return None

    # ─── Tarefa B: Markdown → Blocos JSON ────────────────────────────────────

    def markdown_para_blocos(
        self,
        markdown: str,
        descricoes_imagens: list[dict],
        max_tentativas: int = 3,
    ) -> list[dict] | None:
        """
        Converte Markdown + audiodescrições em blocos JSON {voice, text}.

        Args:
            markdown:           Texto Markdown do documento
            descricoes_imagens: Lista de {"pagina": int, "descricao": str}
            max_tentativas:     Retries em caso de JSON inválido

        Returns:
            Lista de dicts [{voice: str, text: str}, ...] ou None
        """
        # Monta seção de audiodescrições para o prompt
        secao_descricoes = ""
        if descricoes_imagens:
            linhas = ["\n\n--- AUDIODESCRIÇÕES DE IMAGENS ---"]
            for d in descricoes_imagens:
                linhas.append(f"[Página {d['pagina']}] {d['descricao']}")
            secao_descricoes = "\n".join(linhas)

        # Limita tamanho para não estourar contexto (llama3.2-vision: ~8k tokens)
        markdown_truncado = self._truncar_markdown(markdown, max_chars=12000)

        prompt_completo = (
            PROMPT_MARKDOWN_PARA_JSON
            + markdown_truncado
            + secao_descricoes
        )

        payload = {
            "model": self.modelo,
            "prompt": prompt_completo,
            "stream": False,
            "options": {
                "temperature": 0.1,     # Quase determinístico para JSON
                "num_predict": 8192,
                "num_ctx": 16384,
            },
        }

        for tentativa in range(1, max_tentativas + 1):
            log.info(f"  Convertendo Markdown → JSON (tentativa {tentativa}/{max_tentativas})")

            resposta_bruta = self._chamar_api(
                payload,
                descricao=f"md→json tentativa {tentativa}"
            )
            if resposta_bruta is None:
                log.warning(f"  Sem resposta do Ollama na tentativa {tentativa}")
                if tentativa < max_tentativas:
                    time.sleep(5)
                continue

            blocos = self._extrair_json_da_resposta(resposta_bruta)
            if blocos is not None:
                log.info(f"  JSON válido extraído: {len(blocos)} blocos")
                return blocos

            log.warning(f"  Resposta não produziu JSON válido na tentativa {tentativa}")
            if tentativa < max_tentativas:
                # Adiciona instrução de correção no próximo payload
                payload["prompt"] = (
                    prompt_completo
                    + "\n\nATENÇÃO: Retorne SOMENTE o array JSON, começando com [ e terminando com ]."
                )
                time.sleep(3)

        log.error("Todas as tentativas de conversão Markdown→JSON falharam.")
        return None

    # ─── Chamada HTTP ──────────────────────────────────────────────────────────

    def _chamar_api(self, payload: dict, descricao: str = "") -> str | None:
        """
        Executa POST para /api/generate e retorna o campo 'response'.
        Trata timeouts, erros HTTP e JSON malformado.
        """
        try:
            log.debug(f"  POST {self._url_generate} [{descricao}]")
            r = requests.post(
                self._url_generate,
                json=payload,
                timeout=self.timeout_geracao,
            )
            r.raise_for_status()

        except requests.exceptions.ConnectionError:
            log.error(f"Ollama inacessível em {self.base_url}. Verifique se o container está rodando.")
            return None

        except requests.exceptions.Timeout:
            log.error(f"Timeout ({self.timeout_geracao}s) aguardando Ollama [{descricao}].")
            return None

        except requests.exceptions.HTTPError as e:
            log.error(f"Ollama retornou HTTP {r.status_code}: {r.text[:200]}")
            return None

        # Decodifica resposta
        try:
            dados = r.json()
        except ValueError:
            log.error(f"Resposta do Ollama não é JSON válido: {r.text[:200]}")
            return None

        # Verifica se houve erro reportado pelo Ollama
        if "error" in dados:
            log.error(f"Ollama reportou erro: {dados['error']}")
            return None

        resposta = dados.get("response", "").strip()
        if not resposta:
            log.warning("Ollama retornou 'response' vazio.")
            return None

        return resposta

    # ─── Utilitários ──────────────────────────────────────────────────────────

    def _extrair_json_da_resposta(self, texto: str) -> list | None:
        """
        Tenta extrair um array JSON válido de uma string de texto livre.
        Lida com markdown code fences, texto antes/depois do JSON, etc.
        """
        # Remove blocos markdown ```json ... ```
        texto_limpo = re.sub(r"```(?:json)?\s*", "", texto)
        texto_limpo = re.sub(r"```\s*$", "", texto_limpo, flags=re.MULTILINE)

        # Tenta parsear diretamente
        try:
            dados = json.loads(texto_limpo.strip())
            if isinstance(dados, list) and self._validar_blocos(dados):
                return dados
        except (json.JSONDecodeError, ValueError):
            pass

        # Extrai substring entre primeiro [ e último ] (greedy)
        match = re.search(r"\[.*\]", texto_limpo, re.DOTALL)
        if match:
            try:
                dados = json.loads(match.group(0))
                if isinstance(dados, list) and self._validar_blocos(dados):
                    return dados
            except (json.JSONDecodeError, ValueError):
                pass

        # Tenta reparar JSON truncado (adiciona ] no final se faltar)
        try:
            candidato = texto_limpo.strip()
            if candidato.startswith("[") and not candidato.endswith("]"):
                dados = json.loads(candidato + "]")
                if isinstance(dados, list) and self._validar_blocos(dados):
                    log.warning("  JSON estava truncado, foi reparado automaticamente.")
                    return dados
        except (json.JSONDecodeError, ValueError):
            pass

        return None

    def _validar_blocos(self, blocos: list) -> bool:
        """Valida que cada bloco tem 'voice' e 'text' não-vazios."""
        if not blocos:
            return False
        vozes_validas = {"narrator", "female", "male"}
        for i, bloco in enumerate(blocos):
            if not isinstance(bloco, dict):
                log.debug(f"  Bloco {i} não é dict: {bloco}")
                return False
            if "text" not in bloco or not bloco["text"].strip():
                log.debug(f"  Bloco {i} sem campo 'text'")
                return False
            # Normaliza voice para valor válido (tolerante)
            voice = bloco.get("voice", "narrator").lower()
            if voice not in vozes_validas:
                bloco["voice"] = "narrator"
        return True

    def _truncar_markdown(self, texto: str, max_chars: int) -> str:
        """
        Trunca Markdown respeitando limite de contexto.
        Indica truncamento com nota informativa.
        """
        if len(texto) <= max_chars:
            return texto

        truncado = texto[:max_chars]
        # Corta no último parágrafo completo
        ultimo_para = truncado.rfind("\n\n")
        if ultimo_para > max_chars * 0.8:
            truncado = truncado[:ultimo_para]

        nota = (
            f"\n\n[NOTA: Documento truncado em {len(truncado)}/{len(texto)} caracteres "
            f"para caber no contexto. Processe o restante separadamente.]"
        )
        log.warning(
            f"  Markdown truncado: {len(texto)} → {len(truncado)} chars. "
            f"Considere dividir o PDF em partes."
        )
        return truncado + nota
