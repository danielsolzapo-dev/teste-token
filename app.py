"""Interface do Auto Farm para Windows."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk
import webbrowser

import cv2
import numpy as np
from PIL import Image, ImageGrab, ImageTk

from atualizador import (ErroAtualizacao, baixar_atualizacao, buscar_atualizacao,
                         criar_script_instalacao, iniciar_instalacao, preparar_executavel)
from diagnostico import Diagnostico
from estado_local import carregar_estado, salvar_estado as gravar_estado_local
from lobby_especial import MonitorLobby
from logica_autofarm import (ACOES_AEREAS, BOTOES_FILA, BOTOES_LOBBY, Config, Detector, Fluxo,
                             TECLAS, TEMPLATES)
from painel_ativacao import obter_licenca
from licenca import hoje_licenca
from painel_replay import JanelaBaus
from painel_tema import APARENCIAS, TemaPainel, ajustar_titulo, carregar_inter
from regiao import AMERICA_DO_NORTE, MANHA, NOITE, regiao_por_horario, regiao_principal
from relogio_painel import RelogioPainel
from tesouros import DetectorTesouros, monitorar_tesouros
from versao import VERSAO
from webhook import ErroWebhook, enviar_webhook, validar_url
from windows_game import Windows, configurar_dpi, reduzir_prioridade_thread


SOLTANDO_CONTROLES = "Soltando os controles da automação…"
FINALIZANDO_LEITURA = "Controles liberados. Finalizando a leitura da tela…"
CONFIG = Path(os.getenv("LOCALAPPDATA", ".")) / "AutoFarmToken" / "ajustes.json"
ARQUIVO_LOG = CONFIG.with_name("diagnostico.log")
OPCOES = ("clicar_continuar", "fallback_cor", "sair_por_tempo", "anti_afk", "rotina_aerea",
          "atualizar_auto", "resgatar_recompensas", "notificar_tesouros", "regiao_auto",
          "segundo_plano", "cursor_segundo_plano", "pausar_ao_obter_token", "sair_quando_sozinho",
          "continuar_farmando", "pausar_programado")
CAMPOS = (*OPCOES, "minutos_partida", "pausa_apos_minutos", "webhook_url", "regiao_manha",
          "regiao_noite", "aparencia")
ROTULOS = {
    "jogar": "Jogar Novamente", "continuar": "Continuar", "lobby_jogar": "Jogar",
    "lobby_pronto": "Pronto", "menu_aba": "Abrir menu", "voltar_lobby": "Voltar ao lobby",
    "resgatar": "Resgatar", "confirmar_sair": "Confirmar saída", "aba_jogar": "Aba Jogar",
}


def carregar_config():
    padrao = Config()
    try:
        dados = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not isinstance(dados, dict):
            return padrao
        ajustes = {chave: dados[chave] for chave in OPCOES if type(dados.get(chave)) is bool}
        if isinstance(dados.get("webhook_url"), str):
            ajustes["webhook_url"] = dados["webhook_url"]
        dados["regiao_noite"] = regiao_principal(dados.get("regiao_noite"))
        for chave, permitido in (("regiao_manha", MANHA), ("regiao_noite", NOITE), ("aparencia", tuple(APARENCIAS))):
            if dados.get(chave) in permitido:
                ajustes[chave] = dados[chave]
        minutos = dados.get("minutos_partida")
        if type(minutos) in (int, float) and 1 <= minutos <= 60:
            ajustes["minutos_partida"] = minutos
        pausa = dados.get("pausa_apos_minutos")
        if type(pausa) is int and 1 <= pausa <= 1440:
            ajustes["pausa_apos_minutos"] = pausa
        return replace(padrao, **ajustes)
    except (OSError, ValueError):
        return padrao


def salvar_config(config):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    provisoria = CONFIG.with_suffix(".tmp")
    dados = asdict(config)
    provisoria.write_text(json.dumps({chave: dados[chave] for chave in CAMPOS}), encoding="utf-8")
    provisoria.replace(CONFIG)


def salvar_captura_diagnostico(quadro, diagnostico, evento, arquivo):
    if quadro is None:
        diagnostico.registrar(evento)
        return
    caminho = ARQUIVO_LOG.parent / "debug" / arquivo
    try:
        ok, imagem = cv2.imencode(".jpg", quadro, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            raise ValueError("Captura inválida")
        caminho.parent.mkdir(parents=True, exist_ok=True)
        provisoria = caminho.with_suffix(".tmp")
        provisoria.write_bytes(imagem.tobytes())
        provisoria.replace(caminho)
    except (OSError, ValueError, cv2.error):
        diagnostico.registrar(evento, captura="indisponivel")
    else:
        diagnostico.registrar(evento, captura=f"debug/{arquivo}")


def monitorar(windows, fluxo, eventos, parar, diagnostico=None, *, lobby=None, pausar_no_lobby=None):
    estado_anterior = acao_anterior = None
    dialogo_ambiguo_anterior = False
    leituras_anteriores = None
    proximo_registro = 0.0
    ultimo_tipo = None
    ordem = 0
    captura_resgate = None
    tela_regiao_anterior = leitura_regiao_anterior = captura_regiao = None
    prazo_anterior = fluxo.prazo_saida
    eventos.put(("prazo_saida", prazo_anterior))
    proximo_esquadrao = 0.0
    candidato_esquadrao = contagem_esquadrao = None
    quadros_esquadrao = 0
    leitura_equipe_anterior = None
    aguardando_foco = None
    hud_ausente_desde = None
    perda_hud_registrada = False
    etapa = "iniciar"
    acao = None
    try:
        if diagnostico:
            diagnostico.registrar("sessao_iniciada", versao=VERSAO, anti_afk=fluxo.config.anti_afk,
                                  saida_por_tempo=fluxo.config.sair_por_tempo,
                                  saida_quando_sozinho=fluxo.config.sair_quando_sozinho,
                                  segundo_plano=fluxo.config.segundo_plano,
                                  cursor_segundo_plano=fluxo.config.cursor_segundo_plano,
                                  minutos_partida=fluxo.config.minutos_partida)
        detector = fluxo.detector
        if faltando := TEMPLATES.keys() - detector.templates.keys():
            raise ValueError("Imagens de referência ausentes: " + ", ".join(sorted(faltando)))
        if lobby is None:
            lobby = MonitorLobby()
        while not parar.is_set():
            acao = None
            etapa = "capturar"
            segundo_plano = fluxo.config.segundo_plano
            janela = windows.localizar_fortnite() if segundo_plano else windows.fortnite_em_foco()
            em_fundo = bool(segundo_plano and janela and janela.pid_fundo)
            if aguardando_foco and not janela:
                parar.wait(0.3)
                continue
            quadro = None
            if janela and windows.disponivel(janela, parar):
                quadro = windows.capturar(janela, parar) if em_fundo else windows.capturar(janela)
            pronto = quadro is not None and windows.disponivel(janela, parar)
            if pronto:
                etapa = "avisos_lobby"
                especial = lobby.passo(quadro, fluxo, eventos, diagnostico)
                if parar.is_set():
                    break
                if especial:
                    fluxo.sobreviventes.interromper()
                    estado, acao, pausar = especial
                    aguardando_foco = None
                    if fluxo.prazo_saida != prazo_anterior:
                        prazo_anterior = fluxo.prazo_saida
                        eventos.put(("prazo_saida", prazo_anterior))
                    hud_ausente_desde = None
                    if estado != estado_anterior:
                        eventos.put(("estado", estado))
                        estado_anterior = estado
                    if pausar:
                        if diagnostico:
                            diagnostico.registrar("pausa_solicitada", origem="token_loja_pirata")
                        eventos.put(("pausa_token", parar))
                        parar.set()
                        break
                    if acao is not None:
                        etapa = "clicar_aviso_lobby"
                        usar_cursor = em_fundo and fluxo.config.cursor_segundo_plano
                        destino = replace(janela, cursor_fundo=True) if usar_cursor else janela
                        enviado = windows.executar(destino, acao, parar)
                        if diagnostico:
                            diagnostico.registrar("clique_aviso_lobby", alvo=acao.alvo.nome,
                                                  tentativa=acao.tentativa + 1, enviado=enviado)
                    parar.wait(0.8 if lobby.restricao.ativa else 0.35)
                    continue
                if aguardando_foco and em_fundo:
                    parar.wait(0.3)
                    continue
                if aguardando_foco:
                    alvo = aguardando_foco.alvo.nome if aguardando_foco.alvo else None
                    fluxo.rearmar_apos_foco(alvo, time.monotonic())
                    aguardando_foco = None
                    if diagnostico:
                        diagnostico.registrar("controle_retomado_em_primeiro_plano", alvo=alvo)
                etapa = "detectar"
                hud_visivel = partida = detector.em_partida(quadro)
                bloqueio_hud = detector.procurar(quadro, ("espectador", "reinicializando"), 0.8)
                if bloqueio_hud:
                    partida = False
                derrubado = (fluxo.inicio_partida is not None and not partida and not bloqueio_hud
                             and detector.personagem_derrubado(quadro))
                if em_fundo:
                    contexto_jogo = partida or bloqueio_hud or derrubado or fluxo.embarque.em_transicao(time.monotonic())
                    cursor = (fluxo.navegando_menu or not contexto_jogo
                              or bool(detector.procurar(quadro, ("menu_aba", "inventario"))))
                else:
                    cursor = windows.cursor_visivel()
                lendo_menu = cursor and fluxo.navegando_menu
                alvo_onibus, score_onibus = (None, 0.0) if lendo_menu or derrubado else detector.aviso_onibus(quadro)
                if parar.is_set():
                    break
                onibus = bool(alvo_onibus)
                alvo_asa, score_asa = detector.aviso_asa(quadro) if not (onibus or lendo_menu or derrubado) else (None, 0.0)
                if parar.is_set():
                    break
                asa_delta = bool(alvo_asa)
                if em_fundo and (onibus or asa_delta):
                    cursor = False
                contagem = fluxo.partidas
                agora = time.monotonic()
                if not partida or onibus or asa_delta or lendo_menu:
                    candidato_esquadrao = contagem_esquadrao = None
                    quadros_esquadrao = 0
                elif agora >= proximo_esquadrao:
                    proximo_esquadrao = agora + 1
                    quantidade = detector.barras_esquadrao(quadro)
                    quadros_esquadrao = quadros_esquadrao + 1 if quantidade == candidato_esquadrao else 1
                    candidato_esquadrao = quantidade
                    if quadros_esquadrao >= 3 and quantidade != contagem_esquadrao:
                        contagem_esquadrao = quantidade
                        if diagnostico:
                            diagnostico.registrar("hud_esquadrao", barras=quantidade)
                if (pausar_no_lobby is not None and pausar_no_lobby.is_set()
                        and not (partida or onibus or asa_delta or bloqueio_hud or derrubado)):
                    if detector.procurar(quadro, BOTOES_LOBBY):
                        if diagnostico:
                            diagnostico.registrar("pausa_programada", resultado="lobby_confirmado")
                        eventos.put(("pausa_programada_concluida", None))
                        parar.set()
                        break
                etapa = "decidir"
                acao = fluxo.passo(quadro, agora, partida=partida, cursor=cursor,
                                   no_onibus=onibus, hud_visivel=hud_visivel,
                                   asa_delta=asa_delta, espectando=bool(bloqueio_hud), derrubado=derrubado)
                if parar.is_set():
                    break
                sobreviventes = fluxo.sobreviventes
                if sobreviventes.equipe is None:
                    leitura_equipe_anterior = None
                elif diagnostico and sobreviventes.ultima_amostra == agora:
                    equipe = sobreviventes.equipe
                    leitura = (equipe.presentes, equipe.vivos, equipe.eliminados, sobreviventes.jogadores == 1)
                    if leitura != leitura_equipe_anterior or sobreviventes.confirmacoes == 3:
                        diagnostico.registrar("sobreviventes", companheiros=equipe.presentes, vivos=equipe.vivos,
                                              eliminados=equipe.eliminados, jogadores=sobreviventes.jogadores,
                                              motivo=sobreviventes.motivo, confirmacoes=sobreviventes.confirmacoes)
                        leitura_equipe_anterior = leitura
                if (fluxo.inicio_partida is None or hud_visivel or onibus or asa_delta
                        or bloqueio_hud or derrubado or fluxo.navegando_menu):
                    hud_ausente_desde = None
                    perda_hud_registrada = False
                elif fluxo.motivo_anti_afk == "hud_indisponivel":
                    if hud_ausente_desde is None:
                        hud_ausente_desde = agora
                    elif diagnostico and not perda_hud_registrada and agora - hud_ausente_desde >= 5:
                        salvar_captura_diagnostico(quadro, diagnostico, "falha_hud", "hud_ultima_falha.jpg")
                        perda_hud_registrada = True
                if diagnostico and fluxo.motivo_anti_afk == "ajustando_regiao":
                    tela = fluxo.regiao.ultima_tela
                    if tela is not None and tela is not tela_regiao_anterior:
                        tela_regiao_anterior = tela
                        if fluxo.regiao.fechando is None or captura_regiao is None:
                            captura_regiao = quadro
                        principal, subregiao = tela.pareamento, tela.subregiao
                        dados = (fluxo.regiao.alvo, tela.social, tela.configuracoes,
                                 principal.valor if principal else None, subregiao.valor if subregiao else None,
                                 bool(principal and principal.focada), bool(principal and principal.esquerda),
                                 bool(principal and principal.direita), bool(tela.aplicar), tela.dialogo_alteracoes)
                        if dados != leitura_regiao_anterior:
                            leitura_regiao_anterior = dados
                            diagnostico.registrar("leitura_regiao", alvo=dados[0], social=dados[1],
                                                  configuracoes=dados[2], pareamento=dados[3], subregiao=dados[4],
                                                  foco_seletor=dados[5], seta_esquerda=dados[6],
                                                  seta_direita=dados[7], aplicar=dados[8], dialogo_alteracoes=dados[9])
                leituras = (True, em_fundo, hud_visivel, partida, cursor, onibus, asa_delta, derrubado, fluxo.motivo_anti_afk)
                if diagnostico and (leituras != leituras_anteriores or agora >= proximo_registro):
                    diagnostico.registrar("leitura", foco=not em_fundo, segundo_plano=em_fundo,
                                          hud=hud_visivel, partida=partida,
                                          cursor=cursor, onibus=onibus, asa_delta=asa_delta,
                                          anti_afk=fluxo.motivo_anti_afk,
                                          bloqueio_hud=("derrubado" if derrubado else
                                                       bloqueio_hud.nome if bloqueio_hud else None),
                                          score_onibus=round(score_onibus, 3),
                                          metodo_onibus=alvo_onibus.metodo if alvo_onibus else None,
                                          score_asa=round(score_asa, 3),
                                          metodo_asa=alvo_asa.metodo if alvo_asa else None,
                                          resolucao=[quadro.shape[1], quadro.shape[0]])
                    leituras_anteriores, proximo_registro = leituras, agora + 10
                if detector.dialogo_ambiguo and not dialogo_ambiguo_anterior:
                    eventos.put(("acao", "Confirmação de saída ambígua. Confira o jogo."))
                dialogo_ambiguo_anterior = detector.dialogo_ambiguo
                estado = ("Derrubado" if derrubado else "Espectando" if bloqueio_hud else "No ônibus" if onibus else
                          "Descendo de asa-delta" if asa_delta else
                          "Descendo após o salto" if fluxo.motivo_anti_afk == "movimento_aereo" else
                          "Em partida" if partida else
                          "Procurando botões")
                if fluxo.motivo_anti_afk == "aguardando_transicao_da_fila":
                    estado = "Aguardando transição da fila"
                elif hud_ausente_desde is not None and fluxo.motivo_anti_afk == "hud_indisponivel":
                    estado = "HUD da partida indisponível"
                elif fluxo.motivo_anti_afk in ("saida_por_tempo", "saida_por_tempo_sem_hud", "aguardando_menu_de_saida"):
                    estado = "Saindo da partida"
                elif fluxo.motivo_anti_afk == "ultimo_vivo_no_esquadrao":
                    estado = "Saindo · último vivo do esquadrão"
                elif fluxo.motivo_anti_afk == "ultimo_vivo_na_partida":
                    estado = "Saindo · último vivo da partida"
                elif fluxo.motivo_anti_afk == "ajustando_regiao":
                    estado = f"Ajustando região · {fluxo.regiao.alvo}" if fluxo.regiao.alvo else "Voltando ao lobby"
                elif partida and contagem_esquadrao:
                    estado = f"Em partida · {contagem_esquadrao} no HUD do esquadrão"
                if em_fundo:
                    estado += " · Segundo plano"
                falha_menu = acao and (
                    acao.tipo == "painel_social_nao_fechou"
                    or (acao.tipo == "clique_ignorado"
                        and (acao.alvo.nome in BOTOES_FILA
                             or acao.alvo.nome in ("menu_aba", "voltar_lobby", "confirmar_sair")))
                )
                if em_fundo and falha_menu:
                    aguardando_foco = acao
                    estado = "Fortnite sem resposta · volte ao jogo"
                    if diagnostico:
                        diagnostico.registrar("controle_aguardando_primeiro_plano", motivo=acao.tipo,
                                              alvo=acao.alvo.nome if acao.alvo else None)
                tem_entrada = acao is not None and (bool(acao.passos) or acao.tipo in ("clicar", "esc", "apontar"))
                if diagnostico and acao and (tem_entrada or acao.tipo != ultimo_tipo):
                    ordem += 1
                    posicao = ({"centro": tuple(round(v) for v in acao.alvo.centro)}
                               if acao.tipo in ("clicar", "apontar") else {})
                    diagnostico.registrar("acao", id=ordem, tipo=acao.tipo,
                                          alvo=acao.alvo.nome if acao.alvo else None,
                                          tentativa=acao.tentativa + 1 if acao.tipo == "clicar" else None,
                                          passos=acao.passos, **posicao)
                ultimo_tipo = acao.tipo if acao else None
                etapa = "enviar_entrada"
                com_cursor = em_fundo and tem_entrada and acao.tipo == "clicar" and fluxo.config.cursor_segundo_plano
                janela_entrada = replace(janela, cursor_fundo=True) if com_cursor else janela
                executada = acao is not None and (not tem_entrada or windows.executar(janela_entrada, acao, parar))
                motivo = (windows.motivo_interrupcao(janela_entrada, parar)
                          if tem_entrada and not executada and (diagnostico or com_cursor) else None)
                if motivo == "mouse_ou_teclado_em_uso":
                    fluxo.aguardar_controles_livres(time.monotonic())
                if diagnostico and tem_entrada:
                    metodo = ("mensagem_janela_cursor" if com_cursor else
                              "mensagem_janela" if em_fundo else "send_input")
                    diagnostico.registrar("resultado_entrada", id=ordem,
                                          resultado=("mensagem_processada" if com_cursor else
                                                     "mensagem_enfileirada" if em_fundo else "enviada")
                                          if executada else "interrompida", motivo=motivo,
                                          metodo=metodo)
                if diagnostico and acao and acao.tipo in ("regiao_falhou", "regiao_ok"):
                    if acao.tipo == "regiao_falhou":
                        salvar_captura_diagnostico(captura_regiao, diagnostico, "falha_regiao", "regiao_ultima_falha.jpg")
                    tela_regiao_anterior = leitura_regiao_anterior = captura_regiao = None
                if acao and acao.tipo == "clicar" and not acao.alvo.nome.startswith("regiao_"):
                    fluxo.registrar_clique(acao, executada, time.monotonic(), no_onibus=onibus)
                    if executada and acao.alvo.nome == "resgatar":
                        url = fluxo.config.webhook_url
                        captura_resgate = (url, acao.alvo.item, quadro) if url else None
                elif acao and acao.tipo == "resgate_confirmado":
                    if captura_resgate:
                        eventos.put(("resgate_webhook", captura_resgate))
                    captura_resgate = None
                elif acao and acao.tipo == "clique_ignorado" and acao.alvo.nome == "resgatar":
                    captura_resgate = None
                elif acao and (acao.tipo in ACOES_AEREAS or acao.tipo == "movimento_aereo"):
                    fluxo.embarque.registrar(acao.tipo, executada, time.monotonic())
                if fluxo.partidas != contagem:
                    eventos.put(("total", fluxo.partidas))
                prazo_saida = fluxo.prazo_saida
                if prazo_saida != prazo_anterior:
                    eventos.put(("prazo_saida", prazo_saida))
                    if diagnostico:
                        segundos = None if prazo_saida is None else round(max(0, prazo_saida - time.monotonic()), 1)
                        diagnostico.registrar("cronometro_saida", segundos_restantes=segundos)
                    prazo_anterior = prazo_saida
                if executada:
                    if acao.tipo.startswith("regiao_"):
                        texto = (f"Região confirmada · {acao.alvo.nome}." if acao.tipo == "regiao_ok" else
                                 f"A região {acao.alvo.nome} não foi confirmada. Nova tentativa em 20 min.")
                    elif acao.alvo and acao.alvo.nome.startswith("regiao_"):
                        texto = "Verificando região de pareamento…"
                    elif acao.tipo == "clicar":
                        texto = "Clique enviado em " + ROTULOS.get(acao.alvo.nome, acao.alvo.nome)
                    elif acao.tipo in ("movimento", "movimento_aereo"):
                        teclas = [nome for nome, scan in TECLAS.items()
                                  if any(p[0] in ("teclas", "andar_mouse") and scan in p[1] for p in acao.passos)]
                        rotulo = "descida" if acao.tipo == "movimento_aereo" else "caminhada"
                        acoes = [f"{rotulo} ({'/'.join(teclas)})"] if teclas else []
                        if any(p[0] in ("andar_mouse", "mouse") for p in acao.passos):
                            acoes.append("câmera")
                        if any(p[0] == "clique" for p in acao.passos):
                            acoes.append("picareta")
                        texto = f"Anti-AFK · {', '.join(acoes)} às {time.strftime('%H:%M:%S')}" if acoes else None
                    else:
                        texto = {
                            "esc": ("Fechando o painel Social do lobby."
                                    if acao.alvo and acao.alvo.nome == "lobby_fechar_social" else "Esc enviado"),
                            "saltar_onibus": "Salto do ônibus enviado com Espaço.",
                            "fechar_asa": "Espaço enviado após confirmar Entrar em queda livre.",
                            "movimento_menu": "Anti-AFK aguardando fechar o menu de saída.",
                            "clique_confirmado": "Botão saiu da tela",
                            "resgate_confirmado": "Recompensa resgatada.",
                            "clique_ignorado": "Botão continua visível após três tentativas",
                            "painel_social_nao_fechou": "O painel Social continua aberto. Feche-o para voltar ao lobby.",
                            "menu_nao_encontrado": "Menu de saída não encontrado. Tentando de novo em instantes.",
                            "lobby_nao_encontrado": "Lobby não encontrado. Confira se o jogo pede confirmação.",
                            "aguardando_fila": "Aguardando confirmação da fila ou do início da partida",
                            "fila_confirmada": "Fila confirmada. Aguardando o início da partida.",
                            "fila_sem_partida": "Fila confirmada sem início de partida. Aguardando o jogo ou o cancelamento.",
                            "fila_cancelada": "Cancelamento de fila reconhecido.",
                            "partida_confirmada": "Partida reconhecida. Cronômetro iniciado.",
                            "partida_nao_confirmada": "Fila demorando. Continuo aguardando a partida.",
                            "tela_nao_reconhecida": "Tela sem sinais conhecidos. Verifique o jogo ou use Testar imagem.",
                        }[acao.tipo]
                    if texto and texto != acao_anterior:
                        eventos.put(("acao", texto))
                        acao_anterior = texto
                elif acao:
                    fluxo.pausar()
                    if not parar.is_set():
                        texto = ("Solte o mouse e o teclado por um instante para o clique em segundo plano."
                                 if motivo == "mouse_ou_teclado_em_uso" else "Comando interrompido em segundo plano."
                                 if em_fundo else "Ação interrompida. Confira a janela e o foco do Fortnite.")
                        eventos.put(("acao", texto))
            else:
                fluxo.pausar()
                hud_ausente_desde = None
                candidato_esquadrao = contagem_esquadrao = None
                quadros_esquadrao = 0
                estado = "Aguardando o Fortnite em primeiro plano"
                if segundo_plano:
                    estado = ("Captura da janela indisponível" if janela else
                              "Aguardando o Fortnite com a janela restaurada")
                motivo = ("captura_indisponivel" if em_fundo and quadro is None else
                          windows.motivo_interrupcao(janela, parar) if janela else
                          "fortnite_indisponivel" if segundo_plano else "fortnite_sem_foco")
                erro_captura = windows.erro_captura if em_fundo else None
                if diagnostico and leituras_anteriores != (False, motivo, erro_captura):
                    diagnostico.registrar("leitura", foco=False, segundo_plano=em_fundo,
                                          anti_afk=motivo, erro_captura=erro_captura)
                    leituras_anteriores = (False, motivo, erro_captura)
            if estado != estado_anterior:
                eventos.put(("estado", estado))
                estado_anterior = estado
            parar.wait(0.18 if pronto else 0.3)
    except Exception as erro:
        if diagnostico:
            diagnostico.registrar("erro", etapa=etapa, tipo=type(erro).__name__,
                                  codigo=getattr(erro, "winerror", None),
                                  id=ordem if acao else None, acao=acao.tipo if acao else None)
        eventos.put(("erro", str(erro)))
    finally:
        if diagnostico:
            diagnostico.registrar("sessao_encerrada", pausada=parar.is_set())
        parar.set()
        if fluxo.config.segundo_plano:
            windows.fechar_captura_fundo()
        fluxo.pausar()


def detectar_arquivo(caminho):
    quadro = cv2.imdecode(np.fromfile(caminho, dtype=np.uint8), cv2.IMREAD_COLOR)
    if quadro is None:
        raise ValueError("Não foi possível ler essa imagem.")
    return quadro


class Painel:
    def __init__(self, janela, windows, config=None, diagnostico=None, *, expira=None, por_venda=False):
        self.janela, self.windows = janela, windows
        self.expira = expira
        self.por_venda = por_venda
        self.licenca_expirada = False
        self.diagnostico = diagnostico
        self.config = config if config is not None else carregar_config()
        self.estado_local = carregar_estado()
        self.partidas_base = self.estado_local["partidas"]
        self.partidas_sessao = 0
        self.fluxo = None
        self.monitor_lobby = None
        self.eventos = SimpleQueue()
        self.eventos_atualizacao = SimpleQueue()
        self.release_disponivel = self.atualizacao_preparada = self.operacao_atualizacao = None
        self.worker = self.parar = None
        self.fechando = False
        self.f7_anterior = False
        self.status = tk.StringVar(janela, "Pausado")
        self.atividade = tk.StringVar(janela, "Automação pausada. Use Iniciar ou F6 para retomar.")
        self.total = tk.StringVar(janela, str(self.partidas_base))
        self.proxima_saida = tk.StringVar(janela, "—")
        self.farmando = tk.StringVar(janela, "—")
        acumulado = self.estado_local["farmando_segundos"] if self.config.continuar_farmando else 0
        self.relogio = RelogioPainel(acumulado)
        self.status_atualizacao = tk.StringVar(janela, f"Versão {VERSAO}")
        self.opcoes = {nome: tk.BooleanVar(janela, getattr(self.config, nome)) for nome in OPCOES}
        self.minutos = tk.StringVar(janela, f"{self.config.minutos_partida:g}".replace(".", ","))
        self.pausa_apos_minutos = tk.StringVar(janela, str(self.config.pausa_apos_minutos))
        self.status_webhook = tk.StringVar(janela, "Webhook configurado" if self.config.webhook_url else "Webhook desativado")
        self.janela_webhook = self.botao_teste_webhook = None
        self.janela_regiao = None
        self.janela_replay = None
        self.teste_webhook_em_curso = False
        self._pausado_visual = None
        self._proximo_relogio = 0.0
        self._ultima_persistencia_estado = time.monotonic()
        self.pausar_no_lobby = None
        self._pausa_programada_pendente = False
        self._pausa_programada_prazo = None
        self._ultima_atualizacao = time.monotonic()

        self.salvamento = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Ajustes")
        self.aparencia = tk.StringVar(janela, APARENCIAS[self.config.aparencia])
        janela.title(f"Auto Farm {VERSAO}")
        janela.protocol("WM_DELETE_WINDOW", self.fechar)
        familias = set(tkfont.families(janela))
        familia = next((nome for nome in ("Inter Auto Farm", "Inter") if nome in familias), "Segoe UI")
        self.tema = TemaPainel(janela, familia)
        self.viewport = tk.Canvas(janela, bd=0, highlightthickness=0, yscrollincrement=20)
        self.barra = ttk.Scrollbar(janela, orient="vertical", command=self.viewport.yview)
        self.viewport.configure(yscrollcommand=self.barra.set)
        janela.rowconfigure(0, weight=1)
        janela.columnconfigure(0, weight=1)
        self.viewport.grid(row=0, column=0, sticky="nsew")
        self.barra.grid(row=0, column=1, sticky="ns")
        self.conteudo = None
        self._medidas_viewport = None
        self.viewport.bind("<Configure>", self._dimensionar_conteudo)
        janela.bind("<MouseWheel>", self._rolar)
        janela.bind("<Map>", self._ao_mapear)
        janela.bind("<Destroy>", self._ao_destruir)
        self.montar(self.config.aparencia)
        # Mantém a leitura de novos replays ativa durante a sessão, mesmo que
        # o usuário não abra o painel de contagem.
        self.janela_replay = JanelaBaus(janela, self.eventos)
        self.janela_replay.withdraw()
        self.janela_replay.iniciar(automatica=True)
        self.status.trace_add("write", self.decorar_estado)
        self._callback_interface = janela.after(45, self.atualizar)
        self._callback_atualizacao = janela.after(1000, self.verificar_automaticamente)

    def _ao_destruir(self, evento):
        if evento.widget is self.janela:
            self.janela.after_cancel(self._callback_interface)
            self.janela.after_cancel(self._callback_atualizacao)

    def _ao_mapear(self, evento):
        if evento.widget is self.janela:
            ajustar_titulo(self.janela, self.config.aparencia != "rosa")

    def _rolar(self, evento):
        if (isinstance(evento.widget, tk.Misc) and evento.widget.winfo_toplevel() is self.janela
                and not isinstance(evento.widget, (ttk.Spinbox, ttk.Combobox))):
            self.viewport.yview_scroll(-int(evento.delta / 120), "units")

    def _dimensionar_conteudo(self, _=None):
        if self.conteudo is None:
            return
        largura, altura = self.viewport.winfo_width(), self.viewport.winfo_height()
        if min(largura, altura) <= 1:
            return
        natural = self.conteudo.winfo_reqheight()
        medidas = largura, altura, natural
        if medidas == self._medidas_viewport:
            return
        self._medidas_viewport = medidas
        self.viewport.itemconfigure(self.janela_conteudo, width=largura, height=max(altura, natural))
        self.viewport.configure(scrollregion=(0, 0, largura, max(altura, natural)))
        if natural > altura:
            self.barra.grid()
        else:
            self.barra.grid_remove()

    def trocar_aparencia(self, _=None):
        escolha = next((chave for chave, nome in APARENCIAS.items() if nome == self.aparencia.get()), self.config.aparencia)
        if escolha != self.config.aparencia:
            self.config = replace(self.config, aparencia=escolha)
            self.tema.aplicar(escolha)
            self.viewport.configure(background=self.tema.paleta.fundo)
            ajustar_titulo(self.janela, escolha != "rosa")
            self._pausado_visual = None
            self.decorar_estado()
            self._salvar_preferencias()

    def _salvar_preferencias(self):
        self.salvamento.submit(self._gravar_preferencias, self.config)

    def _gravar_preferencias(self, config):
        try:
            salvar_config(config)
        except OSError:
            self.eventos.put(("erro", "Não foi possível salvar os ajustes neste computador."))

    def _registrar_diagnostico(self, evento, **dados):
        if self.diagnostico:
            self.salvamento.submit(self.diagnostico.registrar, evento, **dados)

    def _salvar_estado_local(self):
        # Alguns testes unitários constroem Painel parcialmente com object.__new__.
        # O painel real sempre inicializa esses campos no __init__.
        if not all(hasattr(self, nome) for nome in ("partidas_base", "partidas_sessao", "relogio", "config")):
            return
        estado = {
            "partidas": self.partidas_base + self.partidas_sessao,
            "farmando_segundos": (round(self.relogio.segundos_farmando(time.monotonic()))
                                  if self.config.continuar_farmando else 0),
        }
        self.estado_local = estado
        try:
            self.salvamento.submit(self._gravar_estado_local, estado)
        except RuntimeError:
            pass

    def _gravar_estado_local(self, estado):
        try:
            gravar_estado_local(estado)
        except OSError:
            self.eventos.put(("erro", "Não foi possível salvar o contador e o tempo Farmando."))

    def decorar_estado(self, *_):
        pausado = self.status.get() in ("Pausado", "Pausando")
        if pausado == self._pausado_visual:
            return
        self._pausado_visual = pausado
        cor = self.tema.paleta
        self.icone_estado.configure(image=self.tema.icone(
            "pausa" if pausado else "play", cor.texto_lateral, 19))
        self.botao.configure(image=self.tema.icone("play" if pausado else "pausa", cor.texto_principal))

    def botao_com_icone(self, pai, nome, icone, comando, *, principal=False):
        cor = self.tema.paleta.texto_principal if principal else self.tema.paleta.texto
        botao = ttk.Button(pai, text=nome, image=self.tema.icone(icone, cor), compound="left", command=comando,
                           style="Start.TButton" if principal else "TButton")
        botao._icone_tema = icone
        return botao

    def _estado_visual_botoes(self):
        if self.conteudo is None:
            return {}
        return {nome: (getattr(self, nome).cget("text"), getattr(self, nome).instate(["disabled"]))
                for nome in ("botao", "botao_testar", "botao_diagnostico", "botao_atualizacao")}

    def montar(self, aparencia):
        estados = self._estado_visual_botoes()
        if self.conteudo is not None:
            self.viewport.delete(self.janela_conteudo)
            self.conteudo.destroy()
        self.tema.aplicar(aparencia)
        cor = self.tema.paleta
        self.viewport.configure(background=cor.fundo)
        self.janela.configure(background=cor.fundo)
        self.janela.minsize(540, 345)
        altura = min(900, int(self.janela.winfo_screenheight() * 0.9))
        self.janela.geometry(f"620x{altura}")
        ajustar_titulo(self.janela, aparencia != "rosa")
        self.conteudo = ttk.Frame(self.viewport)
        self._medidas_viewport = None
        self.janela_conteudo = self.viewport.create_window(0, 0, anchor="nw", window=self.conteudo)
        self.conteudo.bind("<Configure>", self._dimensionar_conteudo)
        resumo = ttk.Frame(self.conteudo, style="Lateral.TFrame", padding=(24, 18, 24, 0))
        operacoes = ttk.Frame(self.conteudo, padding=(24, 12, 24, 20))
        resumo.pack(fill="x")
        operacoes.pack(fill="x")
        self._montar_sessao(resumo)
        self._montar_controles(operacoes)
        for nome, (texto, desativado) in estados.items():
            widget = getattr(self, nome)
            widget.configure(text=texto)
            widget.state(["disabled"] if desativado else ["!disabled"])
        self._pausado_visual = None
        self.decorar_estado()
        self.viewport.yview_moveto(0)

    def _montar_sessao(self, resumo):
        alinhamento = "w"
        ttk.Label(resumo, text="Auto Farm", style="Titulo.TLabel").pack(anchor=alinhamento)
        validade = f"Licença até {self.expira:%d/%m/%Y}" if self.expira else "Licença vitalícia"
        ttk.Label(resumo, text=f"v{VERSAO} · {validade}", style="LateralInfo.TLabel").pack(anchor=alinhamento)
        ttk.Label(resumo, text="Fortnite · Fila, saída por tempo e movimento",
                  style="LateralInfo.TLabel").pack(anchor=alinhamento, pady=(3, 12))
        ttk.Label(resumo, text="Aparência", style="LateralInfo.TLabel").pack(anchor=alinhamento, pady=(0, 3))
        seletor = ttk.Combobox(resumo, state="readonly", values=tuple(APARENCIAS.values()),
                              textvariable=self.aparencia, width=28)
        seletor.pack(fill="x", pady=(0, 14))
        seletor.bind("<<ComboboxSelected>>", self.trocar_aparencia)
        estado = ttk.Frame(resumo, style="Lateral.TFrame")
        estado.pack(anchor=alinhamento)
        self.icone_estado = ttk.Label(estado, style="Lateral.TLabel")
        self.icone_estado.pack(side="left", padx=(0, 6))
        self.rotulo_estado = ttk.Label(estado, textvariable=self.status, style="Estado.TLabel",
                                      wraplength=480)
        self.rotulo_estado.pack(side="left")
        ttk.Label(resumo, textvariable=self.atividade, style="LateralInfo.TLabel",
                  wraplength=480, justify="left").pack(anchor=alinhamento, pady=(3, 12))
        linha = ttk.Frame(resumo, style="Lateral.TFrame")
        linha.pack(fill="x")
        for coluna, (nome, valor) in enumerate((("Partidas", self.total),
                                               ("Próxima saída", self.proxima_saida),
                                               ("Farmando", self.farmando))):
            linha.columnconfigure(coluna, weight=1, uniform="metricas")
            self._metrica(linha, nome, valor, cartao=True).grid(
                row=0, column=coluna, sticky="ew", padx=(0, 8) if coluna < 2 else 0)

    def _metrica(self, pai, legenda, variavel, *, cartao=False, contagem="Metrica.TLabel"):
        area = ttk.Frame(pai, style="Metrica.Card.TFrame" if cartao else "Lateral.TFrame", padding=(10, 8) if cartao else 0)
        ttk.Label(area, text=legenda, style="MetricaInfo.TLabel" if cartao else "LateralInfo.TLabel").pack()
        ttk.Label(area, textvariable=variavel, style="MetricaCard.TLabel" if cartao else contagem).pack()
        return area

    def _montar_controles(self, painel):
        automacao = ttk.Frame(painel, style="Card.TFrame", padding=(13, 10))
        automacao.pack(fill="x", pady=(0, 9))
        ttk.Label(automacao, text="Automação", style="Secao.TLabel").pack(anchor="w", pady=(0, 4))
        for nome, texto in (
            ("clicar_continuar", "Clicar em Continuar após a eliminação"),
            ("fallback_cor", "Procurar Jogar Novamente pela cor"),
            ("anti_afk", "Ativar movimento anti-AFK"),
            ("rotina_aerea", "Saltar e fechar a asa-delta com Espaço"),
            ("sair_por_tempo", "Voltar ao lobby após o tempo da partida"),
            ("sair_quando_sozinho", "Voltar ao lobby se só eu restar no esquadrão ou na partida"),
            ("segundo_plano", "Executar em segundo plano (experimental)"),
            ("cursor_segundo_plano", "Permitir mover o cursor nos cliques em segundo plano"),
        ):
            linha_opcao = automacao
            comando = self.salvar_saida_quando_sozinho if nome == "sair_quando_sozinho" else None
            ttk.Checkbutton(linha_opcao, text=texto, variable=self.opcoes[nome], style=self.tema.switch,
                            command=comando).pack(fill="x")
        ttk.Frame(automacao, style="Separador.TFrame").pack(fill="x", pady=6)
        campo = ttk.Frame(automacao, style="CardInterior.TFrame")
        campo.pack(fill="x")
        ttk.Label(campo, text="Minutos por partida", style="Card.TLabel").pack(side="left", padx=(0, 10))
        minutos = tuple(f"{passo / 2:g}".replace(".", ",") for passo in range(2, 121))
        ttk.Spinbox(campo, textvariable=self.minutos, values=minutos, width=8).pack(side="left", padx=(0, 8))
        ttk.Label(campo, text="1–60 min", style="CardInfo.TLabel").pack(side="left")

        programacao = ttk.Frame(painel, style="Card.TFrame", padding=(13, 10))
        programacao.pack(fill="x", pady=(0, 9))
        ttk.Label(programacao, text="Programação", style="Secao.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Checkbutton(programacao, text="Continuar o cronômetro Farmando ao reabrir",
                        variable=self.opcoes["continuar_farmando"], command=self.salvar_preferencia_cronometro,
                        style=self.tema.switch).pack(fill="x")
        linha_pausa = ttk.Frame(programacao, style="CardInterior.TFrame")
        linha_pausa.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(linha_pausa, text="Pausar no lobby após", variable=self.opcoes["pausar_programado"],
                        command=self.salvar_preferencia_programacao,
                        style=self.tema.switch).pack(side="left", fill="x", expand=True)
        campo_pausa = ttk.Spinbox(linha_pausa, from_=1, to=1440, textvariable=self.pausa_apos_minutos, width=6)
        campo_pausa.pack(side="left", padx=(6, 4))
        campo_pausa.bind("<FocusOut>", lambda _: self.salvar_preferencia_programacao())
        campo_pausa.bind("<Return>", lambda _: self.salvar_preferencia_programacao())
        ttk.Label(linha_pausa, text="min", style="CardInfo.TLabel").pack(side="left")

        self.botao = self.botao_com_icone(painel, "Iniciar", "play", self.alternar, principal=True)
        self.botao.pack(fill="x", pady=(0, 8))
        ferramentas = ttk.Frame(painel)
        ferramentas.pack(fill="x")
        self.botao_testar = self.botao_com_icone(ferramentas, "Testar imagem", "imagem", self.testar)
        self.botao_diagnostico = self.botao_com_icone(ferramentas, "Copiar diagnóstico", "diagnostico", self.copiar_diagnostico)
        self._alinhar_botoes(ferramentas, self.botao_testar, self.botao_diagnostico)
        ttk.Label(painel, text="F6 inicia ou pausa\nF7 marca uma falha no diagnóstico durante a sessão",
                  style="Info.TLabel", wraplength=480).pack(anchor="w", pady=(6, 12))

        atualizacao = ttk.Frame(painel, style="Card.TFrame", padding=(13, 10))
        atualizacao.pack(fill="x")
        ttk.Label(atualizacao, text="Atualizações", style="Secao.TLabel").pack(anchor="w")
        linha = ttk.Frame(atualizacao, style="CardInterior.TFrame")
        linha.pack(fill="x", pady=(4, 0))
        self.botao_atualizacao = self.botao_com_icone(linha, "Verificar", "atualizar", self.acao_atualizacao)
        self.botao_atualizacao.pack(side="right")
        ttk.Checkbutton(linha, text="Atualizar automaticamente", variable=self.opcoes["atualizar_auto"],
                        command=self.salvar_preferencia_atualizacao, style=self.tema.switch).pack(
                            side="left", fill="x", expand=True, padx=(0, 12))
        ttk.Label(atualizacao, textvariable=self.status_atualizacao, style="CardInfo.TLabel",
                  wraplength=365).pack(anchor="w", pady=(3, 0))
        rodape = ttk.Frame(painel)
        rodape.pack(fill="x", pady=(9, 0))
        self._alinhar_botoes(rodape,
                           self.botao_com_icone(rodape, "Recompensas e webhook", "presente", self.mostrar_webhook),
                           self.botao_com_icone(rodape, "Regiões por horário", "relogio", self.mostrar_regiao))
        ttk.Label(rodape, textvariable=self.status_webhook, style="Info.TLabel",
                  wraplength=180).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Button(rodape, text="Baús do replay", command=self.mostrar_baus_replay).grid(
            row=1, column=1, sticky="ew", pady=(5, 0))

    @staticmethod
    def _alinhar_botoes(pai, *botoes):
        for coluna, botao in enumerate(botoes):
            pai.columnconfigure(coluna, weight=1, uniform="botoes")
            botao.grid(row=0, column=coluna, sticky="ew", padx=(0, 7) if coluna + 1 < len(botoes) else 0)

    def mostrar_baus_replay(self):
        if self.janela_replay is None or not self.janela_replay.winfo_exists():
            self.janela_replay = JanelaBaus(self.janela, self.eventos)
            self.janela_replay.bind("<Map>", lambda _: ajustar_titulo(self.janela_replay, self.config.aparencia != "rosa"))
        self.janela_replay.abrir()

    def mostrar_regiao(self):
        if self.janela_regiao and self.janela_regiao.winfo_exists():
            self.janela_regiao.lift()
            return
        janela = self.janela_regiao = tk.Toplevel(self.janela)
        janela.title("Regiões por horário")
        janela.transient(self.janela)
        janela.configure(background=self.tema.paleta.fundo)
        janela.bind("<Map>", lambda _: ajustar_titulo(janela, self.config.aparencia != "rosa"))
        painel = ttk.Frame(janela, padding=20)
        painel.pack(fill="both", expand=True)
        ativar = tk.BooleanVar(janela, self.config.regiao_auto)
        manha = tk.StringVar(janela, self.config.regiao_manha)
        noite = tk.StringVar(janela, self.config.regiao_noite)
        estado = tk.StringVar(janela)
        ttk.Checkbutton(painel, text="Ajustar a região antes de entrar na fila", variable=ativar).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(painel, text="Horário de Brasília · Construção e Zero Build", style="Info.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        for linha, (faixa, controle) in enumerate((
            ("00h–06h", ttk.Label(painel, text=AMERICA_DO_NORTE)),
            ("06h–11h", ttk.Combobox(painel, values=MANHA, textvariable=manha, state="readonly")),
            ("11h–18h", ttk.Label(painel, text="Europa")),
            ("18h–00h", ttk.Combobox(painel, values=NOITE, textvariable=noite, state="readonly")),
        ), 2):
            ttk.Label(painel, text=faixa).grid(row=linha, column=0, sticky="w", padx=(0, 24), pady=3)
            controle.grid(row=linha, column=1, sticky="w", pady=3)
        ttk.Button(painel, text="Salvar", command=lambda: self.salvar_regiao(
            ativar.get(), manha.get(), noite.get(), estado)).grid(row=6, column=0, sticky="w", pady=(12, 0))
        ttk.Label(painel, textvariable=estado, style="Info.TLabel").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def salvar_regiao(self, ativar, manha, noite, estado):
        alteracoes = {"regiao_auto": ativar, "regiao_manha": manha, "regiao_noite": noite}
        self.config = replace(self.config, **alteracoes)
        self._salvar_preferencias()
        self.opcoes["regiao_auto"].set(ativar)
        if self.fluxo is not None:
            self.fluxo.config = replace(self.fluxo.config, **alteracoes)
        estado.set(f"Região prevista agora: {regiao_por_horario(manha, noite)}." if ativar else
                   "Região automática desativada.")

    def mostrar_webhook(self):
        if self.janela_webhook and self.janela_webhook.winfo_exists():
            self.janela_webhook.lift()
            return
        janela = self.janela_webhook = tk.Toplevel(self.janela)
        janela.title("Recompensas e webhook")
        janela.transient(self.janela)
        janela.configure(background=self.tema.paleta.fundo)
        janela.bind("<Map>", lambda _: ajustar_titulo(janela, self.config.aparencia != "rosa"))
        painel = ttk.Frame(janela, padding=20)
        painel.pack(fill="both", expand=True)
        resgatar = tk.BooleanVar(janela, self.config.resgatar_recompensas)
        tesouros = tk.BooleanVar(janela, self.config.notificar_tesouros)
        pausar_token = tk.BooleanVar(janela, self.config.pausar_ao_obter_token)
        url = tk.StringVar(janela, self.config.webhook_url)
        item = tk.StringVar(janela, "Picareta de teste")
        mensagem = tk.StringVar(janela, "Este é um teste do Auto Farm.")
        ttk.Checkbutton(painel, text="Resgatar recompensas automaticamente no lobby",
                        variable=resgatar).pack(anchor="w")
        ttk.Checkbutton(painel, text="Notificar tesouros elementais encontrados",
                        variable=tesouros).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(painel, text="Pausar ao obter o token da Loja Pirata", variable=pausar_token,
                        style=self.tema.switch).pack(anchor="w", pady=(6, 0))
        ttk.Label(painel, text="URL do webhook (Discord ou compatível)").pack(anchor="w", pady=(12, 2))
        entrada = ttk.Entry(painel, textvariable=url, width=62, show="•")
        entrada.pack(fill="x")
        ttk.Label(painel, text="Nome do item de teste").pack(anchor="w", pady=(12, 2))
        ttk.Entry(painel, textvariable=item).pack(fill="x")
        ttk.Label(painel, text="Mensagem de teste").pack(anchor="w", pady=(12, 2))
        ttk.Entry(painel, textvariable=mensagem).pack(fill="x")
        comandos = ttk.Frame(painel)
        comandos.pack(fill="x", pady=(12, 6))
        ttk.Button(comandos, text="Salvar", command=lambda: self.salvar_webhook(
            url.get(), resgatar.get(), tesouros.get(), pausar_token.get())).pack(side="left")
        self.botao_teste_webhook = ttk.Button(
            comandos, text="Testar com tela atual",
            command=lambda: self.testar_webhook(url.get(), item.get(), mensagem.get())
        )
        self.botao_teste_webhook.pack(side="left", padx=8)
        if self.teste_webhook_em_curso:
            self.botao_teste_webhook.state(["disabled"])
        ttk.Label(painel, text="O teste captura a tela em 3 segundos. Deixe visível o que quer enviar.",
                  style="Info.TLabel", wraplength=440).pack(anchor="w")
        ttk.Label(painel, textvariable=self.status_webhook, style="Info.TLabel",
                  wraplength=440).pack(anchor="w", pady=(6, 0))

    def salvar_webhook(self, url, resgatar, tesouros=None, pausar_token=None):
        url = url.strip()
        tesouros = self.config.notificar_tesouros if tesouros is None else tesouros
        pausar_token = self.config.pausar_ao_obter_token if pausar_token is None else pausar_token
        alteracoes = dict(webhook_url=url, resgatar_recompensas=resgatar, notificar_tesouros=tesouros,
                          pausar_ao_obter_token=pausar_token)
        config = replace(self.config, **alteracoes)
        try:
            if url:
                validar_url(url)
        except ErroWebhook as erro:
            self.status_webhook.set(str(erro))
            return
        self.config = config
        self._salvar_preferencias()
        self.opcoes["resgatar_recompensas"].set(resgatar)
        self.opcoes["notificar_tesouros"].set(tesouros)
        self.opcoes["pausar_ao_obter_token"].set(pausar_token)
        if self.fluxo is not None:
            self.fluxo.config = replace(self.fluxo.config, **alteracoes)
        self.status_webhook.set("Webhook salvo." if url else "Envio de webhook desativado.")

    def testar_webhook(self, url, nome, mensagem):
        try:
            validar_url(url)
        except ErroWebhook as erro:
            self.status_webhook.set(str(erro))
            return
        self.teste_webhook_em_curso = True
        self.botao_teste_webhook.state(["disabled"])
        self.status_webhook.set("Capturando a tela em 3 segundos…")
        self.janela.after(3000, lambda: self.iniciar_envio_webhook(
            url, nome=nome.strip() or "Picareta de teste", teste=True, mensagem=mensagem))

    def iniciar_envio_webhook(self, url, nome, quadro=None, *, teste=False, mensagem="", tesouro=False, aviso=None):
        Thread(target=self._enviar_webhook, args=(url, nome, quadro, teste, mensagem, tesouro, aviso), daemon=True).start()

    def _enviar_webhook(self, url, nome, quadro, teste, mensagem, tesouro, aviso=None):
        try:
            if quadro is None:
                jogo = self.windows.fortnite_em_foco()
                quadro = (self.windows.capturar(jogo) if jogo else
                          cv2.cvtColor(np.asarray(ImageGrab.grab().convert("RGB")), cv2.COLOR_RGB2BGR))
            enviar_webhook(url, quadro, nome, teste=teste, texto_teste=mensagem, tesouro=tesouro, aviso=aviso)
            texto = "Teste enviado." if teste else "Recompensa enviada ao webhook."
            if tesouro:
                texto = "Tesouro enviado ao webhook."
            elif aviso:
                texto = "Captura do aviso de banimento enviada." if aviso == "conta_banida" else "Captura do token enviada."
            sucesso = True
        except ErroWebhook as erro:
            texto, sucesso = str(erro), False
        except (OSError, ValueError, cv2.error):
            texto, sucesso = "Não foi possível capturar ou enviar a tela.", False
        self.eventos.put(("resultado_webhook", (teste, sucesso, texto)))

    def salvar_preferencia_atualizacao(self):
        self.config = replace(self.config, atualizar_auto=self.opcoes["atualizar_auto"].get())
        self._salvar_preferencias()
        if self.config.atualizar_auto:
            self.verificar_atualizacoes()

    def salvar_saida_quando_sozinho(self):
        habilitada = self.opcoes["sair_quando_sozinho"].get()
        self.config = replace(self.config, sair_quando_sozinho=habilitada)
        if self.fluxo is not None:
            self.fluxo.config = replace(self.fluxo.config, sair_quando_sozinho=habilitada)
        self._salvar_preferencias()

    def verificar_automaticamente(self):
        if self.fechando:
            return
        if self.opcoes["atualizar_auto"].get() and self.release_disponivel is None:
            self.verificar_atualizacoes()
        self._callback_atualizacao = self.janela.after(3600000, self.verificar_automaticamente)

    def verificar_atualizacoes(self):
        if self.operacao_atualizacao or self.fechando:
            return
        self.operacao_atualizacao = "consulta"
        self.status_atualizacao.set("Verificando atualizações…")
        self.botao_atualizacao.state(["disabled"])
        Thread(target=self._consultar_release, daemon=True).start()

    def _consultar_release(self):
        try:
            self.eventos_atualizacao.put(("release", buscar_atualizacao(VERSAO)))
        except (ErroAtualizacao, OSError) as erro:
            self.eventos_atualizacao.put(("erro", str(erro)))

    def acao_atualizacao(self):
        if self.release_disponivel is None:
            self.verificar_atualizacoes()
        elif getattr(sys, "frozen", False):
            self._baixar_release()
        else:
            webbrowser.open(self.release_disponivel.pagina)

    def _baixar_release(self):
        if self.operacao_atualizacao or self.fechando:
            return
        if self.worker is not None and self.worker.is_alive():
            self.status_atualizacao.set("Pause a automação para instalar a atualização.")
            return
        self.operacao_atualizacao = "download"
        self.botao_atualizacao.state(["disabled"])
        self.status_atualizacao.set(f"Baixando e verificando a v{self.release_disponivel.versao}…")
        Thread(target=self._preparar_release, args=(self.release_disponivel,), daemon=True).start()

    def _preparar_release(self, release):
        pasta = CONFIG.parent / "updates"
        pronto = None
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=pasta) as temporaria:
                baixado = baixar_atualizacao(release, temporaria)
                pronto = preparar_executavel(baixado, sys.executable, release.sha256)
                script = criar_script_instalacao(pronto, sys.executable, os.getpid(), pasta, release.sha256)
            self.eventos_atualizacao.put(("preparada", (pronto, script)))
        except (ErroAtualizacao, OSError) as erro:
            if pronto is not None:
                try:
                    pronto.unlink(missing_ok=True)
                except OSError:
                    pass
            self.eventos_atualizacao.put(("erro", str(erro)))

    def _processar_atualizacao(self):
        while True:
            try:
                evento, valor = self.eventos_atualizacao.get_nowait()
            except Empty:
                break
            if evento == "release":
                self.operacao_atualizacao = None
                self.release_disponivel = valor
                self.botao_atualizacao.state(["!disabled"])
                self.botao_atualizacao.configure(text="Atualizar" if valor else "Verificar")
                self.status_atualizacao.set(f"v{valor.versao} disponível." if valor else f"v{VERSAO} atualizada.")
            elif evento == "preparada":
                self.atualizacao_preparada = valor
            else:
                self.operacao_atualizacao = self.release_disponivel = None
                self.botao_atualizacao.state(["!disabled"])
                self.botao_atualizacao.configure(text="Tentar novamente")
                self.status_atualizacao.set(valor)
        if self.fechando or (self.worker is not None and self.worker.is_alive()):
            return
        if self.atualizacao_preparada:
            executavel, script = self.atualizacao_preparada
            self.atualizacao_preparada = None
            try:
                iniciar_instalacao(script)
            except (ErroAtualizacao, OSError) as erro:
                self.release_disponivel = self.operacao_atualizacao = None
                self.botao_atualizacao.state(["!disabled"])
                self.botao_atualizacao.configure(text="Tentar novamente")
                self.status_atualizacao.set(str(erro))
                try:
                    executavel.unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                self.status_atualizacao.set("Instalando a atualização. O app será reiniciado.")
                self.fechar()
            finally:
                try:
                    script.unlink(missing_ok=True)
                except OSError:
                    pass
        elif self.release_disponivel and self.opcoes["atualizar_auto"].get() and getattr(sys, "frozen", False):
            self._baixar_release()

    def copiar_diagnostico(self):
        if self.botao_diagnostico.instate(["disabled"]):
            return
        self.botao_diagnostico.state(["disabled"])
        Thread(target=self._ler_diagnostico, daemon=True).start()

    def _ler_diagnostico(self):
        try:
            texto = self.diagnostico.texto() if self.diagnostico else ""
        except OSError:
            texto = None
        self.eventos.put(("diagnostico_copiado", texto))

    def _mostrar_diagnostico_copiado(self, texto):
        self.botao_diagnostico.state(["!disabled"])
        if texto is None:
            self.atividade.set("Não foi possível ler o diagnóstico local.")
            return
        if not texto:
            self.atividade.set("Inicie uma sessão para gerar o diagnóstico.")
            return
        self.janela.clipboard_clear()
        self.janela.clipboard_append(texto)
        self.atividade.set("Diagnóstico copiado. Cole o texto na conversa em que for relatar a falha.")

    def ler_opcoes(self):
        minutos = float(self.minutos.get().replace(",", "."))
        if not 1 <= minutos <= 60:
            raise ValueError("Informe um tempo de partida entre 1 e 60 minutos.")
        try:
            pausa = int(self.pausa_apos_minutos.get())
        except ValueError:
            pausa = 0
        programada = self.opcoes["pausar_programado"].get()
        if programada and not 1 <= pausa <= 1440:
            raise ValueError("Informe um tempo de pausa programada entre 1 e 1.440 minutos.")
        return replace(self.config, minutos_partida=minutos, pausa_apos_minutos=pausa or 60,
                       **{nome: var.get() for nome, var in self.opcoes.items()})

    def salvar_preferencia_cronometro(self):
        continuar = self.opcoes["continuar_farmando"].get()
        self.config = replace(self.config, continuar_farmando=continuar)
        self._salvar_preferencias()
        if self.worker is None and continuar and self.relogio.segundos_farmando(time.monotonic()) == 0:
            self.relogio = RelogioPainel(self.estado_local["farmando_segundos"])
        self.atualizar_relogio(time.monotonic())
        self._salvar_estado_local()

    def salvar_preferencia_programacao(self):
        try:
            pausa = int(self.pausa_apos_minutos.get())
        except ValueError:
            pausa = 0
        programada = self.opcoes["pausar_programado"].get()
        if programada and not 1 <= pausa <= 1440:
            self.atividade.set("Informe uma pausa programada entre 1 e 1.440 minutos.")
            return
        config = replace(self.config, pausar_programado=programada, pausa_apos_minutos=pausa or 60)
        self.config = config
        self._salvar_preferencias()
        if self.worker is not None and self.worker.is_alive() and not self.parar.is_set():
            self._pausa_programada_prazo = (time.monotonic() + config.pausa_apos_minutos * 60
                                            if config.pausar_programado else None)
            self._pausa_programada_pendente = False
            if self.pausar_no_lobby:
                self.pausar_no_lobby.clear()

    def alternar(self, origem="botao"):
        if self.worker is not None:
            if not self.parar.is_set():
                self.parar.set()
                self.relogio.pausar(time.monotonic())
                self._salvar_estado_local()
                self.botao.state(["disabled"])
                self.atividade.set(SOLTANDO_CONTROLES)
                self.atualizar_pausa()
                self._registrar_diagnostico("pausa_solicitada", origem=origem)
            return
        try:
            config = self.ler_opcoes()
        except ValueError:
            self.atividade.set("Informe um tempo de partida entre 1 e 60 minutos.")
            return
        self.config = config
        self._salvar_preferencias()
        self.atividade.set("Deixe a janela do Fortnite restaurada." if config.segundo_plano else
                           "Coloque o Fortnite em primeiro plano.")
        self.relogio.prazo_saida = self.fluxo.prazo_saida if self.fluxo else None
        self._proximo_relogio = 0.0
        agora = time.monotonic()
        self.relogio.iniciar(agora)
        self._pausa_programada_pendente = False
        self._pausa_programada_prazo = (agora + config.pausa_apos_minutos * 60
                                        if config.pausar_programado else None)
        self.status.set("Iniciando")
        self.botao.configure(text="Pausar")
        self.parar = Event()
        self.pausar_no_lobby = Event()
        self.worker = Thread(target=self._executar_sessao, args=(self.parar, self.pausar_no_lobby))
        self.worker.start()

    def _executar_sessao(self, parar, pausar_no_lobby):
        try:
            reduzir_prioridade_thread()
            if self.fluxo is None:
                self.fluxo = Fluxo(Detector(config=self.config))
            if self.monitor_lobby is None:
                self.monitor_lobby = MonitorLobby()
            if parar.is_set():
                return
            self.fluxo.config = self.fluxo.detector.config = self.config
            if self.fluxo.rearmar_fila() and self.diagnostico:
                self.diagnostico.registrar("espera_fila_rearmada_por_iniciar")
            contexto = self.windows, self.fluxo, self.eventos, parar, self.diagnostico
            Thread(target=monitorar_tesouros, args=contexto, daemon=True).start()
            monitorar(*contexto, lobby=self.monitor_lobby, pausar_no_lobby=pausar_no_lobby)
        except (OSError, ValueError, cv2.error) as erro:
            self.eventos.put(("erro", f"Não foi possível iniciar a automação. {erro}"))
        finally:
            parar.set()

    def atualizar_pausa(self):
        if self.worker is None:
            return
        if not self.worker.is_alive():
            self.worker = None
            self.relogio.pausar(time.monotonic())
            self._salvar_estado_local()
            self.status.set("Pausado")
            self.botao.configure(text="Iniciar")
            self.botao.state(["!disabled"])
            if self.atividade.get() in (SOLTANDO_CONTROLES, FINALIZANDO_LEITURA):
                self.atividade.set("Automação pausada. Use Iniciar ou F6 para retomar.")
        elif self.parar.is_set():
            if self.windows.entrada_em_andamento():
                self.status.set("Pausando")
            else:
                if self.status.get() != "Pausado":
                    self.status.set("Pausado")
                    if self.atividade.get() == SOLTANDO_CONTROLES:
                        self.atividade.set(FINALIZANDO_LEITURA)

    def testar(self):
        if self.botao_testar.instate(["disabled"]):
            return
        caminho = filedialog.askopenfilename(parent=self.janela, title="Selecionar uma captura",
                                             filetypes=[("Imagens", "*.png *.jpg *.jpeg *.bmp")])
        if not caminho:
            return
        try:
            config = self.ler_opcoes()
        except ValueError:
            self.atividade.set("Informe um tempo de partida entre 1 e 60 minutos.")
            return
        self.botao_testar.state(["disabled"])
        self.atividade.set("Analisando imagem…")
        Thread(target=self._testar_imagem, args=(caminho, config), daemon=True).start()

    def _testar_imagem(self, caminho, config):
        try:
            reduzir_prioridade_thread()
            quadro, texto = self._analisar_imagem(caminho, config)
            foto = Image.fromarray(cv2.cvtColor(quadro, cv2.COLOR_BGR2RGB))
            foto.thumbnail((720, 405))
            resultado = foto, texto
        except Exception as erro:
            resultado = None
            if self.diagnostico:
                self.diagnostico.registrar("erro_teste_imagem", tipo=type(erro).__name__)
        self.eventos.put(("teste_imagem", resultado))

    def _analisar_imagem(self, caminho, config):
        quadro = detectar_arquivo(caminho)
        aviso = MonitorLobby().reconhecer(quadro)
        if aviso:
            if aviso.centro_botao:
                cv2.drawMarker(quadro, aviso.centro_botao, (0, 0, 255), cv2.MARKER_CROSS, 15, 2)
            return quadro, aviso.nome
        detector = Detector(config=config)
        alvo = detector.ler_contexto(quadro, "confirmar_sair")
        if alvo is None and not detector.dialogo_ambiguo:
            alvo = detector.detectar(quadro) or detector.procurar(
                quadro, ("lobby_jogar", "lobby_pronto", "voltar_lobby", "resgatar"),
            )
        if alvo:
            x, y, w, h = alvo.caixa
            cv2.rectangle(quadro, (x, y), (x + w, y + h), (85, 224, 183), max(2, quadro.shape[0] // 275))
            centro_x, centro_y = (round(ponto) for ponto in alvo.centro)
            cv2.drawMarker(quadro, (centro_x, centro_y), (0, 0, 255), cv2.MARKER_CROSS, 15, 2)
            metodo = {"cor+OCR": "cor/OCR", "texto": "OCR", "template": "template"}[alvo.metodo]
            texto = (f"{ROTULOS.get(alvo.nome, alvo.nome)} · centro ({centro_x}, {centro_y})"
                     f" · {metodo} · score {alvo.score:.0%}")
        elif detector.dialogo_ambiguo:
            texto = "Mais de um botão de confirmação plausível nesta imagem."
        elif detector.ler_contexto(quadro, "modo_esquadrao"):
            texto = "Painel reconhecido: Esquadrão com Preencher."
        elif detector.ler_contexto(quadro, "tela_regiao"):
            texto = "Aba Jogo reconhecida: Região de Pareamento."
        else:
            texto = "Nenhum botão reconhecido nesta imagem."
        return quadro, texto

    def mostrar_teste(self, foto, texto, *, titulo="Teste de imagem"):
        janela = tk.Toplevel(self.janela)
        janela.title(titulo)
        janela.transient(self.janela)
        janela.configure(background=self.tema.paleta.fundo)
        janela.bind("<Map>", lambda _: ajustar_titulo(janela, self.config.aparencia != "rosa"))
        painel = ttk.Frame(janela, padding=14)
        painel.pack(fill="both", expand=True)
        ttk.Label(painel, text=texto).pack(anchor="w", pady=(0, 10))
        imagem = ttk.Label(painel)
        imagem.foto = ImageTk.PhotoImage(foto)
        imagem.configure(image=imagem.foto)
        imagem.pack()

    def atualizar(self):
        if self.expira and hoje_licenca(self.por_venda) > self.expira and not self.fechando:
            self.encerrar_por_vencimento()
            return
        agora = time.monotonic()
        if agora - self._ultima_atualizacao >= 1:
            self._registrar_diagnostico("atraso_interface", segundos=round(agora - self._ultima_atualizacao, 2))
        self._ultima_atualizacao = agora
        if self.windows.f6_pressionado() and not self.fechando and "disabled" not in self.botao.state():
            self.alternar("f6")
        if self.diagnostico:
            f7 = self.windows.f7()
            if f7 and not self.f7_anterior and self.worker is not None and self.worker.is_alive():
                self._registrar_diagnostico("falha_marcada_pelo_usuario", estado=self.status.get())
            self.f7_anterior = f7
        if (self.worker is not None and self.worker.is_alive() and self.parar is not None
                and not self.parar.is_set() and self._pausa_programada_prazo is not None
                and not self._pausa_programada_pendente and agora >= self._pausa_programada_prazo):
            self._pausa_programada_pendente = True
            if self.pausar_no_lobby:
                self.pausar_no_lobby.set()
            self.atividade.set("Tempo atingido; aguardando o lobby para pausar automaticamente.")
            self._registrar_diagnostico("pausa_programada", resultado="aguardando_lobby")
        self._processar_atualizacao()
        for _ in range(24):
            try:
                evento, valor = self.eventos.get_nowait()
            except Empty:
                break
            if evento == "estado":
                if self.parar is not None and not self.parar.is_set() and self.status.get() != valor:
                    self.status.set(valor)
            elif evento == "total":
                if type(valor) is int and valor >= 0:
                    self.partidas_sessao = max(self.partidas_sessao, valor)
                    self.total.set(str(self.partidas_base + self.partidas_sessao))
                    self._salvar_estado_local()
            elif evento == "pausa_programada_concluida":
                self._pausa_programada_pendente = False
                self._pausa_programada_prazo = None
                self.atividade.set("Pausado no lobby pelo tempo programado.")
                self._registrar_diagnostico("pausa_programada", resultado="pausado_no_lobby")
            elif evento in ("lendo_replay", "baus_replay"):
                if self.janela_replay and self.janela_replay.winfo_exists():
                    if evento == "lendo_replay":
                        self.janela_replay.mostrar_andamento(*valor)
                    else:
                        self.janela_replay.mostrar_resultado(*valor)
            elif evento == "prazo_saida":
                self.relogio.prazo_saida = valor
                self._proximo_relogio = 0.0
            elif evento == "teste_imagem":
                self.botao_testar.state(["!disabled"])
                if valor is None:
                    self.atividade.set("Não foi possível testar a imagem. Confira o arquivo e os ajustes.")
                else:
                    if self.atividade.get() == "Analisando imagem…":
                        self.atividade.set("Análise de imagem concluída.")
                    self.mostrar_teste(*valor)
            elif evento == "diagnostico_copiado":
                self._mostrar_diagnostico_copiado(valor)
            elif evento == "resgate_webhook":
                url, nome, quadro = valor
                if url == self.config.webhook_url:
                    self.iniciar_envio_webhook(url, nome, quadro)
            elif evento == "aviso_webhook":
                url, tipo, nome, quadro = valor
                if url == self.config.webhook_url:
                    self.iniciar_envio_webhook(url, nome, quadro, aviso=tipo)
            elif evento == "pausa_token" and valor is self.parar:
                self.atividade.set("Token da Loja Pirata desbloqueado. Automação pausada.")
            elif evento == "tesouro_webhook":
                url, nome, quadro, sessao = valor
                if (sessao is self.parar and not sessao.is_set() and self.config.notificar_tesouros
                        and url == self.config.webhook_url):
                    self.iniciar_envio_webhook(url, nome, quadro, tesouro=True)
            elif evento == "resultado_webhook":
                teste, sucesso, texto = valor
                if teste:
                    self.teste_webhook_em_curso = False
                    if self.botao_teste_webhook and self.botao_teste_webhook.winfo_exists():
                        self.botao_teste_webhook.state(["!disabled"])
                self.status_webhook.set(texto)
                self._registrar_diagnostico("webhook", teste=teste, enviado=sucesso, resultado=texto)
            elif evento == "erro" or (evento == "acao" and self.parar is not None and not self.parar.is_set()):
                self.atividade.set(valor)
        self.atualizar_pausa()
        agora = time.monotonic()
        if agora >= self._proximo_relogio:
            self._proximo_relogio = agora + 0.25
            self.atualizar_relogio(agora)
        if agora - self._ultima_persistencia_estado >= 10:
            self._ultima_persistencia_estado = agora
            self._salvar_estado_local()
        if self.fechando and (self.worker is None or not self.worker.is_alive()):
            self.janela.destroy()
        else:
            self._callback_interface = self.janela.after(45 if self.worker is not None else 65, self.atualizar)

    def atualizar_relogio(self, agora):
        saida, farmando = self.relogio.valores(agora)
        if saida != self.proxima_saida.get():
            self.proxima_saida.set(saida)
        if farmando != self.farmando.get():
            self.farmando.set(farmando)

    def fechar(self):
        self.fechando = True
        self.relogio.pausar(time.monotonic())
        self._salvar_estado_local()
        if self.parar:
            self.parar.set()

    def encerrar_por_vencimento(self):
        if not self.licenca_expirada:
            self.licenca_expirada = True
            self.fechar()
            self.janela.withdraw()
            for filha in self.janela.winfo_children():
                if isinstance(filha, tk.Toplevel):
                    filha.destroy()
        if self.worker is not None and self.worker.is_alive():
            self._callback_interface = self.janela.after(65, self.encerrar_por_vencimento)
        else:
            self.janela.destroy()


def verificar_build():
    from windows_capture import WindowsCapture
    if getattr(sys, "frozen", False):
        from baus_replay import verificar_leitor
        verificar_leitor()

    if not callable(WindowsCapture):
        raise RuntimeError("Captura de janela ausente no pacote.")
    detector = Detector()
    DetectorTesouros()
    MonitorLobby()
    if faltando := TEMPLATES.keys() - detector.templates.keys():
        raise RuntimeError("Imagens ausentes no pacote: " + ", ".join(sorted(faltando)))
    for nome, (x, y) in (("jogar", (650, 210)), ("continuar", (650, 448)), ("lobby_pronto", (20, 420)),
                        ("lobby_jogar", (20, 420))):
        template = detector.templates[nome]
        h, w = template.shape[:2]
        quadro = np.zeros((540, 960, 3), dtype=np.uint8)
        quadro[y:y + h, x:x + w] = template
        alvo = detector.procurar(quadro, (nome,))
        if not alvo or alvo.caixa != (x, y, w, h):
            raise RuntimeError(f"Falha ao detectar {nome} no pacote.")
    fluxo = Fluxo(detector)
    if fluxo.passo(quadro, 0) is not None or fluxo.passo(quadro.copy(), 0.3).alvo.nome != "lobby_jogar":
        raise RuntimeError("Falha na integração do fluxo de lobby.")
    onibus = np.zeros((1080, 1920, 3), dtype=np.uint8)
    aviso = detector.templates["onibus"]
    h, w = aviso.shape[:2]
    onibus[550:550 + h, 1150:1150 + w] = aviso
    reconhecido, score = detector.aviso_onibus(onibus)
    if reconhecido is None or score < 0.8:
        raise RuntimeError("Falha na leitura do aviso PARA PULAR.")
    fluxo_no_onibus = Fluxo(detector)
    if fluxo_no_onibus.passo(onibus, 0, no_onibus=True) is not None:
        raise RuntimeError("O salto exigia dois quadros do aviso.")
    salto = fluxo_no_onibus.passo(onibus.copy(), 0.3, no_onibus=True)
    if salto is None or salto.passos != (("teclas", (57,), 0.16),):
        raise RuntimeError("O salto precisa enviar somente Espaço, inclusive sem fila iniciada pelo app.")
    fluxo_no_onibus.embarque.registrar(salto.tipo, True, 0.46)
    descida = fluxo_no_onibus.passo(np.zeros_like(onibus), 0.72)
    if descida is None or descida.tipo != "movimento_aereo" or descida.passos != (("teclas", (17,), 0.8),):
        raise RuntimeError("A descida precisa iniciar o movimento com W após o salto.")
    asa = np.zeros_like(onibus)
    aviso = detector.templates["asa_delta"]
    h, w = aviso.shape[:2]
    asa[550:550 + h, 1150:1150 + w] = aviso
    alvo_asa, _ = detector.aviso_asa(asa)
    if alvo_asa is None:
        raise RuntimeError("O aviso Entrar em queda livre não foi reconhecido.")
    fluxo_asa = Fluxo(detector)
    fluxo_asa.passo(asa, 0, asa_delta=True)
    if (fechar_asa := fluxo_asa.passo(asa.copy(), 0.3, asa_delta=True)) is None or fechar_asa.tipo != "fechar_asa":
        raise RuntimeError("O app precisa fechar a asa-delta após dois quadros consecutivos.")
    hud = np.zeros((540, 960, 3), dtype=np.uint8)
    hud[475:486, 32:105] = (0, 215, 145)
    if detector.em_partida(hud):
        raise RuntimeError("Uma barra verde sem cruz foi confundida com HUD.")
    hud[477:484, 25:27] = 255
    hud[479:482, 23:30] = 255
    hud = hud.copy()
    if not detector.em_partida(hud):
        raise RuntimeError("Falha na leitura da barra de vida.")
    fluxo_sem_fila = Fluxo(detector)
    for instante in (0, 0.3, 0.8, 1.1, 1.5):
        fluxo_sem_fila.passo(hud, instante, partida=True)
    if fluxo_sem_fila.inicio_partida is not None or fluxo_sem_fila.partidas:
        raise RuntimeError("HUD já visível iniciou uma partida sem nova fila.")
    from PIL import ImageDraw, ImageFont
    fonte_aviso = ImageFont.truetype(str(Path(os.environ["WINDIR"]) / "Fonts" / "arialbd.ttf"), 25)
    aviso_texto = Image.new("RGB", (1280, 800), "#224463")
    ImageDraw.Draw(aviso_texto).text((855, 432), "ESPAÇO PARA PULAR", font=fonte_aviso,
                                   fill="white", anchor="mm")
    quadro_aviso = cv2.cvtColor(np.asarray(aviso_texto), cv2.COLOR_RGB2BGR)
    alvo_texto, _ = detector.aviso_onibus(quadro_aviso)
    if alvo_texto is None or alvo_texto.metodo != "texto":
        raise RuntimeError("O aviso do ônibus com outro estilo precisa ser reconhecido pelo texto.")
    modal = Image.new("RGB", (960, 540), "#17263a")
    fonte = ImageFont.truetype(str(Path(os.environ["WINDIR"]) / "Fonts" / "arialbd.ttf"), 34)
    desenho = ImageDraw.Draw(modal)
    desenho.text((480, 205), "VOLTAR AO LOBBY?", font=fonte, anchor="mm")
    desenho.text((360, 332), "CONFIRMAR", font=fonte, anchor="mm")
    desenho.text((610, 332), "CANCELAR", font=fonte, anchor="mm")
    confirmacao = detector.ler_contexto(cv2.cvtColor(np.asarray(modal), cv2.COLOR_RGB2BGR), "confirmar_sair")
    if confirmacao is None or not 300 < confirmacao.centro[0] < 420:
        raise RuntimeError("Falha na leitura do diálogo de saída.")
    modal_ingles = Image.new("RGB", (960, 540), "#17263a")
    desenho = ImageDraw.Draw(modal_ingles)
    desenho.text((480, 162), "RETURN TO", font=fonte, anchor="mm")
    desenho.text((480, 207), "LOBBY?", font=fonte, anchor="mm")
    desenho.text((342, 332), "CONFIRM", font=fonte, anchor="mm")
    desenho.text((612, 332), "CANCEL", font=fonte, anchor="mm")
    confirmacao = detector.ler_contexto(cv2.cvtColor(np.asarray(modal_ingles), cv2.COLOR_RGB2BGR),
                                       "confirmar_sair")
    if confirmacao is None or not 300 < confirmacao.centro[0] < 400:
        raise RuntimeError("Falha na leitura do diálogo com título em duas linhas.")
    botao = Image.new("RGB", (960, 540), "#17263a")
    desenho = ImageDraw.Draw(botao)
    fonte_botao = ImageFont.truetype(str(Path(os.environ["WINDIR"]) / "Fonts" / "arialbd.ttf"), 23)
    desenho.rectangle((600, 220, 849, 269), fill="#ffff00")
    desenho.text((724, 245), "JOGAR   NOVAMENTE", font=fonte_botao, anchor="mm", fill="#101010")
    alvo = detector.detectar(cv2.cvtColor(np.asarray(botao), cv2.COLOR_RGB2BGR))
    if alvo is None or alvo.metodo != "cor+OCR" or not 650 < alvo.centro[0] < 790:
        raise RuntimeError("Falha na leitura por cor e texto de Jogar Novamente.")

    sem_rotulo = Image.new("RGB", (960, 540), "#17263a")
    ImageDraw.Draw(sem_rotulo).rectangle((600, 220, 849, 269), fill="#ffff00")
    if detector.detectar(cv2.cvtColor(np.asarray(sem_rotulo), cv2.COLOR_RGB2BGR)) is not None:
        raise RuntimeError("Uma caixa amarela sem rótulo foi confundida com Jogar Novamente.")

    botoes = Image.new("RGB", (960, 540), "#17263a")
    desenho = ImageDraw.Draw(botoes)
    for (x0, x1), palavra in (((600, 770), "JOGAR"), ((778, 948), "NOVAMENTE")):
        desenho.rectangle((x0, 220, x1, 269), fill="#ffff00")
        desenho.text(((x0 + x1) / 2, 245), palavra, font=fonte_botao, anchor="mm", fill="#101010")
    if detector.detectar(cv2.cvtColor(np.asarray(botoes), cv2.COLOR_RGB2BGR)) is not None:
        raise RuntimeError("Dois botões amarelos foram confundidos com Jogar Novamente.")

    empilhado = Image.new("RGB", (960, 540), "#17263a")
    desenho = ImageDraw.Draw(empilhado)
    desenho.text((480, 178), "RETURN TO LOBBY?", font=fonte, anchor="mm")
    desenho.text((480, 275), "CANCEL", font=fonte, anchor="mm")
    desenho.text((480, 335), "CONFIRM", font=fonte, anchor="mm")
    confirmacao = detector.ler_contexto(cv2.cvtColor(np.asarray(empilhado), cv2.COLOR_RGB2BGR),
                                       "confirmar_sair")
    if confirmacao is None or not 310 < confirmacao.centro[1] < 350:
        raise RuntimeError("Falha na leitura do diálogo com Cancelar acima de Confirmar.")

    duplo = Image.new("RGB", (960, 540), "#17263a")
    desenho = ImageDraw.Draw(duplo)
    for centro, titulo in ((310, "LEAVE MATCH?"), (635, "RETURN TO LOBBY?")):
        desenho.text((centro, 180), titulo, font=fonte, anchor="mm")
        desenho.text((centro - 67, 313), "CONFIRM", font=fonte_botao, anchor="mm")
        desenho.text((centro + 67, 313), "CANCEL", font=fonte_botao, anchor="mm")
    confirmacao = detector.ler_contexto(cv2.cvtColor(np.asarray(duplo), cv2.COLOR_RGB2BGR), "confirmar_sair")
    if confirmacao is not None or not detector.dialogo_ambiguo:
        raise RuntimeError("O OCR não bloqueou uma confirmação ambígua.")
    windows = Windows()
    janela = tk.Tk()
    janela.withdraw()
    painel = Painel(janela, windows, config=Config())
    janela.update_idletasks()
    if getattr(sys, "frozen", False):
        fonte = tkfont.Font(janela, font=ttk.Style(janela).lookup("Metrica.TLabel", "font"))
        if fonte.actual("family") != "Inter Auto Farm" or len({fonte.measure(n) for n in "0123456789"}) != 1:
            raise RuntimeError("As fontes tabulares do painel não foram carregadas.")
    if painel.botao.cget("state") == "disabled":
        raise RuntimeError("O painel pausado deve permitir iniciar.")
    janela.destroy()


def executar_painel(janela, licenca):
    windows = Windows()
    try:
        diagnostico = Diagnostico(ARQUIVO_LOG)
    except OSError:
        diagnostico = None
    painel = Painel(janela, windows, diagnostico=diagnostico, expira=licenca.expira, por_venda=licenca.por_venda)
    if diagnostico is None:
        painel.atividade.set("O diagnóstico local não pôde ser criado.")
    try:
        windows.iniciar_atalho()
        janela.deiconify()
        janela.mainloop()
    finally:
        windows.fechar_atalho()
        painel.salvamento.shutdown()
        if diagnostico:
            diagnostico.fechar()
    return painel.licenca_expirada


def main():
    configurar_dpi()
    carregar_inter()
    if sys.argv[1:] == ["--verificar-build"]:
        verificar_build()
        return
    while True:
        janela = tk.Tk()
        janela.withdraw()
        licenca = obter_licenca(janela)
        if licenca is None:
            janela.destroy()
            return
        if not executar_painel(janela, licenca):
            return


if __name__ == "__main__":
    main()
