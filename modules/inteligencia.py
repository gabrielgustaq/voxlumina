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
import os
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
- CORRIJA acentos em notação LaTeX/PDF para UTF-8 real do Português:
    ´a→á  ´e→é  ´i→í  ´o→ó  ´u→ú  ´A→Á  ´E→É  ´I→Í  ´O→Ó  ´U→Ú
    ~a→ã  ~o→õ  ~A→Ã  ~O→Õ  ˆe→ê  ˆo→ô  ˆE→Ê  ˆO→Ô  ¸c→ç  ¸C→Ç  `a→à  ı→i
- Se encontrar palavras quebradas por hífen de fim de linha (ex: "descri-\nção"), junte-as
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
        modelo: str = os.getenv("OLLAMA_MODEL"),
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

    def markdown_para_blocos_em_lotes(
        self,
        markdown: str,
        descricoes_imagens: list[dict],
        max_tentativas: int = 3,
        max_chars_lote: int = 10000,
        contexto: str | None = None,
    ) -> list[dict] | None:
        """
        Converte um Markdown grande em JSON por lotes sequenciais.

        Cada lote é enviado ao Ollama isoladamente; os arrays JSON válidos são
        concatenados no final. Isso evita prompts gigantes e reduz o risco de
        truncamento ou timeout.
        """
        fragmentos = self._dividir_markdown(markdown, max_chars=max_chars_lote)
        if not fragmentos:
            return []

        todos_blocos: list[dict] = []
        total = len(fragmentos)

        log.info(
            f"  Markdown dividido em {total} lote(s) "
            f"(limite {max_chars_lote} chars/lote)"
        )

        for idx, fragmento in enumerate(fragmentos, 1):
            contexto_lote = contexto or "Documento"
            contexto_lote = f"{contexto_lote}; lote {idx}/{total}"

            # Evita repetir audiodescrições quando uma mesma parte precisa ser
            # quebrada em vários lotes por tamanho.
            descricoes_lote = descricoes_imagens if idx == 1 else []

            log.info(
                f"  Convertendo lote {idx}/{total}: "
                f"{len(fragmento)} chars"
            )

            blocos = self.markdown_para_blocos(
                fragmento,
                descricoes_lote,
                max_tentativas=max_tentativas,
                max_chars_markdown=max_chars_lote + 1000,
                contexto=contexto_lote,
            )

            if blocos is None:
                log.error(f"  Falha ao converter lote {idx}/{total} em JSON.")
                return None

            todos_blocos.extend(blocos)

        log.info(f"  Conversão em lotes concluída: {len(todos_blocos)} blocos")
        return todos_blocos

    def markdown_para_blocos(
        self,
        markdown: str,
        descricoes_imagens: list[dict],
        max_tentativas: int = 3,
        max_chars_markdown: int = 12000,
        contexto: str | None = None,
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

        # Limite de segurança. O fluxo principal deve chamar markdown_para_blocos_em_lotes()
        # para evitar truncamento; aqui o corte fica apenas como fallback.
        markdown_truncado = self._truncar_markdown(markdown, max_chars=max_chars_markdown)

        cabecalho_contexto = ""
        if contexto:
            cabecalho_contexto = f"\n\n--- CONTEXTO DESTE TRECHO ---\n{contexto}\n"

        prompt_completo = (
            PROMPT_MARKDOWN_PARA_JSON
            + cabecalho_contexto
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
                return self._normalizar_blocos(dados)
        except (json.JSONDecodeError, ValueError):
            pass

        # Extrai substring entre primeiro [ e último ] (greedy)
        match = re.search(r"\[.*\]", texto_limpo, re.DOTALL)
        if match:
            try:
                dados = json.loads(match.group(0))
                if isinstance(dados, list) and self._validar_blocos(dados):
                    return self._normalizar_blocos(dados)
            except (json.JSONDecodeError, ValueError):
                pass

        # Tenta reparar JSON truncado (adiciona ] no final se faltar)
        try:
            candidato = texto_limpo.strip()
            if candidato.startswith("[") and not candidato.endswith("]"):
                dados = json.loads(candidato + "]")
                if isinstance(dados, list) and self._validar_blocos(dados):
                    log.warning("  JSON estava truncado, foi reparado automaticamente.")
                    return self._normalizar_blocos(dados)
        except (json.JSONDecodeError, ValueError):
            pass

        return None

    def _normalizar_blocos(self, blocos: list, max_chars_texto: int = 450) -> list[dict]:
        """Sanitiza vozes/textos e quebra blocos longos antes do TTS."""
        normalizados: list[dict] = []
        vozes_validas = {"narrator", "female", "male"}

        for bloco in blocos:
            voice = str(bloco.get("voice", "narrator")).lower().strip()
            if voice not in vozes_validas:
                voice = "narrator"

            texto = self._sanitizar_texto_brasil(str(bloco.get("text", "")).strip())
            if not texto:
                continue

            for trecho in self._quebrar_texto_longo(texto, max_chars=max_chars_texto):
                normalizados.append({"voice": voice, "text": trecho})

        return normalizados

    def _quebrar_texto_longo(self, texto: str, max_chars: int = 450) -> list[str]:
        """Quebra texto por frases e, se necessário, por palavras."""
        texto = re.sub(r"\s+", " ", texto).strip()
        if len(texto) <= max_chars:
            return [texto]

        frases = re.split(r"(?<=[.!?;:])\s+", texto)
        partes: list[str] = []
        atual = ""

        def quebrar_por_palavras(trecho: str) -> list[str]:
            saida: list[str] = []
            buf = ""
            for palavra in trecho.split():
                candidato = f"{buf} {palavra}".strip()
                if len(candidato) <= max_chars:
                    buf = candidato
                else:
                    if buf:
                        saida.append(buf)
                    buf = palavra
            if buf:
                saida.append(buf)
            return saida

        for frase in frases:
            if not frase:
                continue

            if len(frase) > max_chars:
                if atual:
                    partes.append(atual)
                    atual = ""
                partes.extend(quebrar_por_palavras(frase))
                continue

            candidato = f"{atual} {frase}".strip()
            if len(candidato) <= max_chars:
                atual = candidato
            else:
                if atual:
                    partes.append(atual)
                atual = frase

        if atual:
            partes.append(atual)

        return [p.strip() for p in partes if p.strip()]

    def _dividir_markdown(self, texto: str, max_chars: int = 10000) -> list[str]:
        """Divide Markdown em lotes preservando parágrafos quando possível."""
        texto = (texto or "").strip()
        if not texto:
            return []
        if len(texto) <= max_chars:
            return [texto]

        paragrafos = re.split(r"\n\s*\n", texto)
        lotes: list[str] = []
        atual: list[str] = []
        tamanho_atual = 0

        def flush():
            nonlocal atual, tamanho_atual
            if atual:
                lotes.append("\n\n".join(atual).strip())
                atual = []
                tamanho_atual = 0

        for paragrafo in paragrafos:
            paragrafo = paragrafo.strip()
            if not paragrafo:
                continue

            if len(paragrafo) > max_chars:
                flush()
                for inicio in range(0, len(paragrafo), max_chars):
                    lotes.append(paragrafo[inicio:inicio + max_chars].strip())
                continue

            acrescimo = len(paragrafo) + (2 if atual else 0)
            if atual and tamanho_atual + acrescimo > max_chars:
                flush()

            atual.append(paragrafo)
            tamanho_atual += acrescimo

        flush()
        return lotes

    def _sanitizar_texto_brasil(self, texto: str) -> str:
        """
        Converte escapes de acento comuns em PDFs/LaTeX para UTF-8 brasileiro.
        Aplicado em cada bloco de texto antes de retornar o JSON final,
        como camada de segurança caso a LLM não corrija sozinha.
        """
        substituicoes = {
            "´a": "á", "´e": "é", "´i": "í", "´o": "ó", "´u": "ú",
            "´A": "Á", "´E": "É", "´I": "Í", "´O": "Ó", "´U": "Ú",
            "~a": "ã",  "~o": "õ",  "~A": "Ã",  "~O": "Õ",
            "ˆe": "ê",  "ˆo": "ô",  "ˆa": "â",  "ˆE": "Ê",  "ˆO": "Ô",  "ˆA": "Â",
            "¸c": "ç",  "¸C": "Ç",
            "`a": "à",  "`A": "À",
            "ı": "i",
        }
        for erro, correto in substituicoes.items():
            texto = texto.replace(erro, correto)

        # Junta palavras hifenizadas no fim de linha: "descri-\nção" → "descrição"
        texto = re.sub(r"-\n\s*", "", texto)

        # Remove espaços espúrios antes de caracteres acentuados (artefato de OCR)
        texto = re.sub(r" ([áéíóúâêôãõçàÁÉÍÓÚÂÊÔÃÕÇÀ])", r"\1", texto)

        return texto

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
            voice = str(bloco.get("voice", "narrator")).lower().strip()
            bloco["voice"] = voice if voice in vozes_validas else "narrator"
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
