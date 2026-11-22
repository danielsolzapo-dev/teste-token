"""Verificação e instalação segura de versões publicadas no GitHub Releases."""

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DONO = "danielsolzapo-dev"
REPOSITORIO = "teste-token"
NOME_EXECUTAVEL = "AutoFarmTokenPirata.exe"
URL_API = f"https://api.github.com/repos/{DONO}/{REPOSITORIO}/releases/latest"
TAMANHO_MAXIMO = 500 * 1024 * 1024
VERSAO_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ErroAtualizacao(RuntimeError):
    """Falha verificável ao consultar, validar ou preparar uma atualização."""


@dataclass(frozen=True)
class Release:
    versao: str
    tag: str
    url: str
    sha256: str
    tamanho: int
    pagina: str


def _versao_numerica(versao):
    correspondencia = VERSAO_RE.fullmatch(str(versao))
    if not correspondencia:
        raise ErroAtualizacao(f"Versão inválida no release: {versao!r}")
    return tuple(int(parte) for parte in correspondencia.groups())


def _url_asset_segura(url, nome_asset=NOME_EXECUTAVEL):
    if not isinstance(url, str):
        raise ErroAtualizacao("O release contém uma URL de download inválida.")
    try:
        endereco = urlparse(url)
        porta = endereco.port
    except ValueError as erro:
        raise ErroAtualizacao("O release contém uma URL de download inválida.") from erro
    prefixo = f"/{DONO}/{REPOSITORIO}/releases/download/"
    if (endereco.scheme != "https" or endereco.hostname != "github.com"
            or porta is not None or not endereco.path.startswith(prefixo)
            or not endereco.path.endswith("/" + nome_asset) or endereco.query or endereco.fragment
            or endereco.username or endereco.password):
        raise ErroAtualizacao("O release contém uma URL de download inesperada.")
    return url


def _ler_resposta(url, limite, abrir_url):
    requisicao = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "AutoFarmToken-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with abrir_url(requisicao, timeout=10) as resposta:
        dados = resposta.read(limite + 1)
        final = resposta.geturl() if hasattr(resposta, "geturl") else url
    if len(dados) > limite:
        raise ErroAtualizacao("A resposta do release excede o tamanho permitido.")
    final_url = urlparse(final)
    if (final_url.scheme != "https" or not (
            final_url.hostname == "github.com"
            or final_url.hostname == "api.github.com"
            or (final_url.hostname and final_url.hostname.endswith(".githubusercontent.com")))):
        raise ErroAtualizacao("O GitHub redirecionou o download para um host não permitido.")
    return dados


def _checksum_asset(assets, abrir_url):
    asset = next((item for item in assets
                  if isinstance(item, dict)
                  and item.get("name") == NOME_EXECUTAVEL + ".sha256"), None)
    if asset is None:
        raise ErroAtualizacao("O release não publica um SHA-256 para o executável.")
    url = _url_asset_segura(
        asset.get("browser_download_url", ""), NOME_EXECUTAVEL + ".sha256",
    )
    try:
        texto = _ler_resposta(url, 2048, abrir_url).decode("ascii").strip()
    except UnicodeDecodeError as erro:
        raise ErroAtualizacao("O arquivo SHA-256 do release não está em ASCII.") from erro
    correspondencia = re.fullmatch(
        rf"([0-9a-fA-F]{{64}})(?:\s+\*?{re.escape(NOME_EXECUTAVEL)})?",
        texto,
    )
    if not correspondencia:
        raise ErroAtualizacao("O arquivo SHA-256 do release está malformado.")
    return correspondencia.group(1).lower()


