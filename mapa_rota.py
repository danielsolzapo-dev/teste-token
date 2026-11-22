"""Localiza o minimapa na imagem da ilha e lê os avisos de navegação do HUD."""

from dataclasses import dataclass
from pathlib import Path
import re

import cv2
import numpy as np

from texto_tela import normalizar


ILHA = Path(__file__).resolve().parent / "templates" / "rota_baus" / "ilha.jpg"


@dataclass(frozen=True)
class Ponto:
    x: int
    y: int
    nome: str
    procurar: bool = False


# Coordenadas na referência de 751 × 720. O caminho contorna a enseada por terra.
ROTA = (
    Ponto(630, 202, "Ponta leste", True),
    Ponto(606, 215, "Faixa de areia"),
    Ponto(584, 221, "Praia norte"),
    Ponto(553, 225, "Construção na praia norte", True),
    Ponto(531, 233, "Entrada da enseada"),
    Ponto(524, 250, "Curva da enseada"),
    Ponto(532, 271, "Praia sul"),
    Ponto(545, 282, "Construção na praia sul", True),
)
# Pequenos percursos de procura ao redor das construções, todos em terra.
VIZINHANCA = {
    0: ((626, 202), (630, 205)),
    3: ((549, 223), (556, 226)),
    7: ((541, 280), (548, 280)),
}


