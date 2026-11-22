"""State machine and screen detector for the independent Auto Farm app."""

from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
import math
from pathlib import Path
import random

import cv2
import numpy as np

from embarque import Embarque
from sobreviventes import Sobreviventes
from regiao import MANHA, NOITE, RegiaoLobby, TelaRegiao, ler_tela_regiao, regiao_por_horario


# Nome do PNG, região de busca (x0, y0, x1, y1) e fator de trabalho em 1080p.
TEMPLATES = {
    "jogar": ("jogar_novamente.png", (0.45, 0, 1, 1), 0.5),
    "continuar": ("continuar.png", (0.6, 0.8, 1, 1), 0.5),
    "menu_aba": ("menu_aba.png", (0.55, 0, 1, 0.25), 1.0),
    "menu_aba_sel": ("menu_aba_sel.png", (0.55, 0, 1, 0.25), 1.0),
    "voltar_lobby": ("voltar_lobby.png", (0.55, 0, 1, 0.45), 1.0),
    "lobby_jogar": ("lobby_jogar.png", (0, 0.6, 0.4, 1), 0.5),
    "lobby_pronto": ("lobby_pronto.png", (0, 0.6, 0.4, 1), 0.5),
    "resgatar": ("resgatar.png", (0.25, 0.7, 0.75, 1), 0.5),
    "engrenagem_lobby": ("engrenagem_lobby.png", (0.66, 0.54, 0.76, 0.88), 1.0),
    "engrenagem_menu_lobby": ("engrenagem_menu_lobby.png", (0.77, 0.018, 0.84, 0.106), 1.0),
    "seta_regiao": ("seta_regiao.png", (0, 0, 1, 1), 1.0),
    "espectador": ("espectador.png", (0.3, 0, 0.7, 0.12), 1.0),
    "inventario": ("inventario.png", (0.65, 0.82, 1, 1), 1.0),
    "aba_jogar": ("aba_jogar.png", (0, 0, 0.3, 0.12), 1.0),
    "aba_jogar_lobby": ("aba_jogar_lobby.png", (0.085, 0.025, 0.19, 0.10), 1.0),
    "onibus": ("onibus.png", (0.5, 0.45, 0.85, 0.65), 1.0),
    "asa_delta": ("asa_delta.png", (0.5, 0.45, 0.85, 0.65), 1.0),
    "reinicializando": ("reinicializando.png", (0.3, 0.45, 0.7, 0.7), 1.0),
}
BOTOES_LOBBY = ("lobby_jogar", "lobby_pronto")
BOTOES_FILA = ("jogar", *BOTOES_LOBBY)
AVISOS = ("onibus", "asa_delta", "reinicializando")
ENGRENAGENS = ("engrenagem_lobby", "engrenagem_menu_lobby")
TECLAS = {"W": 17, "A": 30, "D": 32}
SEQUENCIA_AEREA = ("W", "WA", "W", "WD")
ACOES_AEREAS = {
    "saltar_onibus": (("teclas", (57,), 0.16),),
    "fechar_asa": (("teclas", (57,), 0.16),),
}
DURACAO_CAMINHADA = 1.45
TOLERANCIA_HUD = 5.0


@dataclass(frozen=True)
class Config:
    limiar: float = 0.85
    escala_min: float = 0.6
    escala_max: float = 1.4
    escala_passo: float = 0.1
    fallback_cor: bool = True
    clicar_continuar: bool = True
    confirmacoes: int = 2
    timeout_lobby: float = 90.0
    sair_por_tempo: bool = True
    minutos_partida: float = 7.5
    anti_afk: bool = True
    rotina_aerea: bool = True
    segundo_plano: bool = False
    cursor_segundo_plano: bool = False
    atualizar_auto: bool = True
    resgatar_recompensas: bool = True
    notificar_tesouros: bool = False
    webhook_url: str = field(default="", repr=False)
    regiao_auto: bool = False
    regiao_manha: str = MANHA[0]
    regiao_noite: str = NOITE[0]
    aparencia: str = "azul"
    pausar_ao_obter_token: bool = False
    sair_quando_sozinho: bool = True
    rota_baus: bool = False


@dataclass(frozen=True)
class Alvo:
    nome: str
    score: float
    caixa: tuple[int, int, int, int]
    escala: float | None
    metodo: str = "template"
    item: str | None = None

    @property
    def centro(self):
        x, y, largura, altura = self.caixa
        return x + largura / 2, y + altura / 2


def _resize(imagem, fator):
    if abs(fator - 1) < 0.001:
        return imagem
    interpolacao = cv2.INTER_AREA if fator < 1 else cv2.INTER_LINEAR
    if round(min(imagem.shape[:2]) * fator) < 1:
        largura = max(1, round(imagem.shape[1] * fator))
        altura = max(1, round(imagem.shape[0] * fator))
        return cv2.resize(imagem, (largura, altura), interpolation=interpolacao)
    return cv2.resize(imagem, None, fx=fator, fy=fator, interpolation=interpolacao)


def _branco(imagem):
    mascara = ((imagem.min(axis=2) >= 200) * 255).astype(np.uint8)
    return cv2.cvtColor(mascara, cv2.COLOR_GRAY2BGR)


def _roi(imagem, limites):
    altura, largura = imagem.shape[:2]
    x0, y0, x1, y1 = limites
    x0, y0 = int(x0 * largura), int(y0 * altura)
    return imagem[y0:int(y1 * altura), x0:int(x1 * largura)], x0, y0


