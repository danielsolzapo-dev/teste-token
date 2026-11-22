"""Tempos do painel, calculados a partir de instantes monotônicos."""

import math


def _formatar(segundos):
    horas, resto = divmod(max(0, segundos), 3600)
    minutos, segundos = divmod(resto, 60)
    return f"{horas:02d}:{minutos:02d}:{segundos:02d}" if horas else f"{minutos:02d}:{segundos:02d}"


class RelogioPainel:
    def __init__(self, acumulado=0):
        self._inicio = None
        self._acumulado = max(0.0, float(acumulado))
        self._ja_iniciou = self._acumulado > 0
        self.prazo_saida = None

    def iniciar(self, agora):
        if self._inicio is None:
            self._inicio = agora
            self._ja_iniciou = True

    def pausar(self, agora):
        if self._inicio is not None:
            self._acumulado += max(0.0, agora - self._inicio)
            self._inicio = None

    def valores(self, agora):
        ativo = self._inicio is not None
        farm = self.segundos_farmando(agora)
        saida = _formatar(math.ceil(self.prazo_saida - agora)) if ativo and self.prazo_saida is not None else "—"
        return saida, _formatar(int(farm)) if self._ja_iniciou else "—"

    def segundos_farmando(self, agora):
        farm = self._acumulado
        if self._inicio is not None:
            farm += max(0.0, agora - self._inicio)
        return farm
