"""Leitura em português e inglês dos estados que não têm imagem de referência."""

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

import cv2
from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR


AMBIGUO = object()


@dataclass(frozen=True)
class Linha:
    texto: str
    caixa: tuple[int, int, int, int]

    @property
    def centro(self):
        x, y, w, h = self.caixa
        return x + w / 2, y + h / 2


def normalizar(texto):
    decomposicao = unicodedata.normalize("NFKD", texto.upper())
    sem_acentos = "".join(c for c in decomposicao if not unicodedata.combining(c))
    return " ".join(re.findall(r"[A-Z0-9]+", sem_acentos))


def combinar(linhas, *, vertical=False):
    """Inclui pares de caixas próximas do mesmo rótulo ou título."""
    yield from linhas
    eixo = 1 if vertical else 0
    ordenadas = sorted(linhas, key=lambda linha: linha.caixa[eixo])
    for i, primeira in enumerate(ordenadas):
        x, y, w, h = primeira.caixa
        for segunda in ordenadas[i + 1:]:
            xx, yy, ww, hh = segunda.caixa
            altura = max(h, hh)
            if vertical:
                perto = (-0.25 * altura <= yy - (y + h) <= 1.1 * altura
                         and abs(primeira.centro[0] - segunda.centro[0]) <= 0.35 * max(w, ww))
            else:
                perto = (-0.25 * altura <= xx - (x + w) <= 1.5 * altura
                         and abs(primeira.centro[1] - segunda.centro[1]) <= 0.45 * altura)
            if perto:
                esquerda, topo = min(x, xx), min(y, yy)
                direita, base = max(x + w, xx + ww), max(y + h, yy + hh)
                yield Linha(f"{primeira.texto} {segunda.texto}",
                            (esquerda, topo, direita - esquerda, base - topo))