def previa_rota():
    ilha = cv2.imread(str(ILHA))
    if ilha is None:
        raise ValueError("A imagem da ilha está indisponível.")
    x, y, zoom = 493, 175, 3
    regiao = cv2.resize(ilha[y:306, x:660], None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
    pontos = np.array([[(p.x - x)*zoom, (p.y - y)*zoom] for p in ROTA], np.int32)
    cv2.polylines(regiao, [pontos], False, (35, 32, 26), 6, cv2.LINE_AA)
    cv2.polylines(regiao, [pontos], False, (255, 249, 218), 2, cv2.LINE_AA)
    numero = 0
    for ponto, centro in zip(ROTA, pontos):
        if ponto.procurar:
            numero += 1
            px, py = map(int, centro)
            cv2.circle(regiao, (px, py), 12, (37, 36, 29), -1, cv2.LINE_AA)
            cv2.circle(regiao, (px, py), 12, (255, 249, 218), 2, cv2.LINE_AA)
            cv2.putText(regiao, str(numero), (px - 5, py + 6), cv2.FONT_HERSHEY_SIMPLEX,
                        .52, (255, 255, 255), 2, cv2.LINE_AA)
    return regiao


@dataclass(frozen=True)
class Posicao:
    x: float
    y: float
    lado: int
    confianca: float


def _terreno(imagem):
    matiz, saturacao, _ = cv2.split(cv2.cvtColor(imagem, cv2.COLOR_BGR2HSV))
    agua = (matiz >= 80) & (matiz <= 124) & (saturacao > 53)
    vegetacao = (matiz >= 26) & (matiz < 80) & (saturacao > 54)
    terra = ((matiz < 26) | (matiz > 168)) & (saturacao > 60)
    canais = np.stack((agua, vegetacao, terra), axis=-1).astype(np.float32)
    canais *= (1, .64, .48)
    return cv2.GaussianBlur(canais, (5, 5), 1)


def recortar_minimapa(quadro):
    h, w = quadro.shape[:2]
    lado, margem = round(h / 6), round(h / 45)
    return quadro[margem:margem + lado, w - margem - lado:w - margem]


class Localizador:
    def __init__(self):
        ilha = cv2.imread(str(ILHA))
        if ilha is None or ilha.shape[:2] != (720, 751):
            raise ValueError("A imagem da ilha da rota de baús está ausente ou inválida.")
        self.detalhe = _terreno(ilha)
        self.atlas = cv2.resize(self.detalhe, (376, 360), interpolation=cv2.INTER_AREA)
        self.anterior = None

    def localizar(self, quadro):
        mini = recortar_minimapa(quadro)
        if mini.size == 0:
            return None
        amostra = _terreno(cv2.resize(mini, (120, 120), interpolation=cv2.INTER_AREA))
        if amostra.std(axis=(0, 1)).max() < .10:
            self.anterior = None
            return None
        atlas = self.atlas
        x0 = y0 = 0
        if self.anterior:
            p = self.anterior
            lados = sorted({round(p.lado * k / 2) for k in (.91, .96, 1, 1.04, 1.09)})
            margem = max(lados) // 2 + 22
            x, y = round(p.x / 2), round(p.y / 2)
            x0, y0 = max(0, x - margem), max(0, y - margem)
            atlas = atlas[y0:y + margem + 1, x0:x + margem + 1]
        else:
            lados = (20, 26, 33, 41, 49, 57, 62, 66, 71, 77, 85, 96, 109, 125, 145)
        candidatos = []
        for lado in lados:
            if lado < 10 or lado > min(atlas.shape[:2]):
                continue
            molde = cv2.resize(amostra, (lado, lado), interpolation=cv2.INTER_AREA)
            mapa = cv2.matchTemplate(atlas, molde, cv2.TM_CCOEFF_NORMED)
            for _ in range(2):
                _, nota, _, (x, y) = cv2.minMaxLoc(mapa)
                candidatos.append(Posicao(2 * (x + x0) + lado, 2 * (y + y0) + lado, 2 * lado, nota))
                margem = max(7, lado // 5)
                mapa[max(0, y - margem):y + margem + 1, max(0, x - margem):x + margem + 1] = -1
        self.anterior = None
        if not candidatos:
            return None
        melhor = max(candidatos, key=lambda p: p.confianca)
        raio = max(15, melhor.lado * .17)
        segunda = max((p.confianca for p in candidatos if np.hypot(p.x - melhor.x, p.y - melhor.y) > raio),
                      default=0.)
        if melhor.confianca >= .78 and melhor.confianca - segunda >= .035:
            self.anterior = self._refinar(amostra, melhor)
        return self.anterior

    def _refinar(self, amostra, melhor):
        h, w = self.detalhe.shape[:2]
        candidatos = []
        for lado in (round(.97 * melhor.lado), melhor.lado, round(1.03 * melhor.lado)):
            x0, y0 = max(0, round(melhor.x - lado / 2) - 4), max(0, round(melhor.y - lado / 2) - 4)
            faixa = self.detalhe[y0:min(h, y0 + lado + 9), x0:min(w, x0 + lado + 9)]
            if min(faixa.shape[:2]) < lado:
                continue
            molde = cv2.resize(amostra, (lado, lado), interpolation=cv2.INTER_LINEAR)
            _, nota, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(faixa, molde, cv2.TM_CCOEFF_NORMED))
            candidatos.append(Posicao(x0 + x + lado/2, y0 + y + lado/2, lado, nota))
        refinada = max(candidatos, key=lambda p: p.confianca, default=melhor)
        return refinada if refinada.confianca >= melhor.confianca - .05 else melhor


def rumo_bussola(quadro, leitor):
    h, w = quadro.shape[:2]
    meio = w // 2
    raio = round(h * .022)
    faixa = quadro[round(h * .005):round(h * .031), meio - raio:meio + raio]
    ampliada = cv2.resize(faixa, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    cardeais = {"N": 0, "NE": 45, "L": 90, "E": 90, "SE": 135, "S": 180,
                "SO": 225, "SW": 225, "O": 270, "W": 270, "NO": 315, "NW": 315}
    for linha in leitor.linhas(ampliada):
        if abs(linha.centro[0] - ampliada.shape[1] / 2) > ampliada.shape[1] * .25:
            continue
        valor = normalizar(linha.texto).strip()
        if re.fullmatch(r"\d{1,3}", valor) and int(valor) <= 360:
            return int(valor) % 360
        if valor in cardeais:
            return cardeais[valor]
    return None


def aviso_abrir_asa(quadro, leitor):
    h, w = quadro.shape[:2]
    faixa = quadro[round(.43*h):round(.66*h), round(.5*w):round(.89*w)]
    texto = " ".join(normalizar(l.texto) for l in leitor.linhas(faixa))
    return bool(re.search(r"\b(?:PARA (?:ABRIR|ACIONAR|ATIVAR) (?:A )?ASA DELTA|TO DEPLOY(?: GLIDER)?)\b", texto))


def tecla_de_bau(quadro, leitor):
    """Confere o aviso ao alcance. Um 'Abrir' isolado também pode ser de uma porta."""
    h, w = quadro.shape[:2]
    area = quadro[round(h*.30):round(h*.72), round(w*.34):round(w*.79)]
    linhas = leitor.linhas(area)
    for linha in linhas:
        texto = normalizar(linha.texto)
        m = re.fullmatch(r"(?:(?:HOLD|SEGURE|MANTER) )?(?:([A-Z]) )?"
                         r"(?:VASCULHAR|VASCULHAR BAU|SAQUEAR BAU|ABRIR (?:O )?BAU|"
                         r"REVISTAR|SEARCH(?: CHEST)?|OPEN CHEST)", texto)
        if not m:
            continue
        tecla = m[1]
        if not tecla:
            x, y, _, altura = linha.caixa
            proximas = [normalizar(l.texto) for l in linhas if 0 <= x - (l.caixa[0] + l.caixa[2]) <= 2.5*altura
                        and abs(l.centro[1] - linha.centro[1]) <= altura*.7]
            tecla = next((t for t in proximas if re.fullmatch("[A-Z]", t)), "E")
        linha_teclas = "QWERTYUIOP ASDFGHJKL ZXCVBNM".split()
        for letras, inicio in zip(linha_teclas, (16, 30, 44)):
            if tecla in letras:
                return inicio + letras.index(tecla)
    return None
