from dataclasses import replace
import math
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from logica_autofarm import Config, Detector, Fluxo
from mapa_rota import ILHA, ROTA, Localizador, Posicao, aviso_abrir_asa, previa_rota, rumo_bussola, tecla_de_bau
from rota_baus import RotaBaus
from texto_tela import Linha


def pos(x, y):
    return Posicao(x, y, 132, .94)


class LeituraRotaTest(unittest.TestCase):
    def test_localiza_recortes_da_ilha_em_diferentes_resolucoes(self):
        atlas = cv2.imread(str(ILHA))
        for h, w, x, y, lado in ((720, 1280, 630, 202, 132), (900, 1440, 553, 225, 96),
                                  (1080, 1920, 545, 282, 164), (900, 1440, 398, 303, 140)):
            with self.subTest(h=h, x=x):
                f = np.zeros((h, w, 3), np.uint8)
                margem, s = round(h/45), round(h/6)
                mini = cv2.resize(atlas[y-lado//2:y+lado//2, x-lado//2:x+lado//2], (s, s))
                cv2.drawMarker(mini, (s//2, s//2), (255, 255, 255), cv2.MARKER_CROSS, 7, 1)
                f[margem:margem+s, w-margem-s:w-margem] = mini
                loc = Localizador()
                for _ in range(2):
                    p = loc.localizar(f)
                    self.assertIsNotNone(p)
                    self.assertLessEqual(math.hypot(p.x-x, p.y-y), 3)
        self.assertIsNone(Localizador().localizar(np.zeros((860, 1440, 3), np.uint8)))
        self.assertGreater(previa_rota().size, 0)

    def test_avisos_exigem_rotulo_de_interacao(self):
        frame = np.zeros((900, 1440, 3), np.uint8)
        leitor = Mock()
        for texto, scan in (("[F] VASCULHAR", 33), ("SEARCH", 18), ("Abrir baú", 18),
                            ("Abrir porta", None), ("Equipar", None), ("Pesquisar jogador", None)):
            leitor.linhas.return_value = [Linha(texto, (40, 20, 100, 20))]
            self.assertEqual(tecla_de_bau(frame, leitor), scan)
        leitor.linhas.return_value = [Linha("F", (20, 20, 15, 20)), Linha("VASCULHAR", (40, 20, 100, 20))]
        self.assertEqual(tecla_de_bau(frame, leitor), 33)
        leitor.linhas.return_value = [Linha("94", (45, 3, 25, 15))]
        self.assertEqual(rumo_bussola(frame, leitor), 94)
        for texto, esperado in (("ESPAÇO PARA ABRIR ASA-DELTA", True), ("ESPAÇO PARA ENTRAR EM QUEDA LIVRE", False)):
            leitor.linhas.return_value = [Linha(texto, (10, 10, 100, 15))]
            self.assertEqual(aviso_abrir_asa(frame, leitor), esperado)


class RotaTest(unittest.TestCase):
    def setUp(self):
        self.loc = Mock()
        self.rota = RotaBaus(self.loc)
        self.abrir = self.enterContext(patch("rota_baus.aviso_abrir_asa", return_value=False))
        self.bau = self.enterContext(patch("rota_baus.tecla_de_bau", return_value=None))
        self.bussola = self.enterContext(patch("rota_baus.rumo_bussola", return_value=90))

    def fora(self, t, x=630, y=202, asa=False):
        self.loc.localizar.return_value = pos(x, y)
        return self.rota.fora_do_onibus(None, None, t, hud=True, asa=asa)

    def test_onibus_aguarda_aproximacao_e_usa_o_ponto_mais_proximo(self):
        for t in range(5):
            self.loc.localizar.return_value = pos(600+t*5, 320)
            self.assertIsNone(self.rota.no_onibus(None, t))
        self.loc.localizar.return_value = pos(625, 320)
        acao = self.rota.no_onibus(None, 5)
        self.assertEqual(acao.tipo, "saltar_onibus")
        self.assertEqual(acao.passos, (("teclas", (57,), .16),))
        self.rota.registrar(acao.tipo, True, 5.2)
        self.assertIsNone(self.rota.no_onibus(None, 6))

    def test_perda_de_minimapa_e_pausa_exigem_novas_leituras(self):
        self.rota.fase = "percurso"
        self.loc.localizar.side_effect = [pos(625, 202), pos(625, 202), None, pos(625, 202), pos(625, 202)]
        def avaliar(t):
            return self.rota.fora_do_onibus(None, None, t, hud=True, asa=False)
        self.assertIsNone(avaliar(0))
        self.assertEqual(avaliar(.5).tipo, "rota_avancar")
        self.assertIsNone(avaliar(1))
        self.rota.pausar()
        self.assertIsNone(avaliar(2))
        self.assertEqual(avaliar(2.5).tipo, "rota_avancar")
        self.assertEqual(self.rota.indice, 0)

    def test_planar_longe_e_fechar_apenas_sobre_a_ponta(self):
        self.rota.fase, self.rota.ultimo_aereo = "voo", 0
        self.abrir.return_value = True
        self.fora(0, 580)
        abrir = self.fora(.5, 580)
        self.assertEqual(abrir.tipo, "rota_abrir_asa")
        self.rota.registrar(abrir.tipo, True, .7)
        for t, x in enumerate((592, 606, 617), 2):
            acao = self.fora(t, x, asa=True)
            self.assertEqual(acao.tipo, "rota_avancar")
            self.assertNotIn(31, acao.passos[0][1])
        fechar = self.fora(5, 628, asa=True)
        self.assertEqual(fechar.tipo, "rota_fechar_asa")
        self.assertEqual(self.rota.fase, "voo")
        self.bau.assert_not_called()

    def test_confirmacao_do_bau_repeticao_e_avanco_sem_contar_cliques(self):
        self.rota.fase = "percurso"
        self.bau.return_value = 33
        self.fora(0)
        self.assertIsNone(self.fora(.5))
        abrir = self.fora(1)
        self.assertEqual(abrir.passos, (("teclas", (33,), 1.6),))
        self.rota.registrar(abrir.tipo, True, 2.6)
        self.assertEqual(self.rota.indice, 0)
        self.bau.return_value = None
        self.assertIsNone(self.fora(3))
        self.assertEqual(self.rota.indice, 0)
        self.assertIsNone(self.fora(3.5))
        self.assertEqual((self.rota.indice, self.rota.subponto), (0, 1))

    def test_bau_no_caminho_nao_pula_o_proximo_destino(self):
        self.rota.fase, self.rota.indice = "percurso", 1
        self.bau.return_value = 18
        self.fora(0, 616, 209)
        self.fora(.5, 616, 209)
        acao = self.fora(1, 616, 209)
        self.rota.registrar(acao.tipo, True, 2.6)
        self.bau.return_value = None
        self.fora(3, 616, 209)
        self.fora(3.5, 616, 209)
        self.assertEqual(self.rota.indice, 1)

    def test_fluxo_preserva_pausa_menu_fila_e_saida_por_tempo(self):
        detector = Mock(spec=Detector)
        detector.config = replace(Config(), rota_baus=True, rotina_aerea=False, anti_afk=False,
                                  sair_quando_sozinho=False, minutos_partida=1)
        detector.detectar.return_value = detector.procurar.return_value = detector.ler_contexto.return_value = None
        detector.recompensa.return_value = None
        detector.dialogo_ambiguo = False
        fluxo = Fluxo(detector)
        fluxo.rota = self.rota
        quadro = np.zeros((900, 1440, 3), np.uint8)
        self.loc.localizar.return_value = pos(620, 202)
        self.assertIsNone(fluxo.passo(quadro, 0, no_onibus=True))
        salto = fluxo.passo(quadro, .5, no_onibus=True)
        self.assertEqual(salto.tipo, "saltar_onibus")
        fluxo.embarque.registrar(salto.tipo, True, .7)
        fluxo.rota.registrar(salto.tipo, True, .7)
        self.assertIsNone(fluxo.passo(quadro, 1, foco=False))
        fluxo._iniciar_partida(2)
        self.assertIsNone(fluxo.passo(quadro, 4, partida=True, cursor=True))
        self.assertIsNone(fluxo.passo(quadro, 5, partida=True, derrubado=True))
        self.assertEqual(fluxo.passo(quadro, 63, partida=True).tipo, "esc")
        fluxo.reconhecer_retorno_ao_lobby()
        self.assertEqual(fluxo.rota.fase, "aguardando")

    def test_ultimo_ponto_encerra_sem_voltar_ao_movimento_aleatorio(self):
        self.rota.fase, self.rota.indice = "percurso", len(ROTA) - 1
        self.rota._proximo_ponto()
        self.assertIsNone(self.fora(10))
        self.assertEqual(self.rota.fase, "concluida")
        self.loc.localizar.assert_not_called()