def identificar(linhas, largura, altura, tipo, *, sinalizar_ambiguidade=False):
    if tipo in ("cabecalho_recompensa", "recompensa", "nome_recompensa"):
        cabecalhos = ("ITENS ADQUIRIDOS", "ITEM ADQUIRIDO", "ITENS OBTIDOS", "ITEM OBTIDO",
                      "ITEMS ACQUIRED", "ITEM ACQUIRED", "RECOMPENSAS ADQUIRIDAS",
                      "RECOMPENSAS OBTIDAS", "RECOMPENSAS RESGATADAS", "REWARDS EARNED",
                      "TESOURO ENCONTRADO", "RECOMPENSA DESBLOQUEADA", "RECOMPENSA CONQUISTADA")
        limite = 0.96 if tipo == "cabecalho_recompensa" else 0.40
        titulos = [linha for linha in combinar(linhas) if linha.centro[1] <= limite * altura
                   and normalizar(linha.texto).startswith(cabecalhos)]
        if not titulos:
            return None
        if tipo == "cabecalho_recompensa":
            return titulos[0]
        botoes = {"RESGATAR", "RESGATAR TUDO", "RESGATAR TODOS", "RESGATAR TODAS",
                  "COLETAR", "COLETAR TUDO", "CLAIM", "CLAIM ALL"}
        if tipo == "recompensa":
            opcoes = [linha for linha in combinar(linhas) if 0.22 * largura <= linha.centro[0] <= 0.80 * largura
                      and 0.70 * altura <= linha.centro[1] <= 0.98 * altura
                      and normalizar(linha.texto) in botoes]
            return max(opcoes, key=lambda linha: linha.caixa[2], default=None)
        genericos = ("OBRIGADO", "OBRIGADA", "THANKS", "OBRIGAD", "EQUIPAR", "EQUIP",
                     "IR PARA", "GO TO", "RARIDADE", "CONJUNTO", "PARTE DO", "PART OF", "VOLTAR",
                     "BACK", "INCOMUM", "COMUM", "RARO", "RARA", "EPICO", "EPICA", "LENDARI",
                     "UNCOMMON", "COMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC", "MITIC")
        categorias = {"PICKAXE", "PICARETA", "EMOTE", "GESTO", "TRAJE", "OUTFIT", "GLIDER", "ASA DELTA",
                      "SPRAY", "WRAP", "ENVELOPAMENTO", "MOCHILA", "BACK BLING", "HARVESTING TOOL"}
        candidatos = [linha for linha in combinar(linhas) if linha.caixa[0] >= 0.57 * largura
                      and 0.12 * altura <= linha.centro[1] <= 0.66 * altura
                      and len(normalizar(linha.texto)) >= 3 and not linha.texto.rstrip().endswith(".")
                      and not normalizar(linha.texto).startswith((*cabecalhos, *genericos))
                      and normalizar(linha.texto) not in botoes | categorias]
        return max(candidatos, key=lambda linha: (linha.caixa[3], -linha.caixa[1], linha.caixa[2]), default=None)
    if tipo == "fechar_asa":
        rotulos = {"ENTRAR EM QUEDA LIVRE", "PARA ENTRAR EM QUEDA LIVRE", "ESPACO PARA ENTRAR EM QUEDA LIVRE",
                   "PARA FECHAR ASA DELTA", "PARA FECHAR A ASA DELTA", "ESPACO PARA FECHAR ASA DELTA",
                   "ESPACO PARA FECHAR A ASA DELTA", "TO SKYDIVE", "SPACE TO SKYDIVE", "PRESS SPACE TO SKYDIVE"}
        return next((linha for linha in combinar(linhas) if normalizar(linha.texto) in rotulos), None)
    if tipo == "pular_onibus":
        rotulos = {"PARA PULAR", "ESPACO PARA PULAR", "SPACE PARA PULAR",
                   "APERTE ESPACO PARA PULAR", "PRESSIONE ESPACO PARA PULAR",
                   "TO JUMP", "SPACE TO JUMP", "PRESS SPACE TO JUMP",
                   "PULAR DO ONIBUS", "JUMP FROM THE BUS"}
        return next((linha for linha in combinar(linhas) if normalizar(linha.texto) in rotulos), None)
    if tipo == "pronto_lobby":
        rotulos = {normalizar(linha.texto): linha for linha in linhas}
        if any(texto == "ESC" or texto.startswith("ESC ") for texto in rotulos):
            return None
        return rotulos.get("PRONTO") or rotulos.get("READY") or rotulos.get("READY UP")
    if tipo in ("jogar_modo", "modo_esquadrao"):
        direita = [linha for linha in linhas if linha.centro[0] >= 0.56 * largura]
        por_texto = {normalizar(linha.texto): linha for linha in combinar(direita)}
        modo = por_texto.get("ESQUADRAO") or por_texto.get("SQUADS")
        modo_preencher = por_texto.get("PREENCHER") or por_texto.get("FILL")
        sem_preencher = any("NAO PREENCHER" in texto or "NO FILL" in texto
                            or "DONT FILL" in texto for texto in por_texto)
        if not (modo and modo_preencher and not sem_preencher):
            return None
        if tipo == "modo_esquadrao":
            return modo
        jogar = por_texto.get("JOGAR") or por_texto.get("PLAY")
        return jogar if jogar and jogar.centro[1] >= 0.8 * altura else None
    if tipo == "tela_regiao":
        return next((linha for linha in linhas if 0.20 * largura < linha.centro[0] < 0.65 * largura
                     and 0.12 * altura < linha.centro[1] < 0.42 * altura
                     and normalizar(linha.texto).startswith(
                         ("REGIAO DE PAREAMENT", "MATCHMAKING REGION"))), None)
    if tipo == "jogar":
        return next((linha for linha in combinar(linhas) if normalizar(linha.texto) in
                     {"JOGAR NOVAMENTE", "JOGARNOVAMENTE", "JOGAROVAMENTE",
                      "PLAY AGAIN", "PLAYAGAIN",
                      "READY UP AGAIN", "READYUPAGAIN"}), None)
    if tipo == "fila":
        rotulos = {
            "PROCURANDO PARTIDA", "BUSCANDO PARTIDA", "CANCELAR PAREAMENTO",
            "FINDING MATCH", "LOOKING FOR MATCH", "CANCEL MATCHMAKING", "NA FILA", "IN QUEUE",
        }
        return next((linha for linha in linhas if linha.centro[1] >= 0.50 * altura
                     and normalizar(linha.texto) in rotulos), None)
    if tipo == "fila_cancelada":
        rotulos = {"PAREAMENTO CANCELADO", "FILA CANCELADA", "MATCHMAKING CANCELED",
                   "MATCHMAKING CANCELLED", "QUEUE CANCELED", "QUEUE CANCELLED"}
        return next((linha for linha in combinar(linhas) if normalizar(linha.texto) in rotulos), None)
    central = [linha for linha in linhas if 0.20 * largura <= linha.centro[0] <= 0.80 * largura
               and 0.18 * altura <= linha.centro[1] <= 0.88 * altura]
    afirmativos = {"SIM", "CONFIRMAR", "YES", "CONFIRM"}
    saidas = {"VOLTAR AO LOBBY", "SAIR DA PARTIDA", "RETURN TO LOBBY", "LEAVE MATCH"}
    negativos = [linha for linha in central if normalizar(linha.texto) in {"NAO", "CANCELAR", "NO", "CANCEL"}]
    botoes = [(linha, normalizar(linha.texto)) for linha in combinar(central)]
    candidatos = {}
    for linha in combinar(list(combinar(central)), vertical=True):
        titulo = normalizar(linha.texto)
        menciona_saida = any(p in titulo for p in (
            "VOLTAR AO LOBBY", "SAIR DA PARTIDA", "ABANDONAR A PARTIDA", "RETURN TO LOBBY", "LEAVE MATCH",
        ))
        pergunta = "?" in linha.texto or any(p in titulo for p in (
            "DESEJA", "TEM CERTEZA", "ARE YOU SURE", "DO YOU WANT",
        ))
        if not (menciona_saida and pergunta):
            continue
        centro_titulo = linha.centro[0]
        base_titulo = linha.caixa[1] + linha.caixa[3]
        for botao, rotulo in botoes:
            if rotulo not in afirmativos | saidas:
                continue
            x, y = botao.centro
            if not 0.03 * altura <= y - base_titulo <= 0.50 * altura:
                continue
            for negativo in negativos:
                nx, ny = negativo.centro
                if not 0.03 * altura <= ny - base_titulo <= 0.50 * altura:
                    continue
                alinhamento = abs(y - ny) / altura
                desvio_centro = abs((x + nx) / 2 - centro_titulo) / largura
                distancia = abs(nx - x) / largura
                lado_a_lado = alinhamento <= 0.045 and 0.05 <= distancia <= 0.48
                empilhados = 0.05 <= alinhamento <= 0.18 and distancia <= 0.06
                if desvio_centro <= 0.12 and (lado_a_lado or empilhados):
                    nota = (rotulo not in afirmativos, alinhamento + desvio_centro)
                    anterior = candidatos.get(botao.caixa)
                    if anterior is None or nota < anterior[0]:
                        candidatos[botao.caixa] = nota, botao, linha, negativo
    if not candidatos:
        return None
    melhores = sorted(candidatos.values(), key=lambda item: item[0])
    (classe, nota), botao, titulo, negativo = melhores[0]
    for (outra_classe, outra_nota), _, outro_titulo, outro_negativo in melhores[1:]:
        grupos_distintos = (
            (abs(titulo.centro[0] - outro_titulo.centro[0]) >= 0.15 * largura
             or abs(titulo.centro[1] - outro_titulo.centro[1]) >= 0.15 * altura)
            and negativo.caixa != outro_negativo.caixa
        )
        if (classe == outra_classe or grupos_distintos) and outra_nota <= max(0.08, nota + 0.05):
            return AMBIGUO if sinalizar_ambiguidade else None
    return botao


