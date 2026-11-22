"""Janela de contagem dos baús registrados em um replay do Fortnite."""

from pathlib import Path
from threading import Thread
import tkinter as tk
from tkinter import filedialog, ttk

import baus_replay
import historico_baus


class JanelaBaus(tk.Toplevel):
    def __init__(self, pai, eventos):
        super().__init__(pai)
        self.title("Baús do replay")
        self.transient(pai)
        self.protocol("WM_DELETE_WINDOW", self.ocultar)
        self.eventos = eventos
        self.analise = None
        self.pedido_manual = None
        self.assinatura_ultimo = None
        self.agendamento = None
        self.aberta = False
        self.arquivo = tk.StringVar(self)
        self.voce = tk.StringVar(self, "—")
        self.esquadrao = tk.StringVar(self, "—")
        self.detalhe = tk.StringVar(self, "Procurando o último replay concluído…")
        self.historico = historico_baus.carregar()
        self.hoje = tk.StringVar(self)
        self.melhor_dia = tk.StringVar(self)
        self.recorde = tk.StringVar(self)
        self.media = tk.StringVar(self)
        self.ultima_partida = tk.StringVar(self)

        painel = ttk.Frame(self, padding=18)
        painel.pack(fill="both", expand=True)
        acoes = ttk.Frame(painel)
        acoes.pack(fill="x")
        self.ultimo = ttk.Button(acoes, text="Analisar último replay", command=self.iniciar)
        self.ultimo.pack(side="left")
        self.escolher = ttk.Button(acoes, text="Escolher arquivo…", command=self.escolher_arquivo)
        self.escolher.pack(side="left", padx=(8, 0))
        ttk.Label(painel, textvariable=self.arquivo, wraplength=400).pack(anchor="w", pady=(10, 6))

        contagens = ttk.Frame(painel)
        contagens.pack(fill="x")
        for coluna, (rotulo, valor) in enumerate((("Você", self.voce), ("Esquadrão (inclui você)", self.esquadrao))):
            contagens.columnconfigure(coluna, weight=1, uniform="totais")
            card = ttk.Frame(contagens, style="Metrica.Card.TFrame", padding=(12, 8))
            card.grid(row=0, column=coluna, sticky="ew", padx=(0 if coluna == 0 else 6, 0))
            ttk.Label(card, text=rotulo, style="MetricaInfo.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=valor, style="MetricaCard.TLabel").pack(anchor="w")
        ttk.Label(painel, textvariable=self.detalhe, style="Info.TLabel", wraplength=400,
                  justify="left").pack(anchor="w", pady=(10, 0))
        resumo = ttk.LabelFrame(painel, text="Histórico", padding=10)
        resumo.pack(fill="x", pady=(12, 0))
        for indice, (rotulo, variavel) in enumerate((
                ("Hoje · squad", self.hoje), ("Melhor dia", self.melhor_dia),
                ("Recorde por partida", self.recorde), ("Média por partida", self.media))):
            ttk.Label(resumo, text=rotulo, style="MetricaInfo.TLabel").grid(
                row=indice // 2 * 2, column=indice % 2, sticky="w", padx=(0, 12), pady=(2, 0))
            ttk.Label(resumo, textvariable=variavel, style="MetricaCard.TLabel").grid(
                row=indice // 2 * 2 + 1, column=indice % 2, sticky="w", padx=(0, 12), pady=(0, 5))
        ttk.Label(resumo, text="Baús do squad nos últimos 7 dias", style="MetricaInfo.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        estilo = ttk.Style()
        self.grafico = tk.Canvas(resumo, height=112, highlightthickness=0,
                                 background=estilo.lookup("Card.TFrame", "background") or "#111925")
        self.grafico.grid(row=5, column=0, columnspan=2, sticky="ew")
        resumo.columnconfigure(0, weight=1)
        resumo.columnconfigure(1, weight=1)
        self.grafico.bind("<Configure>", lambda _: self._desenhar_grafico())
        ttk.Label(resumo, textvariable=self.ultima_partida, style="Info.TLabel", wraplength=390).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Label(painel, text="Novos replays são verificados enquanto o Auto Farm estiver aberto.",
                  style="Info.TLabel", wraplength=400).pack(anchor="w", pady=(8, 0))
        self._atualizar_historico()

    def _atualizar_historico(self):
        resumo = historico_baus.resumir(self.historico)
        self.hoje.set(str(resumo["hoje"]))
        melhor = resumo["melhor_dia"]
        dia_melhor = f"{melhor[0][8:10]}/{melhor[0][5:7]}" if melhor else "—"
        self.melhor_dia.set(f"{dia_melhor} · {melhor[1]}" if melhor else "—")
        recorde = resumo["recorde_partida"]
        media = resumo["media_partida"]
        self.recorde.set("—" if recorde is None else str(recorde))
        self.media.set("—" if media is None else f"{media:.1f}".replace(".", ","))
        ultima = resumo["ultima_partida"]
        self.ultima_partida.set("Última partida: —" if ultima is None else
                                f"Última partida: você {ultima['voce'] if ultima['voce'] is not None else '—'} · "
                                f"squad {ultima['esquadrao']}")
        self._desenhar_grafico()

    def _desenhar_grafico(self):
        if not hasattr(self, "grafico") or not self.grafico.winfo_exists():
            return
        self.grafico.delete("all")
        pontos = historico_baus.resumir(self.historico)["ultimos_7_dias"]
        largura = max(280, self.grafico.winfo_width())
        altura = max(90, self.grafico.winfo_height())
        margem_x, margem_topo, margem_base = 12, 10, 22
        base = altura - margem_base
        maximo = max((valor for _, valor in pontos), default=0) or 1
        estilo = ttk.Style()
        cor = estilo.lookup("MetricaCard.TLabel", "foreground") or "#B8F36D"
        passo = (largura - 2 * margem_x) / 7
        for indice, (dia, valor) in enumerate(pontos):
            x1 = margem_x + indice * passo + passo * 0.2
            x2 = margem_x + (indice + 1) * passo - passo * 0.2
            topo = base - (base - margem_topo) * valor / maximo if valor else base - 1
            self.grafico.create_rectangle(x1, topo, x2, base, fill=cor, outline="")
            self.grafico.create_text((x1 + x2) / 2, max(6, topo - 7), text=str(valor),
                                     fill=cor, font=("Segoe UI", 8))
            self.grafico.create_text((x1 + x2) / 2, altura - 9, text=dia[8:],
                                     fill=estilo.lookup("Info.TLabel", "foreground") or "#A0AEC0",
                                     font=("Segoe UI", 8))

    def abrir(self):
        self.deiconify()
        self.lift()
        if not self.aberta:
            self.aberta = True
            self.iniciar(automatica=True)

    def ocultar(self):
        self.aberta = False
        self.cancelar_agendamento()
        self.withdraw()

    def cancelar_agendamento(self):
        if self.agendamento is not None:
            self.after_cancel(self.agendamento)
            self.agendamento = None

    def destroy(self):
        self.cancelar_agendamento()
        super().destroy()

    def escolher_arquivo(self):
        arquivo = filedialog.askopenfilename(parent=self, title="Escolher replay do Fortnite",
                                             initialdir=baus_replay.pasta_replays(),
                                             filetypes=[("Replay do Fortnite", "*.replay")])
        if arquivo:
            self.iniciar(Path(arquivo))

    def iniciar(self, caminho=None, *, automatica=False):
        if self.analise is not None:
            if not automatica:
                self.pedido_manual = (caminho,)
            return
        self.cancelar_agendamento()
        self.analise = object()
        if not automatica:
            self.mostrar_andamento(self.analise, caminho)
        Thread(target=self._ler, args=(caminho, self.analise, self.eventos, self.assinatura_ultimo,
                                      automatica), daemon=True).start()

    @staticmethod
    def _ler(caminho, analise, eventos, assinatura, automatica):
        resultado = detalhe = None
        try:
            # Ao escolher uma gravação antiga, guarda o último replay atual para
            # trocar de resultado somente quando o jogo salvar uma nova partida.
            try:
                ultimo = baus_replay.ultimo_replay()
                dados = ultimo.stat()
                atual = (ultimo, dados.st_size, dados.st_mtime_ns)
            except (ValueError, OSError):
                if caminho is None:
                    raise
                atual = None
            if caminho is None:
                if automatica and atual == assinatura:
                    return
                caminho = ultimo
            assinatura = atual
            eventos.put(("lendo_replay", (analise, caminho)))
            resultado = baus_replay.analisar_replay(caminho)
            detalhe = resultado.detalhe
        except (ValueError, OSError) as erro:
            detalhe = str(erro)
        except Exception:
            detalhe = "Falha ao analisar o replay. Tente outra gravação."
        finally:
            eventos.put(("baus_replay", (analise, caminho, resultado, detalhe, assinatura, automatica)))

    def mostrar_andamento(self, analise, caminho):
        if analise is self.analise and self.pedido_manual is None:
            self.ultimo.state(["disabled"])
            self.escolher.state(["disabled"])
            self.voce.set("—")
            self.esquadrao.set("—")
            self.arquivo.set(caminho.name if caminho else "")
            self.detalhe.set("Analisando o replay…" if caminho else "Procurando o último replay concluído…")

    def mostrar_resultado(self, analise, caminho, resultado, detalhe, assinatura, automatica):
        if analise is not self.analise:
            return
        self.analise = None
        self.assinatura_ultimo = assinatura
        self.ultimo.state(["!disabled"])
        self.escolher.state(["!disabled"])
        if self.pedido_manual is not None:
            caminho_pedido, = self.pedido_manual
            self.pedido_manual = None
            self.iniciar(caminho_pedido)
            return
        if automatica and resultado is not None and assinatura is not None:
            novo_historico, atualizado = historico_baus.registrar(
                self.historico, caminho, assinatura, resultado)
            if atualizado:
                self.historico = novo_historico
                try:
                    historico_baus.salvar(self.historico)
                except OSError:
                    detalhe = (detalhe + " Histórico local não pôde ser salvo.") if detalhe else \
                        "Histórico local não pôde ser salvo."
                self._atualizar_historico()
        if detalhe is not None and (caminho is not None or not automatica or not self.arquivo.get()):
            self.arquivo.set(caminho.name if caminho else "")
            if resultado is not None:
                self.voce.set("—" if resultado.voce is None else str(resultado.voce))
                self.esquadrao.set("—" if resultado.esquadrao is None else str(resultado.esquadrao))
            if self.detalhe.get() != detalhe:
                self.detalhe.set(detalhe)
        if self.winfo_exists():
            self.agendamento = self.after(30_000, lambda: self.iniciar(automatica=True))