def buscar_atualizacao(versao_atual, abrir_url=None):
    """Retorna o release mais recente se for mais novo; nunca baixa código nessa etapa."""
    abrir_url = abrir_url or urlopen
    versao_instalada = _versao_numerica(versao_atual)
    try:
        dados = json.loads(_ler_resposta(URL_API, 2_000_000, abrir_url))
    except ErroAtualizacao:
        raise
    except (URLError, TimeoutError, OSError, ValueError) as erro:
        raise ErroAtualizacao(f"Não foi possível consultar os releases do GitHub: {erro}") from erro
    if not isinstance(dados, dict) or not isinstance(dados.get("assets"), list):
        raise ErroAtualizacao("A resposta do GitHub não contém um release válido.")
    tag = dados.get("tag_name", "")
    versao = tag[1:] if isinstance(tag, str) and tag.startswith("v") else tag
    chave_versao = _versao_numerica(versao)
    if chave_versao <= versao_instalada:
        return None
    asset = next((item for item in dados["assets"]
                  if isinstance(item, dict) and item.get("name") == NOME_EXECUTAVEL), None)
    if asset is None:
        raise ErroAtualizacao("O release não contém o executável esperado.")
    tamanho = asset.get("size")
    if type(tamanho) is not int or not 0 < tamanho <= TAMANHO_MAXIMO:
        raise ErroAtualizacao("O tamanho do executável no release é inválido.")
    url = _url_asset_segura(asset.get("browser_download_url", ""))
    digest = asset.get("digest", "")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        digest = digest.removeprefix("sha256:").lower()
    else:
        try:
            digest = _checksum_asset(dados["assets"], abrir_url)
        except ErroAtualizacao:
            raise
        except (URLError, TimeoutError, OSError) as erro:
            raise ErroAtualizacao(f"Não foi possível obter o SHA-256 do release: {erro}") from erro
    if not SHA256_RE.fullmatch(digest):
        raise ErroAtualizacao("O release não contém um SHA-256 válido para o executável.")
    pagina = f"https://github.com/{DONO}/{REPOSITORIO}/releases/tag/{quote(tag, safe='v.-_')}"
    return Release(versao, tag, url, digest, tamanho, pagina)


def baixar_atualizacao(release, diretorio, abrir_url=None):
    """Baixa o executável em arquivo temporário e publica-o só após validar hash e tamanho."""
    abrir_url = abrir_url or urlopen
    url = _url_asset_segura(release.url)
    diretorio = Path(diretorio)
    diretorio.mkdir(parents=True, exist_ok=True)
    descritor, temporario = tempfile.mkstemp(prefix=".autofarm-", suffix=".part", dir=diretorio)
    temporario = Path(temporario)
    sha = hashlib.sha256()
    total = 0
    try:
        requisicao = Request(url, headers={"User-Agent": "AutoFarmToken-Updater"})
        with os.fdopen(descritor, "wb") as arquivo:
            with abrir_url(requisicao, timeout=30) as resposta:
                final = urlparse(resposta.geturl() if hasattr(resposta, "geturl") else url)
                if (final.scheme != "https" or not (
                        final.hostname == "github.com"
                        or (final.hostname and final.hostname.endswith(".githubusercontent.com")))):
                    raise ErroAtualizacao("O GitHub redirecionou o executável para um host não permitido.")
                while True:
                    bloco = resposta.read(1024 * 1024)
                    if not bloco:
                        break
                    total += len(bloco)
                    if total > release.tamanho or total > TAMANHO_MAXIMO:
                        raise ErroAtualizacao("O executável baixado excede o tamanho anunciado.")
                    sha.update(bloco)
                    arquivo.write(bloco)
                arquivo.flush()
                os.fsync(arquivo.fileno())
        if total != release.tamanho:
            raise ErroAtualizacao("O tamanho do executável baixado não corresponde ao release.")
        if sha.hexdigest() != release.sha256:
            raise ErroAtualizacao("O SHA-256 do executável não corresponde ao release.")
        destino = diretorio / NOME_EXECUTAVEL
        os.replace(temporario, destino)
        return destino
    except ErroAtualizacao:
        raise
    except (URLError, TimeoutError, OSError) as erro:
        raise ErroAtualizacao(f"Falha ao baixar a atualização: {erro}") from erro
    finally:
        temporario.unlink(missing_ok=True)