class Reconhecedor:
    def __init__(self):
        self.dialogo_ambiguo = False
        self._quadro_lido = None
        self._linhas = []
        modelos = Path(__file__).resolve().with_name("modelos")
        det = modelos / "PP-OCRv6_det_small.onnx"
        rec = modelos / "latin_PP-OCRv5_rec_mobile.onnx"
        if not (det.is_file() and rec.is_file()):
            raise ValueError("Modelos de leitura ausentes. Execute preparar_ocr.py ou use o executável.")
        self.ocr = RapidOCR(params={
            "Global.use_cls": False, "Global.log_level": "error", "Global.max_side_len": 1600,
            # Limita a maior dimensão para evitar ampliar recortes largos do HUD.
            "Det.limit_side_len": 960, "Det.limit_type": "max", "Det.model_path": str(det),
            "Rec.model_path": str(rec), "Rec.lang_type": LangRec.LATIN,
            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        })

    def linhas(self, quadro):
        if self._quadro_lido is not quadro:
            resultado = self.ocr(quadro, use_cls=False)
            self._linhas = [] if resultado.boxes is None else [
                Linha(texto, cv2.boundingRect(caixa.astype("float32")))
                for caixa, texto, score in zip(resultado.boxes, resultado.txts, resultado.scores)
                if score >= 0.78
            ]
            self._quadro_lido = quadro
        return self._linhas

    def ler(self, quadro, tipo):
        altura, largura = quadro.shape[:2]
        linha = identificar(self.linhas(quadro), largura, altura, tipo, sinalizar_ambiguidade=True)
        if tipo == "confirmar_sair":
            self.dialogo_ambiguo = linha is AMBIGUO
        return None if linha is AMBIGUO else linha
