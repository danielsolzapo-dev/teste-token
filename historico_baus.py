"""Histórico local de baús lidos em replays concluídos."""

from collections import defaultdict
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile


def caminho_historico():
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return local / "AutoFarmToken" / "historico_baus.json"


def carregar():
    try:
        dados = json.loads(caminho_historico().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(dados, list):
        return []
    resultado = []
    for entrada in dados[-1000:]:
        if not isinstance(entrada, dict):
            continue
        dia, assinatura = entrada.get("dia"), entrada.get("assinatura")
        esquadrao, voce = entrada.get("esquadrao"), entrada.get("voce")
        try:
            date.fromisoformat(dia)
        except (TypeError, ValueError):
            continue
        if (not isinstance(assinatura, str) or not assinatura
                or type(esquadrao) is not int or esquadrao < 0
                or (voce is not None and (type(voce) is not int or voce < 0))):
            continue
        resultado.append({"dia": dia, "assinatura": assinatura, "esquadrao": esquadrao,
                          "voce": voce, "arquivo": str(entrada.get("arquivo", ""))[:180]})
    return resultado


def salvar(historico):
    destino = caminho_historico()
    destino.parent.mkdir(parents=True, exist_ok=True)
    descritor, temporario = tempfile.mkstemp(dir=destino.parent, prefix=".baus-", suffix=".tmp")
    try:
        with os.fdopen(descritor, "w", encoding="utf-8", newline="\n") as arquivo:
            json.dump(historico[-1000:], arquivo, ensure_ascii=False)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
    finally:
        Path(temporario).unlink(missing_ok=True)


def registrar(historico, caminho, assinatura, resultado, dia=None):
    if resultado is None or type(resultado.esquadrao) is not int or resultado.esquadrao < 0:
        return historico, False
    if assinatura is None:
        return historico, False
    arquivo, tamanho, modificado = assinatura
    identidade = f"{Path(arquivo).resolve()}|{tamanho}|{modificado}"
    identificador = hashlib.sha256(identidade.encode("utf-8", errors="replace")).hexdigest()
    if any(item["assinatura"] == identificador for item in historico):
        return historico, False
    voce = resultado.voce if type(resultado.voce) is int and resultado.voce >= 0 else None
    entrada = {"dia": (dia or date.today()).isoformat(), "assinatura": identificador,
               "esquadrao": resultado.esquadrao, "voce": voce,
               "arquivo": Path(caminho).name if caminho else ""}
    return [*historico, entrada][-1000:], True


def resumir(historico, hoje=None):
    hoje = hoje or date.today()
    por_dia = defaultdict(int)
    partidas = [item["esquadrao"] for item in historico]
    for item in historico:
        por_dia[item["dia"]] += item["esquadrao"]
    melhor = max(por_dia.items(), key=lambda item: (item[1], item[0]), default=None)
    datas = [(hoje - timedelta(days=6 - indice)).isoformat() for indice in range(7)]
    return {
        "hoje": por_dia.get(hoje.isoformat(), 0),
        "melhor_dia": melhor,
        "recorde_partida": max(partidas, default=None),
        "media_partida": sum(partidas) / len(partidas) if partidas else None,
        "ultimos_7_dias": [(dia, por_dia.get(dia, 0)) for dia in datas],
        "ultima_partida": historico[-1] if historico else None,
    }
