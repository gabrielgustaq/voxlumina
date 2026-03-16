"""
modules/ingestor.py
─────────────────────────────────────────────────────────────────────────────
Responsabilidade: Converter PDFs acadêmicos em Markdown estruturado e
                  extrair recortes de imagem com coordenadas de página.

Dependências: docling, Pillow
Instalação:   pip install docling Pillow
─────────────────────────────────────────────────────────────────────────────
"""

import logging
import shutil
from pathlib import Path

log = logging.getLogger("VoxLumina.Ingestor")


class Ingestor:
    """
    Converte um PDF em:
      - markdown_texto  : str  → conteúdo textual estruturado
      - imagens         : list → [{"caminho": Path, "pagina": int, "bbox": tuple}]

    Usa Docling para parsing e PIL para salvar recortes de imagem.
    """

    def __init__(self, imagens_dir: Path = Path("temp_imagens")):
        self.imagens_dir = imagens_dir
        self.imagens_dir.mkdir(parents=True, exist_ok=True)

    # ─── API Pública ──────────────────────────────────────────────────────────

    def processar(self, pdf_path: Path) -> dict | None:
        """
        Processa um PDF e retorna um dicionário com markdown e metadados de imagens.

        Retorna:
            {
                "markdown": str,
                "imagens": [
                    {"caminho": Path, "pagina": int, "bbox": (x0, y0, x1, y1)},
                    ...
                ]
            }
            ou None em caso de falha.
        """
        if not pdf_path.exists():
            log.error(f"PDF não encontrado: {pdf_path}")
            return None

        log.info(f"Iniciando ingestão: {pdf_path.name}")

        # Limpa recortes anteriores do mesmo arquivo
        self._limpar_imagens_temp(pdf_path.stem)

        try:
            return self._processar_com_docling(pdf_path)
        except ImportError:
            log.error("Docling não instalado. Execute: pip install docling")
            return None
        except Exception as e:
            log.error(f"Erro na ingestão com Docling: {e}", exc_info=True)
            return None

    # ─── Implementação Docling ─────────────────────────────────────────────────

    def _processar_com_docling(self, pdf_path: Path) -> dict:
        from docling.document_converter import DocumentConverter
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        # Configuração otimizada para 32GB RAM: usa OCR completo + layout detection
        pipeline_opts = PdfPipelineOptions()
        pipeline_opts.do_ocr = True
        pipeline_opts.do_table_structure = True
        pipeline_opts.images_scale = 2.0          # Alta resolução para imagens
        pipeline_opts.generate_page_images = False  # Economiza memória

        converter = DocumentConverter()

        log.info("  Docling convertendo PDF... (pode levar alguns minutos)")
        result = converter.convert(str(pdf_path))
        doc = result.document

        # ── Exporta Markdown ────────────────────────────────────────────────
        markdown_texto = doc.export_to_markdown()
        log.info(f"  Markdown gerado: {len(markdown_texto)} chars")

        # ── Extrai imagens com coordenadas ──────────────────────────────────
        imagens_extraidas = self._extrair_imagens_docling(doc, pdf_path)
        log.info(f"  Imagens extraídas: {len(imagens_extraidas)}")

        return {
            "markdown": markdown_texto,
            "imagens": imagens_extraidas,
        }

    def _extrair_imagens_docling(self, doc, pdf_path: Path) -> list:
        """
        Extrai e salva recortes de imagem a partir do documento Docling.
        Usa PIL para recortar a partir das páginas renderizadas.
        """
        imagens = []

        try:
            from PIL import Image
            import io
        except ImportError:
            log.warning("Pillow não instalado (pip install Pillow). Imagens serão ignoradas.")
            return []

        # Itera sobre elementos do documento que sejam figuras/imagens
        for elem_idx, (element, _level) in enumerate(doc.iterate_items()):
            # Docling representa imagens como PictureItem ou FigureItem
            tipo_elem = type(element).__name__
            if tipo_elem not in ("PictureItem", "FigureItem", "ImageRefItem"):
                continue

            try:
                # Pega referência da página e bounding box
                prov = element.prov[0] if element.prov else None
                if prov is None:
                    continue

                pagina_num = prov.page_no
                bbox = prov.bbox  # BoundingBox com l, t, r, b

                # Tenta obter a imagem diretamente do elemento Docling
                img_data = self._obter_imagem_elemento(element, doc)

                if img_data is not None:
                    nome_img = f"{pdf_path.stem}_p{pagina_num:03d}_img{elem_idx:03d}.png"
                    caminho_img = self.imagens_dir / nome_img

                    with open(caminho_img, "wb") as f:
                        f.write(img_data)

                    imagens.append({
                        "caminho": caminho_img,
                        "pagina": pagina_num,
                        "bbox": (
                            getattr(bbox, "l", 0),
                            getattr(bbox, "t", 0),
                            getattr(bbox, "r", 0),
                            getattr(bbox, "b", 0),
                        ),
                        "elemento_idx": elem_idx,
                    })
                    log.debug(f"  Imagem salva: {nome_img}")

            except (AttributeError, IndexError, OSError) as e:
                log.debug(f"  Ignorando elemento {elem_idx}: {e}")
                continue

        return imagens

    def _obter_imagem_elemento(self, element, doc) -> bytes | None:
        """
        Tenta obter bytes PNG de um elemento de imagem Docling.
        Fallback: retorna None se não conseguir.
        """
        import io

        # Método 1: get_image() nativo do Docling (disponível em versões recentes)
        try:
            if hasattr(element, "get_image"):
                img = element.get_image(doc)
                if img is not None:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    return buf.getvalue()
        except Exception:
            pass

        # Método 2: image_data direto no elemento
        try:
            if hasattr(element, "image") and element.image is not None:
                buf = io.BytesIO()
                element.image.save(buf, format="PNG")
                return buf.getvalue()
        except Exception:
            pass

        # Método 3: dados brutos em pil_image
        try:
            if hasattr(element, "pil_image") and element.pil_image is not None:
                buf = io.BytesIO()
                element.pil_image.save(buf, format="PNG")
                return buf.getvalue()
        except Exception:
            pass

        return None

    # ─── Utilitários ──────────────────────────────────────────────────────────

    def _limpar_imagens_temp(self, prefixo: str):
        """Remove imagens antigas do mesmo PDF para evitar acúmulo."""
        removidos = 0
        for img in self.imagens_dir.glob(f"{prefixo}_*.png"):
            try:
                img.unlink()
                removidos += 1
            except OSError:
                pass
        if removidos:
            log.debug(f"  Removidos {removidos} recortes anteriores de '{prefixo}'")

    def limpar_tudo(self):
        """Limpa todos os arquivos temporários de imagem."""
        if self.imagens_dir.exists():
            shutil.rmtree(self.imagens_dir)
            self.imagens_dir.mkdir(parents=True, exist_ok=True)
            log.info("Pasta de imagens temporárias limpa.")