def _cruz_da_vida(branco, x, y, altura):
    margem = max(22, round(altura * 0.9))
    esquerda, topo = max(0, x - margem), max(0, y - 3)
    area = branco[topo:y + altura + 3, esquerda:x]
    if not area.size:
        return False
    contornos, _ = cv2.findContours(area, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contorno in contornos:
        xx, yy, w, h = cv2.boundingRect(contorno)
        if not (6 <= w <= 24 and 6 <= h <= 24 and 0.7 <= w / h <= 1.3
                and abs(topo + yy + h / 2 - (y + altura / 2)) <= max(7, 0.35 * altura)):
            continue
        forma = cv2.resize(area[yy:yy + h, xx:xx + w], (9, 9), interpolation=cv2.INTER_AREA) >= 128
        bracos = (forma[3:6, :3], forma[3:6, 6:], forma[:3, 3:6], forma[6:, 3:6])
        cantos = (forma[:3, :3], forma[:3, 6:], forma[6:, :3], forma[6:, 6:])
        if all(b.mean() >= 0.55 for b in bracos) and all(c.mean() <= 0.45 for c in cantos):
            return True
    return False


class Detector:
    def __init__(self, pasta=None, config=Config(), leitor=None):
        self.config = config
        self.leitor = leitor
        self._ultimo_texto = None
        self._recorte_avisos = None
        self._cache_recompensa = None
        self.dialogo_ambiguo = False
        pasta = Path(pasta) if pasta else Path(__file__).with_name("templates")
        self.templates = {}
        self.ultima_escala = {}
        self._cache = {}
        self._quadro = None
        self._redimensionados = {}
        for nome, (arquivo, _, fator) in TEMPLATES.items():
            caminho = pasta / arquivo
            if caminho.is_file():
                imagem = cv2.imdecode(np.frombuffer(caminho.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                if imagem is not None:
                    imagem = _resize(imagem, fator)
                    if nome in ENGRENAGENS:
                        imagem = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
                    self.templates[nome] = _branco(imagem) if nome in AVISOS else imagem

    def preparar(self, quadro, fator_trabalho=0.5):
        if self._quadro is not quadro:
            self._quadro = quadro
            self._redimensionados.clear()
        if fator_trabalho not in self._redimensionados:
            fator = 1080 * fator_trabalho / quadro.shape[0]
            self._redimensionados[fator_trabalho] = _resize(quadro, fator), fator
        return self._redimensionados[fator_trabalho]

    def escalas(self):
        menor, maior = sorted((self.config.escala_min, self.config.escala_max))
        if not (0 < menor <= maior < math.inf):
            raise ValueError("As escalas precisam ser positivas e finitas.")
        passo = max(0.02, self.config.escala_passo)
        escalas = []
        while menor <= maior + 1e-6:
            escalas.append(round(menor, 3))
            menor += passo
        return escalas or [1.0]

    @staticmethod
    def _comparar(regiao, template):
        h, w = template.shape[:2]
        if not (6 <= h <= regiao.shape[0] and 6 <= w <= regiao.shape[1]):
            return 0.0, (0, 0)
        matriz = cv2.matchTemplate(regiao, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, posicao = cv2.minMaxLoc(matriz)
        return score, posicao

    def _estimar_escala(self, quadro, nome, limites, escalas):
        imagem, _ = self.preparar(quadro)
        regiao, _, _ = _roi(imagem, limites)
        if not regiao.size:
            return 1.0
        if nome in AVISOS:
            regiao = _branco(regiao)
        regiao = cv2.cvtColor(regiao, cv2.COLOR_BGR2GRAY)
        base = _resize(self.templates[nome], 0.5)
        if base.ndim == 3:
            base = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
        escala, maior = min(escalas), max(escalas)
        melhor_score, estimativa = -1.0, 1.0
        while escala <= maior + 1e-6:
            score, _ = self._comparar(regiao, _resize(base, escala))
            if score > melhor_score:
                melhor_score, estimativa = score, escala
            escala += 0.04
        return estimativa

    def buscar_template(self, quadro, nome, limiar=None, *, limites=None):
        if nome not in self.templates:
            return None
        limiar = self.config.limiar if limiar is None else limiar
        _, limites_padrao, fator_trabalho = TEMPLATES[nome]
        limites = limites or limites_padrao
        imagem, fator = self.preparar(quadro, fator_trabalho)
        regiao, x0, y0 = _roi(imagem, limites)
        if nome in AVISOS:
            regiao = _branco(regiao)
        elif nome in ENGRENAGENS:
            regiao = cv2.cvtColor(regiao, cv2.COLOR_BGR2GRAY)
        escalas = self.escalas()
        ultima = self.ultima_escala.get(nome)
        melhor = None

        def testar(escala):
            nonlocal melhor
            chave = nome, escala
            if chave not in self._cache:
                self._cache[chave] = _resize(self.templates[nome], escala)
            template = self._cache[chave]
            score, (x, y) = self._comparar(regiao, template)
            if score > (melhor.score if melhor else 0):
                h, w = template.shape[:2]
                caixa = tuple(round(v / fator) for v in (x + x0, y + y0, w, h))
                melhor = Alvo(nome, score, caixa, escala)
            return score

        if fator_trabalho >= 1:
            if ultima is None or testar(ultima) < limiar:
                centro = self._estimar_escala(quadro, nome, limites, escalas)
                for passo in sorted(range(-5, 6), key=abs):
                    if testar(round(centro + passo * 0.01, 3)) >= limiar:
                        break
        else:
            ordem = escalas if ultima is None else [ultima, *sorted(
                (e for e in escalas if e != ultima), key=lambda e: abs(e - ultima)
            )]
            for escala in ordem:
                if testar(escala) >= limiar:
                    break
        if melhor and limiar - 0.2 <= melhor.score < 0.95:
            centro = melhor.escala
            for ajuste in (-0.05, 0.05, -0.025, 0.025):
                testar(round(centro + ajuste, 3))
        if melhor and melhor.score >= limiar:
            self.ultima_escala[nome] = melhor.escala
        return melhor

    def procurar(self, quadro, nomes, limiar=None):
        limiar = self.config.limiar if limiar is None else limiar
        candidatos = (self.buscar_template(quadro, nome, limiar) for nome in nomes)
        validos = (alvo for alvo in candidatos if alvo and alvo.score >= limiar
                   and (alvo.nome != "lobby_pronto" or self._confirmar_pronto(quadro, alvo)))
        melhor = max(validos, key=lambda alvo: alvo.score, default=None)
        if melhor is None and "lobby_jogar" in nomes:
            return self._ler_jogar_painel(quadro)
        return melhor

    def _confirmar_pronto(self, quadro, alvo):
        x, y, w, h = alvo.caixa
        recorte = quadro[y:y + h, x:x + w]
        if recorte.size == 0:
            return False
        hsv = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
        amarelo = cv2.inRange(hsv, (20, 140, 145), (38, 255, 255))
        if cv2.countNonZero(amarelo) < 0.64 * amarelo.size:
            return False
        ampliado = _resize(recorte, max(1, 90 / recorte.shape[0]))
        margem = cv2.copyMakeBorder(ampliado, 20, 20, 20, 20, cv2.BORDER_CONSTANT,
                                    value=(255, 255, 255))
        return bool(self.ler_contexto(margem, "pronto_lobby"))

    def aviso_onibus(self, quadro):
        return self._aviso_aereo(quadro, "onibus", "pular_onibus")

    def aviso_asa(self, quadro):
        return self._aviso_aereo(quadro, "asa_delta", "fechar_asa")

    def recompensa(self, quadro):
        if self._cache_recompensa is not None and self._cache_recompensa[0] is quadro:
            return self._cache_recompensa[1]
        recorte = quadro[:round(quadro.shape[0] * 0.42)]
        alvo = None
        if self.ler_contexto(recorte, "cabecalho_recompensa"):
            botao = self.ler_contexto(quadro, "recompensa")
            if botao:
                titulo = self.leitor.ler(quadro, "nome_recompensa")
                alvo = replace(botao, nome="resgatar", item=titulo.texto if titulo else None)
        self._cache_recompensa = quadro, alvo
        return alvo

    def tela_regiao(self, quadro):
        altura, largura = quadro.shape[:2]
        engrenagem = self._engrenagem_horizontal(quadro)
        if engrenagem and self._menu_do_painel_social(quadro):
            return TelaRegiao(social=True, engrenagem=engrenagem.caixa)
        leitor = self._carregar_leitor()
        linhas = leitor.linhas(quadro)
        tela = ler_tela_regiao(linhas, largura, altura)
        if not (tela.social or tela.configuracoes or tela.dialogo_alteracoes):
            # Ao abrir Configurações, o rodapé pode ficar pequeno na leitura da tela inteira.
            rodape, x0, y0 = _roi(quadro, (0.59, 0.86, 1, 1))
            linhas_rodape = []
            for linha in leitor.linhas(_resize(rodape, 1.5)):
                x, y, w, h = (round(v / 1.5) for v in linha.caixa)
                linhas_rodape.append(replace(linha, caixa=(x + x0, y + y0, w, h)))
            linhas = [*linhas, *linhas_rodape]
            tela = ler_tela_regiao(linhas, largura, altura)
        if tela.configuracoes and not (tela.pareamento and tela.pareamento.valor):
            recorte, x0, y0 = _roi(quadro, (0.10, 0.13, 0.66, 0.46))
            detalhadas = []
            for linha in leitor.linhas(_resize(recorte, 1.5)):
                x, y, w, h = (round(v / 1.5) for v in linha.caixa)
                detalhadas.append(replace(linha, caixa=(x + x0, y + y0, w, h)))
            tela = ler_tela_regiao([*linhas, *detalhadas], largura, altura)
        if tela.social:
            if engrenagem:
                tela = replace(tela, engrenagem=engrenagem.caixa)
            elif not tela.engrenagem:
                antiga = self.buscar_template(quadro, "engrenagem_lobby", 0.87)
                if antiga and antiga.score >= 0.87:
                    tela = replace(tela, engrenagem=antiga.caixa)
        return replace(tela, pareamento=self._setas_regiao(quadro, tela.pareamento))

    def _engrenagem_horizontal(self, quadro):
        altura, largura = quadro.shape[:2]
        limites = (max(0, 1 - 0.35 * altura / largura), 0.018,
                   1 - 0.263 * altura / largura, 0.106)
        alvo = self.buscar_template(quadro, "engrenagem_menu_lobby", 0.87, limites=limites)
        return alvo if alvo and alvo.score >= 0.87 else None

    def _menu_do_painel_social(self, quadro):
        altura, largura = quadro.shape[:2]
        limites = (max(0, 1 - 0.25 * altura / largura), 0.018,
                   1 - 0.151 * altura / largura, 0.106)
        for nome in ("menu_aba", "menu_aba_sel"):
            alvo = self.buscar_template(quadro, nome, 0.81, limites=limites)
            if alvo and alvo.score >= 0.81:
                return True
        return False

    def painel_social_lobby(self, quadro):
        return bool(self._engrenagem_horizontal(quadro) and self._menu_do_painel_social(quadro))

    def perfil_lobby(self, quadro):
        """Localiza a entrada do menu somente no cabeçalho Jogar reconhecido."""
        if not self.procurar(quadro, ("aba_jogar_lobby", "aba_jogar")):
            return None
        altura, largura = quadro.shape[:2]
        lado = round(0.046 * altura)
        return largura - round(0.091 * altura) - lado // 2, round(0.058 * altura) - lado // 2, lado, lado

    def _setas_regiao(self, quadro, linha):
        if linha is None or "seta_regiao" not in self.templates:
            return linha
        altura, largura = quadro.shape[:2]
        x, y, w, h = linha.caixa
        cy = y + h / 2
        topo, base = round(cy - 0.022 * altura), round(cy + 0.022 * altura)
        template = _branco(self.templates["seta_regiao"])

        def procurar(ini, fim, modelo):
            if ini >= fim:
                return None
            recorte = _branco(quadro[topo:base, ini:fim])
            melhor = None
            for escala in (0.70, 0.80, 0.90, 1, 1.1, 1.2):
                alvo = _resize(modelo, altura / 1080 * escala)
                score, (px, py) = self._comparar(recorte, alvo)
                if score >= 0.83 and (melhor is None or score > melhor[0]):
                    melhor = score, (ini + px, topo + py, alvo.shape[1], alvo.shape[0])
            return melhor[1] if melhor else None

        centro = x + w / 2
        direita = procurar(round(centro + 0.04 * largura), round(min(centro + 0.16 * largura, 0.68 * largura)), template)
        esquerda = procurar(round(max(centro - 0.16 * largura, 0.29 * largura)),
                            round(centro - 0.04 * largura), cv2.flip(template, 1))
        return replace(linha, direita=direita, esquerda=esquerda)

    def _aviso_aereo(self, quadro, nome, tipo_texto):
        candidato = self.buscar_template(quadro, nome, 0.8)
        score = candidato.score if candidato else 0.0
        if score >= 0.8:
            return candidato, score
        recorte, _, _ = _roi(quadro, (0.46, 0.42, 0.90, 0.69))
        if score >= 0.48 or self._faixa_texto_claro(recorte, quadro.shape[0]):
            if self._recorte_avisos is None or self._recorte_avisos[0] is not quadro:
                self._recorte_avisos = quadro, _resize(recorte, 1.5)
            ampliado = self._recorte_avisos[1]
            texto = self.ler_contexto(ampliado, tipo_texto)
            if texto:
                return texto, score
        return None, score

    @staticmethod
    def _faixa_texto_claro(recorte, altura):
        branco = cv2.inRange(recorte, (175, 175, 175), (255, 255, 255))
        largura = recorte.shape[1]
        # Aproxima as letras da mesma linha sem juntar linhas vizinhas.
        ligadas = cv2.morphologyEx(branco, cv2.MORPH_CLOSE,
                                   np.ones((2, max(5, largura // 24)), np.uint8))
        contornos, _ = cv2.findContours(ligadas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return any(0.09 * largura <= w <= 0.92 * largura and 0.006 * altura <= h <= 0.055 * altura
                   and w >= 2 * h for _, _, w, h in map(cv2.boundingRect, contornos))

    def _ler_jogar_painel(self, quadro):
        regiao, x0, y0 = _roi(quadro, (0.55, 0.8, 0.98, 0.98))
        hsv = cv2.cvtColor(regiao, cv2.COLOR_BGR2HSV)
        mascara = cv2.inRange(hsv, (20, 140, 110), (38, 255, 255))
        contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        altura, largura = quadro.shape[:2]
        for contorno in contornos:
            x, y, w, h = cv2.boundingRect(contorno)
            if not (0.13 * largura <= w <= 0.26 * largura and 0.05 * altura <= h <= 0.11 * altura
                    and cv2.contourArea(contorno) >= 0.8 * w * h):
                continue
            alvo = self.ler_contexto(quadro, "jogar_modo")
            if alvo:
                cx, cy = alvo.centro
                if x + x0 <= cx <= x + x0 + w and y + y0 <= cy <= y + y0 + h:
                    return replace(alvo, nome="lobby_jogar", caixa=(x + x0, y + y0, w, h))
        return None

    def buscar_por_cor(self, quadro):
        imagem, fator = self.preparar(quadro)
        altura, largura = imagem.shape[:2]
        x0, y0 = int(largura * 0.6), int(altura * 0.3)
        recorte = imagem[y0:int(altura * 0.75), x0:]
        hsv = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
        mascara = cv2.inRange(hsv, (22, 150, 190), (38, 255, 255))
        # A borda externa engloba os furos das letras sem fundir controles vizinhos.
        contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cinza = cv2.cvtColor(recorte, cv2.COLOR_BGR2GRAY)
        melhor, maior_area = None, 0
        for contorno in contornos:
            x, y, w, h = cv2.boundingRect(contorno)
            if not (0.045 * altura <= h <= 0.16 * altura
                    and 0.25 * altura <= w <= 0.8 * altura and 3 <= w / h <= 9):
                continue
            area = w * h
            janela = np.s_[y:y + h, x:x + w]
            amarelo = cv2.countNonZero(mascara[janela]) / area
            escuro = float((cinza[janela] < 90).mean())
            if (area > maior_area and cv2.contourArea(contorno) / area >= 0.85
                    and amarelo >= 0.5 and 0.03 <= escuro <= 0.45):
                caixa = tuple(round(v / fator) for v in (x + x0, y + y0, w, h))
                melhor, maior_area = Alvo("jogar", amarelo, caixa, None, "cor+OCR"), area
        return melhor

    def fora_de_partida(self, quadro):
        return self.procurar(quadro, (*BOTOES_LOBBY, "menu_aba", "menu_aba_sel")) is not None

    def em_partida(self, quadro):
        """Reconhece a barra própria de vida e sua cruz branca à esquerda."""
        imagem, _ = self.preparar(quadro, 1.0)
        recorte, _, _ = _roi(imagem, (0, 0.60, 0.45, 1))
        hsv = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
        altura, largura = hsv.shape[:2]
        branco = cv2.inRange(hsv, (0, 0, 185), (180, 85, 255))
        branco_claro = None
        # Duas faixas de brilho acomodam HUDs claros e escuros.
        for v_min in (155, 95):
            verde = cv2.inRange(hsv, (28, 120, v_min), (82, 255, 255))
            contornos, _ = cv2.findContours(verde, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contorno in contornos:
                x, y, w, h = cv2.boundingRect(contorno)
                if not (10 <= h <= 60 and w >= 2 and x < 0.35 * largura and y > 0.4 * altura):
                    continue
                vida_baixa = w < 1.2 * h
                if vida_baixa and not (x < 0.15 * largura and y > 0.73 * altura):
                    continue
                barra = verde[y:y + h, x:x + w]
                if cv2.countNonZero(barra) < 0.60 * w * h:
                    continue
                if not vida_baixa and hsv[y:y + h, x:x + w, 0].mean() >= 65:
                    continue
                cruz = _cruz_da_vida(branco, x, y, h)
                if not cruz:
                    # Fundos claros podem ligar a cruz ao cenário na primeira máscara.
                    if branco_claro is None:
                        branco_claro = cv2.inRange(hsv, (0, 0, 220), (180, 85, 255))
                    cruz = _cruz_da_vida(branco_claro, x, y, h)
                if cruz and (not vida_baixa or self._sobrescudo_no_hud(hsv, x, y, h)):
                    return True
        return False

    @staticmethod
    def _sobrescudo_no_hud(hsv, x, y, altura_vida):
        # Com pouca vida, exige também o ícone ciano alinhado à direita da barra.
        h = altura_vida
        regiao = hsv[max(0, y - h):y + 2 * h, x + 7 * h:x + 17 * h]
        if not regiao.size:
            return False
        ciano = cv2.inRange(regiao, (85, 100, 125), (104, 255, 255))
        contornos, _ = cv2.findContours(ciano, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contorno in contornos:
            _, topo, largura, altura = cv2.boundingRect(contorno)
            if (0.8 * h <= altura <= 1.7 * h and 0.75 * altura <= largura <= 1.35 * altura
                    and topo + altura / 2 <= 1.65 * h
                    and cv2.contourArea(contorno) >= 0.52 * largura * altura):
                return True
        return False

    def personagem_derrubado(self, quadro):
        imagem, _ = self.preparar(quadro, 1.0)
        recorte, _, _ = _roi(imagem, (0, 0.89, 0.25, 0.97))
        hsv = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
        rosa = cv2.inRange(hsv, (165, 135, 100), (179, 255, 255))
        vermelho = cv2.inRange(hsv, (0, 135, 100), (8, 255, 255))
        mascara = cv2.bitwise_or(rosa, vermelho)
        contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for x, y, largura, altura in map(cv2.boundingRect, contornos):
            if not (30 <= x <= 90 and 12 <= altura <= 45 and 2 * altura <= largura <= 320):
                continue
            if cv2.countNonZero(mascara[y:y + altura, x:x + largura]) < 0.65 * largura * altura:
                continue
            icone = hsv[y:y + altura, max(0, x - 25):x]
            branco = cv2.inRange(icone, (0, 0, 205), (180, 75, 255))
            marcas, _ = cv2.findContours(branco, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for ix, iy, iw, ih in map(cv2.boundingRect, marcas):
                if not (6 <= iw <= 24 and 6 <= ih <= 24 and abs(iy + ih / 2 - altura / 2) <= 7):
                    continue
                forma = cv2.resize(branco[iy:iy + ih, ix:ix + iw], (9, 9)) >= 128
                if (forma.diagonal().mean() >= 0.55 and np.fliplr(forma).diagonal().mean() >= 0.55
                        and forma[:2, 4].mean() <= 0.5):
                    return True
        return False

    def barras_esquadrao(self, quadro):
        """Conta pares de vida e escudo nas linhas pequenas acima do HUD próprio."""
        recorte, _, _ = _roi(quadro, (0.006, 0.69, 0.165, 0.90))
        hsv = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
        altura, largura = quadro.shape[:2]

        def barras(minimo, maximo):
            mascara = cv2.inRange(hsv, minimo, maximo)
            contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            return [(x, y, w, h) for x, y, w, h in map(cv2.boundingRect, contornos)
                    if 0.035 * largura <= w <= 0.125 * largura
                    and 0.003 * altura <= h <= 0.015 * altura and w >= 7 * h]

        vida = barras((30, 130, 110), (78, 255, 255))
        escudo = barras((79, 110, 120), (108, 255, 255))
        linhas = [y for x, y, _, h in vida if any(
            abs(x - xx) <= 0.012 * largura and 0 < y - yy <= 0.016 * altura
            and abs(h - hh) <= 0.008 * altura for xx, yy, _, hh in escudo
        )]
        contagem = 0
        ultima = -altura
        for y in sorted(linhas):
            if y - ultima >= 0.022 * altura:
                contagem += 1
                ultima = y
        return contagem if 1 <= contagem <= 3 else None

    def detectar(self, quadro):
        """Prioridade da v59: Jogar Novamente, alternativa por cor, Continuar."""
        referencia = self.buscar_template(quadro, "jogar")
        if referencia and referencia.score >= self.config.limiar:
            return referencia
        if self.config.fallback_cor:
            alvo = self.buscar_por_cor(quadro)
            if alvo and not self.fora_de_partida(quadro):
                x, y, w, h = alvo.caixa
                if self.ler_contexto(quadro[y:y + h, x:x + w], "jogar"):
                    return alvo
        if self.config.clicar_continuar:
            return self.procurar(quadro, ("continuar",))
        return None

    def _carregar_leitor(self):
        if self.leitor is None:
            from texto_tela import Reconhecedor
            self.leitor = Reconhecedor()
        return self.leitor

    def ler_contexto(self, quadro, tipo):
        leitor = self._carregar_leitor()
        if self._ultimo_texto is not None:
            imagem, contexto, alvo = self._ultimo_texto
            if imagem is quadro and contexto == tipo:
                return alvo
        linha = leitor.ler(quadro, tipo)
        if tipo == "confirmar_sair":
            self.dialogo_ambiguo = getattr(self.leitor, "dialogo_ambiguo", False)
        alvo = Alvo(tipo, 1.0, linha.caixa, None, "texto") if linha else None
        self._ultimo_texto = quadro, tipo, alvo
        return alvo


class Confirmacao:
    def __init__(self):
        self.limpar()

    def limpar(self):
        self.candidato = None
        self.total = 0

    def observar(self, alvo, altura, minimo):
        centro = alvo.centro
        tolerancia = altura * 0.05
        mesmo = (self.candidato is not None and self.candidato[0] == alvo.nome
                 and all(abs(a - b) < tolerancia for a, b in zip(centro, self.candidato[1])))
        self.total = self.total + 1 if mesmo else 1
        self.candidato = alvo.nome, centro
        if self.total < max(1, minimo):
            return False
        self.limpar()
        return True


@dataclass(frozen=True)
class Acao:
    tipo: str
    alvo: Alvo | None = None
    passos: tuple = ()
    tentativa: int = 0


class Fluxo:
    """Adaptação em máquina de estados, avançada pelo chamador a cada captura.

    As coordenadas são relativas à captura. O chamador verifica a janela,
    executa a ação, obtém uma captura nova e fornece a leitura do HUD.
    """

    def __init__(self, detector, rng=None):
        self.detector = detector
        self.config = detector.config
        self.rng = rng if rng is not None else random.Random()
        self.partidas = 0
        self.sobreviventes = Sobreviventes()
        self.regiao = RegiaoLobby()
        self.rota = None
        self.reconhecer_retorno_ao_lobby()

    def reconhecer_retorno_ao_lobby(self):
        """Um aviso de lobby confirmado encerra as ações pendentes da partida anterior."""
        if self.rota is not None:
            self.rota.reiniciar()
        self.sobreviventes.reiniciar()
        if self.regiao.alvo is not None:
            self.regiao = RegiaoLobby()
        self.confirmacao = Confirmacao()
        self.inicio_partida = None
        self._proxima_acao = 0.0
        self._repetir_clique_em = 0.0
        self._prazo_lobby = None
        self._tentativas_fechar_social = 0
        self._prazo_menu = None
        self._repetir_saida_em = 0.0
        self._tentativas_saida_sem_hud = 0
        self._prazo_fila = None
        self._fila_contada = False
        self._fila_expirada = False
        self._quadros_partida = 0
        self._quadros_sem_hud = 0
        self._transicao_hud = False
        self._inicio_sem_sinais = None
        self._inicio_hud_novo = None
        self._fila_bloqueada = False
        self._botoes_fila_clicados = set()
        self._leituras_fila = 0
        self._leituras_cancelamento = 0
        self._proximo_texto_fila = 0.0
        self._sem_leitura_desde = None
        self._aviso_leitura = False
        self._pendente = None
        self._item_resgate = None
        self._tentativas = 0
        self._quadros_sem_alvo = 0
        self._bloqueado = None
        self._humor = None
        self._troca_humor = 0.0
        self._desvio = (0.0, 0.0, 0.0)
        self._sem_pulo_ate = 0.0
        self._quadros_hud_afk = 0
        self._ultimo_hud_em_partida = None
        self._proxima_caminhada = 0.0
        self._proximo_clique_movimento = 0.0
        self._voltas_caminhada = 0
        self._onibus_visivel_no_clique = False
        self._movimento_exige_transicao = False
        self.motivo_anti_afk = "aguardando_hud"
        self.embarque = Embarque()

    def pausar(self):
        self.sobreviventes.reiniciar()
        self.confirmacao.limpar()
        self._quadros_sem_alvo = self._quadros_partida = self._leituras_fila = 0
        self._quadros_sem_hud = 0
        self._transicao_hud = False
        self._inicio_sem_sinais = self._inicio_hud_novo = None
        self._leituras_cancelamento = 0
        self._sem_leitura_desde = None
        self._aviso_leitura = False
        self._quadros_hud_afk = 0
        self._ultimo_hud_em_partida = None
        self.embarque.pausar()
        if self.rota is not None:
            self.rota.pausar()

    def _iniciar_partida(self, agora):
        self.sobreviventes.reiniciar()
        self.inicio_partida = agora
        self._ultimo_hud_em_partida = agora
        self._repetir_saida_em = 0.0
        self._tentativas_saida_sem_hud = 0
        self._desvio = (0.0, 0.0, 0.0)
        self._sem_pulo_ate = 0.0
        self._quadros_hud_afk = 0
        self._proxima_caminhada = agora
        self._proximo_clique_movimento = agora
        self._voltas_caminhada = 0
        self._inicio_hud_novo = None
        self._movimento_exige_transicao = False

    @property
    def navegando_menu(self):
        return self.regiao.alvo is not None or self._prazo_menu is not None or self._prazo_lobby is not None

    @property
    def prazo_saida(self):
        if self.inicio_partida is not None and self.config.sair_por_tempo:
            return self.inicio_partida + self.config.minutos_partida * 60
        return None

    def rearmar_fila(self):
        """Permite uma nova tentativa somente após ação explícita de iniciar."""
        if self._fila_contada and not self._fila_expirada:
            return False
        recuperando = self._movimento_exige_transicao or self._pendente in BOTOES_FILA
        if recuperando:
            self._encerrar_espera_fila()
            if self._pendente in BOTOES_FILA:
                self._pendente, self._tentativas = None, 0
            self._movimento_exige_transicao = False
            self._onibus_visivel_no_clique = False
            self._proxima_acao = 0.0
            self.pausar()
        self._fila_bloqueada = False
        self._tentativas_fechar_social = 0
        if self._bloqueado in BOTOES_FILA:
            self._bloqueado = None
        return recuperando

    def rearmar_apos_foco(self, alvo, agora):
        if alvo is None or alvo in BOTOES_FILA:
            self.rearmar_fila()
            return
        if self._bloqueado == alvo:
            self._bloqueado = None
        self.pausar()
        if alvo == "confirmar_sair":
            self._prazo_lobby = agora + self.config.timeout_lobby
        else:
            self._prazo_menu = agora + 10

    def aguardar_controles_livres(self, agora):
        if self._prazo_menu is not None:
            self._prazo_menu = agora + 10
        if self.regiao.alvo is not None and self.regiao.fechando is None:
            self.regiao._ultima_mudanca = agora
            self.regiao.prazo = max(self.regiao.prazo, agora + 15)

    def _procurar(self, quadro, *nomes):
        return self.detector.procurar(quadro, nomes)

    def _alvo(self, quadro, nome):
        if nome == "resgatar":
            return self.detector.recompensa(quadro)
        if nome in ("jogar", "continuar"):
            alvo = self.detector.detectar(quadro)
            return alvo if alvo and alvo.nome == nome else None
        if nome == "confirmar_sair":
            return self.detector.ler_contexto(quadro, nome)
        return self._procurar(quadro, nome)

    def _clicar_e_verificar(self, alvo, quadro):
        if alvo.nome == self._bloqueado:
            return None
        if self._pendente is None and not self.confirmacao.observar(
                alvo, quadro.shape[0], self.config.confirmacoes):
            return None
        return Acao("clicar", alvo, tentativa=self._tentativas)

    def registrar_clique(self, acao, enviado, agora, *, no_onibus=False):
        """Registra a tentativa quando o Windows aceitou a entrada do clique."""
        self._proxima_acao = agora + (0.45 if enviado else 0.35)
        if not enviado:
            self.pausar()
            return
        self._repetir_clique_em = agora + 1.5
        self._pendente = acao.alvo.nome
        self._tentativas += 1
        self._quadros_sem_alvo = 0
        if self._pendente == "resgatar":
            self._item_resgate = acao.alvo.item
        if self._pendente == "menu_aba" and self._prazo_menu is not None:
            self._prazo_menu = agora + 10
        if self._pendente in BOTOES_FILA:
            self._fila_bloqueada = True
            self._movimento_exige_transicao = True
            self._onibus_visivel_no_clique = no_onibus
            self.embarque.reiniciar()
            if self.rota is not None:
                self.rota.reiniciar()
            self._botoes_fila_clicados.add(self._pendente)
            self._transicao_hud = False
            self._quadros_sem_hud = 0
            self._inicio_sem_sinais = self._inicio_hud_novo = None
            self._quadros_partida = 0

    def _observar_transicao(self, hud_visivel, no_onibus, agora):
        if not self._transicao_hud:
            if hud_visivel or no_onibus:
                self._quadros_sem_hud = 0
                self._inicio_sem_sinais = None
                return
            if self._inicio_sem_sinais is None:
                self._inicio_sem_sinais = agora
            self._quadros_sem_hud += 1
            self._transicao_hud = (
                self._quadros_sem_hud >= 4 and agora - self._inicio_sem_sinais >= 0.65
            )

    def _verificar_clique(self, quadro, agora, hud_visivel, no_onibus, partida=False):
        nome = self._pendente
        abriu_modal = nome == "voltar_lobby" and self.detector.ler_contexto(quadro, "confirmar_sair")
        mudou_de_aba = nome == "menu_aba" and self._procurar(quadro, "voltar_lobby")
        alvo_sumiu = abriu_modal or mudou_de_aba or (partida and nome in BOTOES_LOBBY)
        alvo = None if alvo_sumiu else self._alvo(quadro, nome)
        if nome == "resgatar" and alvo and self._item_resgate and alvo.item:
            if SequenceMatcher(None, self._item_resgate.casefold(), alvo.item.casefold()).ratio() < 0.80:
                alvo = None
        if alvo:
            self._quadros_sem_alvo = 0
        if nome in ("voltar_lobby", "confirmar_sair") and self.detector.dialogo_ambiguo:
            self._quadros_sem_alvo = 0
            return None
        if alvo:
            if agora < self._repetir_clique_em:
                return None
            if self._tentativas < 3:
                return self._clicar_e_verificar(alvo, quadro)
        if not alvo:
            if nome in BOTOES_FILA:
                self._observar_transicao(hud_visivel, no_onibus, agora)
            self._quadros_sem_alvo += 1
            if self._quadros_sem_alvo < 2:
                return None
        self._pendente, self._tentativas = None, 0
        if alvo:
            self._bloqueado = nome
            self._quadros_sem_alvo = 0
            return Acao("clique_ignorado", alvo)
        if nome in BOTOES_FILA:
            self._iniciar_espera_fila(agora)
            return Acao("aguardando_fila")
        elif nome == "voltar_lobby":
            self._prazo_menu = None
            self._prazo_lobby = agora + self.config.timeout_lobby
        elif nome == "resgatar":
            self._item_resgate = None
            return Acao("resgate_confirmado")
        return Acao("clique_confirmado")

    def _iniciar_espera_fila(self, agora):
        self.sobreviventes.reiniciar()
        self.inicio_partida = None
        self._prazo_lobby = None
        self._prazo_fila = agora + self.config.timeout_lobby
        self._fila_contada = self._fila_expirada = False
        self._leituras_cancelamento = 0
        self._quadros_partida = self._leituras_fila = 0
        self._inicio_hud_novo = None
        self._proximo_texto_fila = agora

    def _encerrar_espera_fila(self):
        self._prazo_fila = None
        self._fila_contada = self._fila_expirada = False
        self._botoes_fila_clicados.clear()
        self.confirmacao.limpar()

    def _confirmar_fila(self, quadro, agora, partida, hud_visivel, no_onibus):
        if agora >= self._prazo_fila and not self._fila_expirada:
            self._fila_expirada = True
            return Acao("fila_sem_partida" if self._fila_contada else "partida_nao_confirmada")
        self._observar_transicao(hud_visivel, no_onibus, agora)
        jogo_novo = partida and self._transicao_hud
        self._quadros_partida = self._quadros_partida + 1 if jogo_novo else 0
        if jogo_novo:
            if self._inicio_hud_novo is None:
                self._inicio_hud_novo = agora
        else:
            self._inicio_hud_novo = None
        if (self._quadros_partida >= 3 and self._inicio_hud_novo is not None
                and agora - self._inicio_hud_novo >= 0.35):
            self._iniciar_partida(agora)
            if not self._fila_contada:
                self.partidas += 1
            if self._bloqueado in BOTOES_FILA:
                self._bloqueado = None
            self._fila_bloqueada = False
            self._encerrar_espera_fila()
            return Acao("partida_confirmada")
        if self._fila_contada or self._fila_expirada:
            if not jogo_novo and agora >= self._proximo_texto_fila:
                self._proximo_texto_fila = agora + 1
                cancelada = self.detector.ler_contexto(quadro, "fila_cancelada")
                self._leituras_cancelamento = self._leituras_cancelamento + 1 if cancelada else 0
                if self._leituras_cancelamento >= 2:
                    self._encerrar_espera_fila()
                    self._fila_bloqueada = False
                    return Acao("fila_cancelada")
            return None
        if not jogo_novo and agora >= self._proximo_texto_fila:
            self._proximo_texto_fila = agora + 1
            fila = self.detector.ler_contexto(quadro, "fila")
            self._leituras_fila = self._leituras_fila + 1 if fila else 0
            if self._leituras_fila >= 2:
                self.partidas += 1
                self._fila_contada = True
                self.confirmacao.limpar()
                return Acao("fila_confirmada")
        if not self._fila_bloqueada and not (self._leituras_fila or partida):
            botoes = tuple(nome for nome in BOTOES_LOBBY if nome not in self._botoes_fila_clicados)
            alvo = self._procurar(quadro, *botoes)
            if alvo:
                return self._clicar_e_verificar(alvo, quadro)
        self.confirmacao.limpar()
        return None

    def _mover_no_ar(self, quadro, agora, autorizado):
        if not autorizado or agora < self.embarque.proximo_movimento:
            return None
        if self.detector.dialogo_ambiguo or self._procurar(
                quadro, "jogar", "continuar", "inventario", "voltar_lobby", *BOTOES_LOBBY):
            return None
        direcao = SEQUENCIA_AEREA[self.embarque.voltas_movimento % len(SEQUENCIA_AEREA)]
        codigos = tuple(TECLAS[tecla] for tecla in direcao)
        self.motivo_anti_afk = "movimento_aereo"
        return Acao("movimento_aereo", passos=(("teclas", codigos, 0.8),))

    def _seguir_rota(self, quadro, agora, *, onibus=False, hud=False, asa=False):
        if self.rota is None:
            from rota_baus import RotaBaus
            self.rota = RotaBaus()
        self.motivo_anti_afk = "rota_baus"
        if onibus:
            ordem = self.rota.no_onibus(quadro, agora)
        else:
            ordem = self.rota.fora_do_onibus(quadro, self.detector._carregar_leitor(), agora, hud=hud, asa=asa)
        return Acao(ordem.tipo, passos=ordem.passos) if ordem else None

    @staticmethod
    def _acao_regiao(ordem, alvo):
        if ordem is None:
            return None
        if ordem.tecla:
            origem = Alvo(f"regiao_{ordem.etapa}", 1.0, (0, 0, 0, 0), None)
            return Acao("tecla_regiao", origem, passos=(("teclas", (ordem.tecla,), 0.07),))
        if ordem.caixa:
            return Acao(ordem.tipo, Alvo(f"regiao_{ordem.etapa}", 1.0, ordem.caixa, None),
                        tentativa=ordem.tentativa)
        if ordem.tipo == "esc":
            return Acao("esc", Alvo(f"regiao_{ordem.etapa}", 1.0, (0, 0, 0, 0), None))
        return Acao(f"regiao_{ordem.tipo}", Alvo(alvo, 1.0, (0, 0, 0, 0), None))

    def passo(self, quadro, agora, *, partida=False, foco=True, cursor=False, no_onibus=False,
              hud_visivel=None, asa_delta=False, espectando=False, derrubado=False):
        """Recebe imagem BGR e tempo monotônico em segundos. Retorna Acao ou None."""
        self.motivo_anti_afk = "outro_fluxo_ativo" if self.config.anti_afk else "desativado"
        if not foco:
            self.motivo_anti_afk = "fortnite_sem_foco"
            self.pausar()
            return None
        hud_visivel = partida if hud_visivel is None else hud_visivel
        if self.rota is not None and not self.config.rota_baus:
            self.rota = None
        sem_controle = espectando or derrubado
        observar_sobreviventes = (self.config.sair_quando_sozinho and partida and hud_visivel
                                  and not (sem_controle or no_onibus or asa_delta or self.navegando_menu
                                           or self._movimento_exige_transicao or self._prazo_fila is not None
                                           or self.embarque.em_transicao(agora)))
        if not observar_sobreviventes:
            self.sobreviventes.interromper()
        if cursor or no_onibus or asa_delta or sem_controle:
            self._ultimo_hud_em_partida = None
        elif partida and self.inicio_partida is not None:
            self._ultimo_hud_em_partida = agora
        hud_recente = (not partida and self._ultimo_hud_em_partida is not None
                       and agora - self._ultimo_hud_em_partida <= TOLERANCIA_HUD)
        if agora < self._proxima_acao:
            self.motivo_anti_afk = "intervalo_entre_acoes"
            return None
        if self.regiao.alvo is not None and not (partida or no_onibus or asa_delta or sem_controle):
            self.motivo_anti_afk = "ajustando_regiao"
            if not self.config.regiao_auto and self.regiao.fechando is None:
                self.regiao.cancelar(agora)
            if not self.regiao.pronto(agora):
                return None
            alvo_regiao = self.regiao.alvo
            tela = self.detector.tela_regiao(quadro)
            lobby = not (tela.social or tela.configuracoes) and bool(self._procurar(quadro, *BOTOES_LOBBY))
            return self._acao_regiao(self.regiao.passo(tela, agora, lobby=lobby), alvo_regiao)
        if not (partida or hud_recente) or no_onibus:
            self._quadros_hud_afk = 0
            if self._prazo_fila is None:
                self._inicio_hud_novo = None
        onibus_novo = no_onibus and (not self._onibus_visivel_no_clique or self._transicao_hud)
        saiu_do_onibus = self.embarque.saltou_em is not None and not no_onibus
        descendo = saiu_do_onibus and self.embarque.em_transicao(agora)
        controle_aereo = (descendo and self.config.rotina_aerea and not self.config.rota_baus
                          and self.config.anti_afk and not cursor and not sem_controle
                          and self._prazo_menu is None and self._prazo_lobby is None)
        aereo_autorizado = onibus_novo or asa_delta or self.embarque.saltou_em is not None
        replay_na_tela = ((no_onibus or asa_delta) and self._pendente is None and self._prazo_fila is None
                          and self._procurar(quadro, "jogar", "continuar"))
        pode_rota = self.config.rota_baus and not (sem_controle or replay_na_tela or self.navegando_menu)
        if pode_rota and onibus_novo and not (cursor and self._procurar(quadro, "inventario", "voltar_lobby")):
            salto = self._seguir_rota(quadro, agora, onibus=True)
            if salto or (self._pendente is None and self._prazo_fila is None):
                return salto
        if (self.config.rotina_aerea and not self.config.rota_baus and aereo_autorizado and not sem_controle and not replay_na_tela
                and self._prazo_menu is None and self._prazo_lobby is None):
            fase = self.embarque.observar(onibus=onibus_novo, asa=asa_delta)
            if fase:
                self.motivo_anti_afk = "rotina_aerea"
                self._proxima_acao = agora + (0.25 if fase == "saltar_onibus" else 1)
                return Acao(fase, passos=ACOES_AEREAS[fase])
        else:
            self.embarque.pausar()
        if (onibus_novo or asa_delta) and not replay_na_tela and self._pendente is None and self._prazo_fila is None:
            if pode_rota and asa_delta and not cursor:
                return self._seguir_rota(quadro, agora, hud=partida, asa=True)
            self.motivo_anti_afk = "aviso_de_onibus" if onibus_novo else "asa_delta"
            return self._mover_no_ar(quadro, agora, controle_aereo)
        if saiu_do_onibus and partida and not sem_controle and not cursor:
            if (descendo and self.inicio_partida is None and self._pendente is None and self._prazo_fila is None
                    and not self.navegando_menu and not replay_na_tela):
                # O clique pode ter ficado sem confirmação mesmo quando o jogo
                # iniciou a partida que este salto, seguido do HUD, comprova.
                self._iniciar_espera_fila(agora)
            if self._pendente in BOTOES_FILA or self._prazo_fila is not None:
                self._transicao_hud = True
        if self._pendente:
            self.motivo_anti_afk = "verificando_clique"
            acao = self._verificar_clique(quadro, agora, hud_visivel, no_onibus,
                                         partida or onibus_novo or asa_delta)
            if not acao and pode_rota and (asa_delta or descendo) and not (no_onibus or cursor):
                return self._seguir_rota(quadro, agora, hud=partida, asa=asa_delta)
            return acao or self._mover_no_ar(quadro, agora, controle_aereo)
        if self._bloqueado and self._alvo(quadro, self._bloqueado) is None:
            self._quadros_sem_alvo += 1
            if self._quadros_sem_alvo >= 2:
                self._bloqueado = None
        elif self._bloqueado:
            self._quadros_sem_alvo = 0
        if self._prazo_fila is not None:
            self.motivo_anti_afk = "aguardando_transicao_da_fila"
            acao = self._confirmar_fila(quadro, agora, partida, hud_visivel, no_onibus)
            if not acao and pode_rota and (asa_delta or descendo) and not (no_onibus or cursor):
                return self._seguir_rota(quadro, agora, hud=partida, asa=asa_delta)
            return acao or self._mover_no_ar(quadro, agora, controle_aereo)
        self._botoes_fila_clicados.clear()
        if self._prazo_menu is not None:
            self.motivo_anti_afk = "aguardando_menu_de_saida"
            alvo = self._procurar(quadro, "voltar_lobby") or self._procurar(quadro, "menu_aba")
            if alvo:
                if acao := self._clicar_e_verificar(alvo, quadro):
                    return acao
                if alvo.nome != self._bloqueado and agora < self._prazo_menu + 5:
                    return None
            if agora >= self._prazo_menu:
                self._prazo_menu = None
                self._repetir_saida_em = agora + 5
                return Acao("menu_nao_encontrado")
            self.confirmacao.limpar()
            return None

        if self.detector.dialogo_ambiguo or (self._prazo_lobby is not None and agora < self._prazo_lobby):
            alvo = self.detector.ler_contexto(quadro, "confirmar_sair")
            if self.detector.dialogo_ambiguo:
                self.confirmacao.limpar()
                return None
            if alvo:
                return self._clicar_e_verificar(alvo, quadro)
        alvo = None
        if (self.config.resgatar_recompensas and not (partida or sem_controle or no_onibus or asa_delta)
                and (cursor or self._prazo_lobby is not None)):
            alvo = self.detector.recompensa(quadro)
        alvo = alvo or self.detector.detectar(quadro)
        if alvo:
            if alvo.nome == "jogar":
                self._ultimo_hud_em_partida = None
            self._sem_leitura_desde = None
            self._aviso_leitura = False
            return self._clicar_e_verificar(alvo, quadro)

        if self._prazo_lobby is not None and agora >= self._prazo_lobby:
            self._prazo_lobby = None
            return Acao("lobby_nao_encontrado")
        if not (self._fila_bloqueada or partida or sem_controle or no_onibus or asa_delta):
            alvo = self._procurar(quadro, *BOTOES_LOBBY)
        if alvo:
            self.inicio_partida = None
            self.sobreviventes.reiniciar()
            self._sem_leitura_desde = None
            self._aviso_leitura = False
            if self.detector.painel_social_lobby(quadro):
                self.confirmacao.limpar()
                self.motivo_anti_afk = "fechando_painel_social"
                if self._tentativas_fechar_social >= 3:
                    if self._tentativas_fechar_social == 3:
                        self._tentativas_fechar_social += 1
                        return Acao("painel_social_nao_fechou")
                    return None
                self._tentativas_fechar_social += 1
                self._proxima_acao = agora + 1.5
                return Acao("esc", replace(alvo, nome="lobby_fechar_social"))
            self._tentativas_fechar_social = 0
            acao = self._clicar_e_verificar(alvo, quadro)
            if acao and self.config.regiao_auto:
                preferida = regiao_por_horario(self.config.regiao_manha, self.config.regiao_noite)
                ordem = self.regiao.iniciar(preferida, agora, perfil=self.detector.perfil_lobby(quadro))
                if ordem:
                    self.motivo_anti_afk = "ajustando_regiao"
                    return self._acao_regiao(ordem, preferida)
            return acao
        self._tentativas_fechar_social = 0
        if self._prazo_lobby is not None or not partida:
            alvo = self._procurar(quadro, "aba_jogar")
            if alvo:
                return self._clicar_e_verificar(alvo, quadro)
        if self._prazo_lobby is not None:
            self.motivo_anti_afk = "aguardando_lobby"
            self.confirmacao.limpar()
            return None
        self.confirmacao.limpar()
        inventario_aberto = bool((partida or hud_recente) and self._procurar(quadro, "inventario"))
        if inventario_aberto:
            self._quadros_hud_afk = 0
            self._inicio_hud_novo = None
        jogo_visivel = partida or sem_controle
        if jogo_visivel:
            self._tentativas_saida_sem_hud = 0
        prazo_saida = self.prazo_saida
        tempo_esgotado = prazo_saida is not None and agora >= prazo_saida
        motivo_sobreviventes = None
        if inventario_aberto:
            self.sobreviventes.interromper()
        elif observar_sobreviventes and not tempo_esgotado:
            motivo_sobreviventes = self.sobreviventes.verificar(quadro, agora)
        if ((tempo_esgotado or motivo_sobreviventes) and agora >= self._repetir_saida_em
                and not (inventario_aberto or no_onibus or asa_delta)):
            alvo_saida = self._procurar(quadro, "voltar_lobby") if cursor else None
            if cursor and jogo_visivel and alvo_saida is None:
                alvo_saida = self._procurar(quadro, "menu_aba")
            pode_tentar_sem_hud = not cursor and self._tentativas_saida_sem_hud < 2
            if alvo_saida or jogo_visivel or pode_tentar_sem_hud:
                self.motivo_anti_afk = motivo_sobreviventes or (
                    "saida_por_tempo" if jogo_visivel else "saida_por_tempo_sem_hud")
                self._prazo_menu = agora + 10
                if alvo_saida:
                    return self._clicar_e_verificar(alvo_saida, quadro)
                if not jogo_visivel:
                    self._tentativas_saida_sem_hud += 1
                self._proxima_acao = agora + 1
                return Acao("esc")
        if jogo_visivel:
            self._sem_leitura_desde = None
            self._aviso_leitura = False
        elif self._sem_leitura_desde is None:
            self._sem_leitura_desde = agora
        elif not self._aviso_leitura and agora - self._sem_leitura_desde >= self.config.timeout_lobby:
            self._aviso_leitura = True
            return Acao("tela_nao_reconhecida")
        if self.config.rota_baus:
            if (pode_rota and (partida or descendo) and not cursor and not inventario_aberto
                    and not self._movimento_exige_transicao and not self.detector.dialogo_ambiguo
                    and not self.detector.procurar(quadro, ("voltar_lobby",), limiar=.92)):
                return self._seguir_rota(quadro, agora, hud=partida, asa=asa_delta)
            if self.rota is not None:
                self.rota.pausar()
            return None
        if not self.config.anti_afk:
            self.motivo_anti_afk = "desativado"
            return None
        if controle_aereo:
            return self._mover_no_ar(quadro, agora, controle_aereo)
        if not (partida or hud_recente) or no_onibus or asa_delta or sem_controle:
            self.motivo_anti_afk = ("derrubado" if derrubado else "espectando" if espectando
                                   else "aviso_de_onibus" if no_onibus
                                   else "asa_delta" if asa_delta else "hud_indisponivel")
            return None
        if self._movimento_exige_transicao:
            self.motivo_anti_afk = "aguardando_transicao_da_fila"
            return None
        if descendo and cursor:
            self.motivo_anti_afk = "cursor_visivel"
            return None
        # O ícone do menu isolado também pode aparecer junto do HUD de jogo.
        # Exige o controle de saída, com limiar maior se não houver cursor.
        if self.detector.procurar(quadro, ("voltar_lobby",), limiar=None if cursor else 0.92):
            self._quadros_hud_afk = 0
            self._inicio_hud_novo = None
            self.motivo_anti_afk = "menu_de_saida"
            return Acao("movimento_menu")
        if inventario_aberto:
            self.motivo_anti_afk = "inventario_aberto"
            return None
        self._quadros_hud_afk += 1
        if self.inicio_partida is None and self._inicio_hud_novo is None:
            self._inicio_hud_novo = agora
        if (self._quadros_hud_afk < 2 or
                (self.inicio_partida is None and agora - self._inicio_hud_novo < 0.35)):
            self.motivo_anti_afk = "confirmando_hud"
            return None
        if partida and not cursor:
            self._ultimo_hud_em_partida = agora
        self.motivo_anti_afk = "movimento_planejado"
        transicao_aerea = self.embarque.em_transicao(agora)
        camera_livre = not cursor and not transicao_aerea
        if agora >= self._proxima_caminhada:
            x, y, rumo = self._desvio
            teclas = ("W", "WA", "W", "WD")[self._voltas_caminhada % 4]
            self._voltas_caminhada += 1
            duracao = DURACAO_CAMINHADA
            x, y = _andar(x, y, rumo, teclas, duracao)
            codigos = tuple(TECLAS[tecla] for tecla in teclas)
            if camera_livre:
                giro = self.rng.choice((-1, 1)) * self.rng.randint(350, 650)
                passos = [("andar_mouse", codigos, duracao, giro, self.rng.randint(-65, 80))]
                rumo = _angulo(rumo + giro * 0.03)
            else:
                passos = [("teclas", codigos, duracao)]
            if camera_livre and agora >= self._proximo_clique_movimento:
                passos += [("clique", 0.18), ("pausa", 0.32), ("clique", 0.18)]
                self._proximo_clique_movimento = agora + 5.0
            self._desvio = x, y, rumo
            self._proxima_caminhada = agora + 2.6
            total = sum(p[2] if p[0] in ("teclas", "andar_mouse") else p[-1] for p in passos)
            self._proxima_acao = agora + total + 0.12
            return Acao("movimento", passos=tuple(passos))
        if self._humor is None or agora >= self._troca_humor:
            self._humor = self.rng.choice([h for h in HUMORES if h != self._humor])
            self._troca_humor = agora + self.rng.uniform(20, 90)
        passos, self._desvio = plano_anti_afk(
            self._desvio, camera_livre, self._humor, not transicao_aerea and agora >= self._sem_pulo_ate,
            rng=self.rng,
        )
        duracao = sum(p[2] if p[0] in ("teclas", "andar_mouse") else p[-1] for p in passos)
        if any(p[0] == "teclas" and 57 in p[1] for p in passos):
            self._sem_pulo_ate = agora + duracao + 1.2
        self._proxima_acao = agora + duracao + intervalo_anti_afk(0.2, self._humor, rng=self.rng)
        return Acao("movimento", passos=tuple(passos))


VETORES = {"W": (0, 1), "A": (-1, 0), "D": (1, 0)}
PESOS = {"W": 3.0, "A": 1.0, "D": 1.0}
TIPOS = ("explorar", "andar", "camera", "pular", "agachar", "ajeitar")
HUMORES = {
    "calmo": ((6, 1.5, 1.5, 0.5, 0.4, 0.3), 1.35),
    "andando": ((9, 1.5, 1, 1, 0.6, 0.5), 0.9),
    "inquieto": ((5, 2, 1.5, 2.5, 3, 3), 0.65),
}


def _rodar(tecla, rumo):
    x, y = VETORES[tecla]
    radianos = math.radians(rumo)
    seno, cosseno = math.sin(radianos), math.cos(radianos)
    return x * cosseno + y * seno, y * cosseno - x * seno


def _andar(x, y, rumo, teclas, duracao):
    fator = 1.0 if len(teclas) == 1 else 0.7
    for tecla in teclas:
        dx, dy = _rodar(tecla, rumo)
        x += dx * duracao * fator
        y += dy * duracao * fator
    return x, y


def _direcao(x, y, rumo, rng):
    distancia = math.hypot(x, y)
    pesos = []
    for tecla in VETORES:
        dx, dy = _rodar(tecla, rumo)
        volta = -(dx * x + dy * y) / distancia if distancia > 0.01 else 0
        pesos.append(PESOS[tecla] * max(0.15, 1 + volta * min(2 * distancia / 10, 3)))
    return rng.choices(list(VETORES), weights=pesos)[0]


def _diagonal(tecla, rng):
    return "W" + rng.choice("AD") if tecla == "W" and rng.random() < 0.4 else tecla


def _angulo(graus):
    return (graus + 180) % 360 - 180


def plano_anti_afk(desvio, camera=True, humor="andando", pular=True, *, rng=random):
    """Devolve passos (scancodes e movimentos relativos) e o novo desvio estimado."""
    x, y, rumo = desvio
    passos = []
    n = rng.choices((1, 2, 3), weights=(5, 3, 1))[0]
    for i in range(n):
        pesos = [
            0 if (tipo == "camera" and not camera)
            or (tipo == "pular" and (i > 0 or not pular)) else peso
            for tipo, peso in zip(TIPOS, HUMORES[humor][0])
        ]
        tipo = rng.choices(TIPOS, weights=pesos)[0]
        if tipo in ("explorar", "andar"):
            longa = tipo == "explorar"
            if longa:
                duracao = min(2.1, max(0.8, rng.lognormvariate(math.log(1.5), 0.45)))
                correr = rng.random() < 0.45
            else:
                duracao = min(1.4, max(0.1, rng.lognormvariate(math.log(0.45), 0.5)))
                correr = False
            direcao = _direcao(x, y, rumo, rng)
            teclas = _diagonal("W", rng) if direcao == "W" or longa and camera else "W" + direcao
            codigos = [TECLAS[t] for t in teclas] + [42] * correr
            if longa and camera:
                if math.hypot(x, y) > 10:
                    volta = _angulo(math.degrees(math.atan2(-x, -y)) - rumo)
                    giro = max(-120, min(120, volta)) + rng.gauss(0, 15)
                else:
                    giro = max(-90, min(90, rng.gauss(0, 40)))
                mx = round(giro / 0.03)
                x, y = _andar(x, y, rumo + giro * 0.7, teclas, duracao)
                rumo = _angulo(rumo + mx * 0.03)
                passos.append(("andar_mouse", codigos, duracao, mx, rng.randint(-60, 60)))
            else:
                x, y = _andar(x, y, rumo, teclas, duracao)
                passos.append(("teclas", codigos, duracao))
            if longa:
                break
        elif tipo == "camera":
            mx, my = rng.choice((-1, 1)) * rng.randint(400, 1300), rng.randint(-80, 80)
            passos.append(("mouse", mx, my, rng.uniform(0.12, 0.4)))
            sorte = rng.random()
            if sorte < 0.25:
                fator = rng.uniform(0.08, 0.18)
                passos.append(("pausa", rng.uniform(0.05, 0.2)))
                passos.append(("mouse", -round(mx * fator), -round(my * fator), rng.uniform(0.06, 0.15)))
                mx -= round(mx * fator)
            elif sorte < 0.5:
                volta = -round(mx * rng.uniform(0.6, 1.4))
                passos.append(("pausa", rng.uniform(0.15, 0.35)))
                passos.append(("mouse", volta, -my, rng.uniform(0.15, 0.4)))
                mx += volta
            rumo = _angulo(rumo + mx * 0.03)
        elif tipo == "pular":
            passos.append(("teclas", [57], rng.uniform(0.05, 0.15)))
        elif tipo == "ajeitar":
            primeira, segunda = rng.choice((("A", "D"), ("D", "A")))
            passos.append(("teclas", [TECLAS[primeira]], rng.uniform(0.06, 0.18)))
            passos.append(("pausa", rng.uniform(0.03, 0.1)))
            passos.append(("teclas", [TECLAS[segunda]], rng.uniform(0.06, 0.18)))
        else:
            passos.append(("teclas", [29], rng.uniform(0.05, 0.12)))
            passos.append(("pausa", rng.uniform(0.15, 0.35)))
            passos.append(("teclas", [29], rng.uniform(0.05, 0.12)))
        if i < n - 1:
            passos.append(("pausa", rng.uniform(0.1, 0.35)))
    return passos, (x, y, rumo)


def intervalo_anti_afk(base, humor, *, rng=random):
    sigma = 0.35
    intervalo = base * HUMORES[humor][1] * rng.lognormvariate(-(sigma ** 2) / 2, sigma)
    if rng.random() < 0.2:
        intervalo *= rng.uniform(0.3, 0.5)
    return min(0.35, max(0.1, intervalo))
