"""
modules/narrador.py
─────────────────────────────────────────────────────────────────────────────
Responsabilidade: Consumir blocos JSON {voice, text} e gerar audiobook MP3.
                  Preserva INTEGRALMENTE a lógica de concatenação FFmpeg
                  do main.py original, com tratamento de erros aprimorado.

Comunicação: exclusivamente via requests HTTP para Kokoro TTS.
Endpoint:    http://<IP_KOKORO>:8880/v1/audio/speech
─────────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import requests

log = logging.getLogger("VoxLumina.Narrador")

# ─── Mapa de vozes padrão (equivalente ao VOICE_CONFIGS da UI web) ────────────

VOZES_PADRAO = {
    "ptbr": {
        "narrator": ["pm_alex", 0.95],
        "female":   ["pf_dora",  1.05],
        "male":     ["pm_alex", 1.0],
    },
    "eng": {
        "narrator": ["af_bella", 0.95],
        "female":   ["af_emma",  1.05],
        "male":     ["am_adam",  1.0],
    },
}


class NarradorKokoro:
    """
    Gera áudio via Kokoro TTS e concatena segmentos com FFmpeg.
    Todo acesso ao servidor Kokoro é feito via requests HTTP.
    """

    def __init__(
        self,
        kokoro_url: str = "http://127.0.0.1:8880",
        timeout_tts: int = 120,
        timeout_health: int = 3,
    ):
        self.kokoro_url  = kokoro_url.rstrip("/")
        self._url_speech = f"{self.kokoro_url}/v1/audio/speech"
        self._url_health = f"{self.kokoro_url}/health"
        self.timeout_tts    = timeout_tts
        self.timeout_health = timeout_health

    # ─── Health Check ─────────────────────────────────────────────────────────

    def health_check(self) -> str:
        """
        Replica exata do check_kokoro_status() do main.py original.
        Retorna: "ONLINE" | "REACHABLE" | "OFFLINE"
        """
        try:
            r = requests.get(self._url_health, timeout=self.timeout_health)
            return "ONLINE" if r.status_code == 200 else "ERROR"
        except requests.exceptions.RequestException:
            pass

        # Fallback: tenta ping na raiz
        try:
            requests.get(self.kokoro_url, timeout=1)
            return "REACHABLE"
        except requests.exceptions.RequestException:
            return "OFFLINE"

    # ─── API Pública Principal ────────────────────────────────────────────────

    def processar_audiobook(
        self,
        json_path: Path,
        output_dir: Path,
        config_vozes: dict | None = None,
        idioma: str = "ptbr",
    ) -> Path | None:
        """
        Pipeline completa: JSON → segmentos MP3 → concatenação FFmpeg → arquivo final.

        Args:
            json_path:    Caminho para o arquivo .json com os blocos
            output_dir:   Diretório onde o MP3 final será salvo
            config_vozes: Mapa customizado {id_voz: [nome_kokoro, speed]}
                          Se None, usa VOZES_PADRAO[idioma]
            idioma:       "ptbr" ou "eng" (usado quando config_vozes=None)

        Returns:
            Path do arquivo MP3 gerado, ou None em caso de falha total.
        """
        if not json_path.exists():
            log.error(f"JSON não encontrado: {json_path}")
            return None

        # Carrega blocos
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                blocos = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.error(f"Erro ao carregar JSON {json_path}: {e}")
            return None

        if not blocos:
            log.error("JSON está vazio ou sem blocos.")
            return None

        # Define mapa de vozes
        misturas = config_vozes or VOZES_PADRAO.get(idioma, VOZES_PADRAO["ptbr"])

        # Prepara diretórios
        output_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = output_dir.parent / "temp_segments"
        temp_dir.mkdir(parents=True, exist_ok=True)

        arquivo_saida = output_dir / f"final_{json_path.stem}.mp3"

        # ── Geração dos segmentos ─────────────────────────────────────────────
        lista_segmentos = self._gerar_segmentos(blocos, misturas, temp_dir)

        if not lista_segmentos:
            log.error("Nenhum segmento de áudio foi gerado. Verifique Kokoro TTS.")
            self._limpar_temp(temp_dir, [])
            return None

        # ── Concatenação FFmpeg ───────────────────────────────────────────────
        log.info(f"\nConcatenando {len(lista_segmentos)}/{len(blocos)} segmentos com FFmpeg...")
        sucesso = self._concatenar_ffmpeg(lista_segmentos, arquivo_saida)

        # Limpeza de temporários
        self._limpar_temp(temp_dir, lista_segmentos)

        if sucesso:
            tamanho_mb = arquivo_saida.stat().st_size / (1024 * 1024)
            log.info(f"Audiobook gerado: {arquivo_saida} ({tamanho_mb:.1f} MB)")
            return arquivo_saida
        else:
            log.error("FFmpeg falhou na concatenação.")
            return None

    # ─── Geração de Segmentos ─────────────────────────────────────────────────

    def _gerar_segmentos(
        self,
        blocos: list,
        misturas: dict,
        temp_dir: Path,
    ) -> list[Path]:
        """
        Envia cada bloco ao Kokoro TTS e salva como MP3 individual.
        Preserva a lógica original de mapeamento de vozes.
        """
        lista_segmentos = []
        total = len(blocos)

        log.info(f"Iniciando TTS: {total} blocos")

        for i, bloco in enumerate(blocos):
            nome_segmento = temp_dir / f"seg_{i:03d}.mp3"
            id_voz_ia = bloco.get("voice", "narrator")
            voz_info  = misturas.get(id_voz_ia, [id_voz_ia, 1.0])

            # Pula blocos com texto vazio
            texto = bloco.get("text", "").strip()
            if not texto:
                log.debug(f"  Bloco {i+1} ignorado (texto vazio)")
                continue

            payload = {
                "model": "kokoro",
                "input": texto,
                "voice": voz_info[0],
                "speed": voz_info[1],
                "response_format": "mp3",
            }

            sucesso = self._chamar_kokoro_com_retry(payload, nome_segmento, i + 1, total)
            if sucesso:
                lista_segmentos.append(nome_segmento)

        log.info(f"Segmentos gerados: {len(lista_segmentos)}/{total}")
        return lista_segmentos

    def _chamar_kokoro_com_retry(
        self,
        payload: dict,
        destino: Path,
        num_bloco: int,
        total: int,
        max_tentativas: int = 3,
        delay_retry: float = 5.0,
    ) -> bool:
        """
        Chama Kokoro TTS com retry automático.
        Retorna True se o segmento foi salvo com sucesso.
        """
        for tentativa in range(1, max_tentativas + 1):
            try:
                r = requests.post(
                    self._url_speech,
                    json=payload,
                    timeout=self.timeout_tts,
                )

                if r.status_code == 200:
                    with open(destino, "wb") as f_seg:
                        f_seg.write(r.content)

                    # Verifica se o arquivo tem conteúdo real
                    if destino.stat().st_size < 100:
                        log.warning(f"  [{num_bloco}/{total}] Arquivo muito pequeno, possível erro")
                        destino.unlink(missing_ok=True)
                        continue

                    print(f" -> [{num_bloco}/{total}] OK", end="\r", flush=True)
                    return True

                else:
                    log.warning(
                        f"\n  [{num_bloco}/{total}] HTTP {r.status_code} "
                        f"(tentativa {tentativa}/{max_tentativas}): {r.text[:100]}"
                    )

            except requests.exceptions.Timeout:
                log.warning(
                    f"\n  [{num_bloco}/{total}] Timeout TTS "
                    f"(tentativa {tentativa}/{max_tentativas})"
                )
            except requests.exceptions.ConnectionError:
                log.error(f"\n  [{num_bloco}/{total}] Kokoro inacessível.")
                return False  # Sem retry em caso de conexão recusada
            except OSError as e:
                log.error(f"\n  [{num_bloco}/{total}] Erro ao salvar segmento: {e}")
                return False

            if tentativa < max_tentativas:
                time.sleep(delay_retry)

        log.error(f"\n  [{num_bloco}/{total}] Falhou após {max_tentativas} tentativas.")
        return False

    # ─── Concatenação FFmpeg ──────────────────────────────────────────────────

    def _concatenar_ffmpeg(self, segmentos: list[Path], saida: Path) -> bool:
        """
        Concatena segmentos MP3 usando FFmpeg concat demuxer.
        Lógica IDÊNTICA ao main.py original, com tratamento de erros adicional.
        """
        if not self._ffmpeg_disponivel():
            log.error("FFmpeg não encontrado. Instale com: sudo apt install ffmpeg")
            return False

        concat_list_path = saida.parent / "lista_ffmpeg.txt"

        try:
            # Escreve lista de arquivos absolutos (obrigatório para concat demuxer)
            with open(concat_list_path, "w", encoding="utf-8") as f_list:
                for seg in segmentos:
                    # Escapa aspas simples no caminho (raro, mas seguro)
                    caminho_abs = str(seg.resolve()).replace("'", "'\\''")
                    f_list.write(f"file '{caminho_abs}'\n")

            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list_path),
                "-c", "copy",
                str(saida),
            ]

            log.debug(f"  FFmpeg: {' '.join(cmd)}")
            resultado = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=300,
            )

            if resultado.returncode != 0:
                erro_ffmpeg = resultado.stderr.decode("utf-8", errors="replace")[-500:]
                log.error(f"FFmpeg erro (código {resultado.returncode}):\n{erro_ffmpeg}")
                return False

            return saida.exists() and saida.stat().st_size > 0

        except subprocess.TimeoutExpired:
            log.error("FFmpeg excedeu timeout de 300s.")
            return False
        except FileNotFoundError:
            log.error("Comando 'ffmpeg' não encontrado no PATH.")
            return False
        except OSError as e:
            log.error(f"Erro ao criar lista FFmpeg: {e}")
            return False
        finally:
            # Limpeza garantida da lista temporária
            concat_list_path.unlink(missing_ok=True)

    # ─── Utilitários ──────────────────────────────────────────────────────────

    def _ffmpeg_disponivel(self) -> bool:
        """Verifica se o FFmpeg está instalado e acessível."""
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _limpar_temp(self, temp_dir: Path, segmentos: list[Path]):
        """Remove segmentos temporários e o diretório temp (se vazio)."""
        removidos = 0
        for seg in segmentos:
            try:
                seg.unlink(missing_ok=True)
                removidos += 1
            except OSError:
                pass

        # Remove diretório se vazio
        try:
            if temp_dir.exists() and not any(temp_dir.iterdir()):
                temp_dir.rmdir()
        except OSError:
            pass

        if removidos:
            log.debug(f"  Removidos {removidos} segmentos temporários.")

    def listar_vozes_disponiveis(self) -> list[str] | None:
        """
        Consulta o Kokoro para obter vozes disponíveis (se endpoint existir).
        Retorna lista de nomes ou None.
        """
        try:
            r = requests.get(f"{self.kokoro_url}/v1/audio/voices", timeout=5)
            if r.status_code == 200:
                dados = r.json()
                return [v.get("id", v) if isinstance(v, dict) else v for v in dados]
        except requests.exceptions.RequestException:
            pass
        return None
