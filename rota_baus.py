"""Salto, aproximação e procura de baús guiados pelo minimapa visível."""

from collections import deque
from dataclasses import dataclass
import math

from mapa_rota import Localizador, Ponto, ROTA, VIZINHANCA, aviso_abrir_asa, rumo_bussola, tecla_de_bau


@dataclass(frozen=True)
class Comando:
    tipo: str
    passos: tuple


def tecla(tipo, codigo, tempo):
    return Comando(tipo, (("teclas", (codigo,), tempo),))


def distancia(p, alvo):
    return math.hypot(p.x - alvo.x, p.y - alvo.y)


def diferenca(alvo, atual):
    return (alvo - atual + 180) % 360 - 180


class RotaBaus:
    def __init__(self, localizador=None):
        self.mapa = localizador if localizador is not None else Localizador()
        self.reiniciar()

    def reiniciar(self):
        self.fase = "aguardando"
        self.indice = 0
        self.subponto = 0
        self.status = "Rota armada · aguardando o ônibus"
        self.ultimo_onibus = None
        self.saltos = 0
        self.ultimo_espaco = -100.
        self.ultimo_aereo = None
        self.ganho_mouse = 5.
        self.pausar()

    def pausar(self):
        self.proxima = self.proxima_asa = 0.
        self.abrir_asa = False
        self.amostra = self.pos = self.giro = None
        self.giros_sem_resposta = 0
        self.mapa.anterior = None
        self.onibus = deque(maxlen=12)
        self._limpar_busca()
        self.melhor_distancia = math.inf
        self.progresso = None
        self.desvios = 0

    def _limpar_busca(self):
        self.busca_desde = self.rumo_busca = None
        self.olhares = self.tentativas_bau = self.leituras_tecla = 0
        self.tecla_vista = None
        self.esperando_bau = False
        self.bau_ignorado = None

    def _localizar(self, quadro, agora):
        nova = self.mapa.localizar(quadro)
        anterior, self.amostra = self.amostra, (nova, agora) if nova else None
        self.pos = None
        if nova and anterior:
            ultima, instante = anterior
            dt = agora - instante
            if 0 < dt <= 5 and distancia(nova, ultima) <= 9 + 16 * dt:
                self.pos = nova
        if self.pos is None:
            self.giro = None
            self.status = "Rota · localizando o personagem no minimapa"
        return self.pos

    def no_onibus(self, quadro, agora):
        if self.fase != "onibus":
            self.reiniciar()
            self.fase = "onibus"
        self.ultimo_onibus = agora
        if agora < self.proxima:
            return None
        self.proxima = agora + .35
        pos = self._localizar(quadro, agora)
        if pos is None:
            self.onibus.clear()
            return None
        self.status = "No ônibus · calculando o salto para a ponta leste"
        self.onibus.append((agora, pos))
        t, inicial = self.onibus[0]
        intervalo = agora - t
        alvo = ROTA[0]
        perto = distancia(pos, alvo) <= 62
        passou = False
        if intervalo >= 1 and distancia(pos, inicial) >= 3:
            vx, vy = (pos.x - inicial.x) / intervalo, (pos.y - inicial.y) / intervalo
            ate_o_ponto_mais_proximo = ((alvo.x - pos.x) * vx + (alvo.y - pos.y) * vy) / (vx*vx + vy*vy)
            passou = ate_o_ponto_mais_proximo <= 1.5
        if (perto or passou) and self.saltos < 3 and agora - self.ultimo_espaco >= 1.4:
            return tecla("saltar_onibus", 57, .16)
        return None

    def registrar(self, tipo, enviado, agora):
        if not enviado:
            self.pausar()
        elif tipo == "saltar_onibus" and self.fase == "onibus":
            self.saltos += 1
            self.ultimo_espaco = agora
        elif tipo in ("rota_abrir_asa", "rota_fechar_asa"):
            self.ultimo_espaco = agora
            self.proxima_asa = 0.
        elif tipo == "rota_vasculhar":
            self.tentativas_bau += 1
            self.esperando_bau = True
            self.leituras_tecla = 0

    def _proximo_ponto(self):
        self.indice += 1
        self.subponto = 0
        self.melhor_distancia = math.inf
        self.progresso = None
        self.desvios = 0
        self._limpar_busca()
        if self.indice == len(ROTA):
            self.fase = "concluida"
            self.status = "Rota concluída · aguardando a próxima partida"

    def _proxima_busca(self):
        if self.subponto >= len(VIZINHANCA.get(self.indice, ())):
            self._proximo_ponto()
            return
        self.subponto += 1
        self._limpar_busca()
        self.melhor_distancia, self.progresso, self.desvios = math.inf, None, 0

    def _virar(self, atual, desejado, andando=False):
        desvio = diferenca(desejado, atual)
        if self.giro:
            ultimo, enviado = self.giro
            mudanca = diferenca(atual, ultimo)
            self.giros_sem_resposta = self.giros_sem_resposta + 1 if abs(mudanca) < 2 else 0
            if 2 < abs(mudanca) < 135 and enviado * mudanca > 0:
                ganho = min(28., max(.5, abs(enviado / mudanca)))
                self.ganho_mouse = .55 * self.ganho_mouse + .45 * ganho
            self.giro = None
        if abs(desvio) <= 8:
            self.giros_sem_resposta = 0
            return None
        if self.giros_sem_resposta >= 3:
            self.status = "Rota · câmera sem resposta, confira o foco do Fortnite"
            self.proxima += 1.5
            self.giros_sem_resposta = 0
            return Comando("rota_aguardar_camera", ())
        dx = round(max(-450, min(450, desvio * self.ganho_mouse * .84)))
        self.giro = atual, dx
        if andando and abs(desvio) < 30:
            return Comando("rota_avancar", (("andar_mouse", (17,), .45, dx, 0),))
        return Comando("rota_virar", (("mouse", dx, 0, .16),))

    def _avancar(self, quadro, leitor, pos, alvo, agora, *, voo=False):
        atual = rumo_bussola(quadro, leitor)
        if atual is None:
            self.status = "Rota · aguardando a direção na bússola"
            self.giro = None
            return None
        desejado = math.degrees(math.atan2(alvo.x - pos.x, pos.y - alvo.y))
        giro = self._virar(atual, desejado, andando=True)
        if giro:
            return giro
        dist = distancia(pos, alvo)
        if not voo:
            if self.progresso is None or dist < self.melhor_distancia - 1.2:
                self.progresso, self.melhor_distancia, self.desvios = agora, dist, 0
            elif agora - self.progresso > 5:
                self.progresso = agora
                self.desvios += 1
                if self.desvios <= 2:
                    lado = 30 if self.desvios == 1 else 32
                    return Comando("rota_contornar", (("teclas", (lado,), .48), ("teclas", (17, 57), .45)))
                self.status = f"Rota · caminho bloqueado até {alvo.nome}"
                if self.desvios >= 4:
                    if alvo.procurar and dist < 14:
                        self._proxima_busca()
                    elif self.indice > 0:
                        self._proximo_ponto()
                return None
        return tecla("rota_avancar", 17, .42 if dist < 8 else .95)

    def _interagir(self, quadro, leitor, no_ponto):
        codigo = tecla_de_bau(quadro, leitor)
        if codigo is not None and self.bau_ignorado and distancia(self.pos, self.bau_ignorado) < 4:
            return False, None
        self.bau_ignorado = None
        if self.esperando_bau:
            if codigo is None:
                self.leituras_tecla += 1
                if self.leituras_tecla >= 2:
                    self.status = "Aviso de vasculhar desapareceu · seguindo a rota"
                    self._terminar_interacao(no_ponto)
                return True, None
            self.esperando_bau = False
            self.leituras_tecla = 0
            if self.tentativas_bau >= 2:
                self.status = "Baú ainda com aviso após duas tentativas · seguindo a rota"
                self._terminar_interacao(no_ponto)
                return True, None
        if codigo is not None:
            self.status = "Rota · conferindo o comando de vasculhar"
            self.leituras_tecla = self.leituras_tecla + 1 if self.tecla_vista == codigo else 1
            self.tecla_vista = codigo
            if self.leituras_tecla >= 2:
                return True, tecla("rota_vasculhar", codigo, 1.6)
            return True, None
        self.tecla_vista, self.leituras_tecla = None, 0
        return False, None

    def _terminar_interacao(self, no_ponto):
        if no_ponto:
            self._proxima_busca()
        else:
            self.esperando_bau = False
            self.tecla_vista = None
            self.tentativas_bau = self.leituras_tecla = 0
        self.bau_ignorado = self.pos

    def _buscar(self, quadro, leitor, agora):
        if self.busca_desde is None:
            self.busca_desde = agora
        if self.olhares >= 6 or agora - self.busca_desde > 16:
            self._proxima_busca()
            return None
        self.status = f"Procurando baú · {ROTA[self.indice].nome}"
        atual = rumo_bussola(quadro, leitor)
        if atual is None:
            return None
        if self.rumo_busca is None:
            self.rumo_busca = atual
        giro = self._virar(atual, self.rumo_busca + 60 * self.olhares)
        if giro:
            return giro
        self.olhares += 1
        if self.olhares in (1, 4):
            return tecla("rota_aproximar", 17, .45)
        return None

    def fora_do_onibus(self, quadro, leitor, agora, *, hud, asa):
        if self.fase == "concluida" or agora < self.proxima:
            return None
        self.proxima = agora + .35
        pos = self._localizar(quadro, agora)
        if pos is None:
            self.tecla_vista, self.leituras_tecla = None, 0
            return None
        pouso = ROTA[0]
        dist = distancia(pos, pouso)
        if self.fase == "aguardando":
            if asa:
                self.fase, self.ultimo_aereo = "voo", agora
            elif hud and dist <= 16:
                self.fase = "voo"
                self.ultimo_aereo = agora
            else:
                self.status = "Rota armada · aguardando o ônibus"
                self.proxima = agora + 1
                return None
        if self.fase == "onibus":
            if agora - self.ultimo_onibus < .8:
                return None
            self.fase, self.ultimo_aereo = "voo", agora
        if self.fase == "voo":
            if asa:
                self.abrir_asa = False
            elif agora >= self.proxima_asa:
                self.abrir_asa = aviso_abrir_asa(quadro, leitor)
                self.proxima_asa = agora + 1
            if asa or self.abrir_asa:
                self.ultimo_aereo = agora
            self.status = "Rota · aproximação da ponta leste"
            if agora - self.ultimo_espaco >= 2.5:
                if self.abrir_asa and dist > 36:
                    return tecla("rota_abrir_asa", 57, .16)
                if asa and dist <= 5:
                    return tecla("rota_fechar_asa", 57, .16)
            aterrissou = hud and not (asa or self.abrir_asa) and agora - self.ultimo_aereo >= (3 if dist <= 4 else 9)
            if not aterrissou and dist > 3.8:
                return self._avancar(quadro, leitor, pos, pouso, agora, voo=True)
            if not aterrissou:
                self.status = "Rota · aguardando o pouso na ponta leste"
                return None
            self.fase = "percurso"
        if not hud or asa:
            return None
        alvo = ROTA[self.indice]
        if self.subponto:
            x, y = VIZINHANCA[self.indice][self.subponto - 1]
            alvo = Ponto(x, y, alvo.nome, True)
        dist = distancia(pos, alvo)
        no_ponto = alvo.procurar and (dist <= 2.8 or self.busca_desde is not None and dist <= 6)
        em_interacao, comando = self._interagir(quadro, leitor, no_ponto)
        if em_interacao:
            return comando
        if no_ponto:
            return self._buscar(quadro, leitor, agora)
        if self.busca_desde is not None:
            self._limpar_busca()
        if dist <= 4 and not alvo.procurar:
            self._proximo_ponto()
            return None
        self.status = f"Rota · indo para {alvo.nome}"
        return self._avancar(quadro, leitor, pos, alvo, agora)