def preparar_executavel(baixado, destino, sha256):
    """Copia a versão verificada ao lado do app para permitir uma troca local após sair."""
    baixado, destino = Path(baixado), Path(destino).resolve()
    if not destino.is_file():
        raise ErroAtualizacao("Não encontrei o executável atual para atualizar.")
    descritor, temporario = tempfile.mkstemp(
        prefix=f".{destino.stem}-", suffix=".update", dir=destino.parent,
    )
    os.close(descritor)
    temporario = Path(temporario)
    try:
        shutil.copyfile(baixado, temporario)
        sha = hashlib.sha256()
        with temporario.open("rb") as arquivo:
            for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
                sha.update(bloco)
        if sha.hexdigest() != sha256:
            raise ErroAtualizacao("A cópia local não passou na validação SHA-256.")
        return temporario
    except (ErroAtualizacao, OSError):
        temporario.unlink(missing_ok=True)
        raise


def criar_script_instalacao(executavel_novo, destino, pid, diretorio_dados, sha256=None):
    """Gera um helper PowerShell com caminhos transportados em JSON codificado em Base64."""
    diretorio_dados = Path(diretorio_dados)
    diretorio_dados.mkdir(parents=True, exist_ok=True)
    script = diretorio_dados / f"instalar-atualizacao-{pid}.ps1"
    dados = json.dumps({
        "pid": int(pid),
        "novo": str(Path(executavel_novo).resolve()),
        "destino": str(Path(destino).resolve()),
        "log": str((diretorio_dados.parent / "atualizacao.log").resolve()),
        "sha256": sha256 or hashlib.sha256(Path(executavel_novo).read_bytes()).hexdigest(),
    }, ensure_ascii=True).encode("utf-8")
    codificado = base64.b64encode(dados).decode("ascii")
    conteudo = r"""
$ErrorActionPreference = 'Stop'
$data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@@PAYLOAD@@')) | ConvertFrom-Json
function Write-UpdateLog([string]$message) {
    try { Add-Content -LiteralPath $data.log -Value ("{0:o} {1}" -f (Get-Date), $message) -Encoding UTF8 } catch {}
}
$trocado = $false
$backup = $data.destino + '.previous'
try {
    $limite = (Get-Date).AddSeconds(120)
    while (Get-Process -Id ([int]$data.pid) -ErrorAction SilentlyContinue) {
        if ((Get-Date) -ge $limite) { throw 'Application did not close in time.' }
        Start-Sleep -Milliseconds 250
    }
    $arquivo = [System.IO.File]::OpenRead($data.novo)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $hash = [BitConverter]::ToString($sha256.ComputeHash($arquivo)).Replace('-', '')
        } finally { $sha256.Dispose() }
    } finally { $arquivo.Dispose() }
    if ($hash -ne $data.sha256) { throw 'Update SHA-256 changed before installation.' }
    for ($i = 0; $i -lt 20; $i++) {
        try {
            [System.IO.File]::Replace($data.novo, $data.destino, $backup)
            $trocado = $true
            break
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $trocado) { throw 'Could not replace the executable.' }
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $data.destino
    Write-UpdateLog 'Update installed; application restarted.'
} catch {
    Write-UpdateLog ("Installation failed: " + $_.Exception.Message)
    try {
        if ($trocado -and (Test-Path -LiteralPath $backup)) {
            [System.IO.File]::Replace($backup, $data.destino, $backup + '.failed')
        }
        if (Test-Path -LiteralPath $data.destino) { Start-Process -FilePath $data.destino }
    } catch { Write-UpdateLog ("Restart failed: " + $_.Exception.Message) }
    exit 1
}
"""
    script.write_text(conteudo.replace("@@PAYLOAD@@", codificado).lstrip(), encoding="ascii")
    return script


def iniciar_instalacao(script, popen=None):
    """Inicia o helper oculto no Windows; o helper espera este processo sair."""
    if sys.platform != "win32":
        raise ErroAtualizacao("A instalação automática só está disponível no Windows.")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise ErroAtualizacao("O PowerShell do Windows não foi encontrado.")
    iniciar = popen or subprocess.Popen
    comando = base64.b64encode(Path(script).read_text(encoding="ascii").encode("utf-16le")).decode("ascii")
    return iniciar(
        [powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", comando],
        close_fds=True, creationflags=0x08000000,
    )
