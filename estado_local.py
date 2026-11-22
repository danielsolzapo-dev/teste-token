"""Persistência local de contagem de partidas e tempo de farm."""

import json
import os
from pathlib import Path
import tempfile


def caminho_estado():
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return local / "AutoFarmToken" / "estado.json"


def carregar_estado():
    padrao = {"partidas": 0, "farmando_segundos": 0}
    try:
        dados = json.loads(caminho_estado().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao
    if not isinstance(dados, dict):
        return padrao
    partidas = dados.get("partidas")
    segundos = dados.get("farmando_segundos")
    if type(partidas) is int and partidas >= 0:
        padrao["partidas"] = partidas
    if type(segundos) in (int, float) and 0 <= segundos <= 10**10:
        padrao["farmando_segundos"] = int(segundos)
    return padrao


def salvar_estado(estado):
    destino = caminho_estado()
    destino.parent.mkdir(parents=True, exist_ok=True)
    descritor, temporario = tempfile.mkstemp(dir=destino.parent, prefix=".estado-", suffix=".tmp")
    try:
        with os.fdopen(descritor, "w", encoding="utf-8", newline="\n") as arquivo:
            json.dump({"partidas": max(0, int(estado["partidas"])),
                       "farmando_segundos": max(0, int(estado["farmando_segundos"]))}, arquivo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
    finally:
        Path(temporario).unlink(missing_ok=True)
