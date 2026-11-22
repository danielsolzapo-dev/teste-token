import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import atualizador
from atualizador import ErroAtualizacao, Release


class RespostaSimulada:
    def __init__(self, dados, url):
        self._dados = BytesIO(dados)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, tamanho=-1):
        return self._dados.read(tamanho)

    def geturl(self):
        return self.url


def release_json(versao, executavel, digest=None, sidecar=None):
    assets = [{
        "name": atualizador.NOME_EXECUTAVEL,
        "size": len(executavel),
        "digest": f"sha256:{digest}" if digest else None,
        "browser_download_url": (
            f"https://github.com/{atualizador.DONO}/{atualizador.REPOSITORIO}"
            f"/releases/download/v{versao}/{atualizador.NOME_EXECUTAVEL}"
        ),
    }]
    if sidecar is not None:
        assets.append({
            "name": atualizador.NOME_EXECUTAVEL + ".sha256",
            "size": len(sidecar),
            "browser_download_url": (
                f"https://github.com/{atualizador.DONO}/{atualizador.REPOSITORIO}"
                f"/releases/download/v{versao}/{atualizador.NOME_EXECUTAVEL}.sha256"
            ),
        })
    return {
        "tag_name": f"v{versao}",
        "html_url": "https://github.com/invalid/untrusted",
        "assets": assets,
    }


class AtualizadorTest(unittest.TestCase):
    def test_detecta_versao_nova_e_constroi_pagina_canonica(self):
        arquivo = b"binary"
        digest = sha256(arquivo).hexdigest()
        resposta = json.dumps(release_json("2.0.7", arquivo, digest)).encode()
        release = atualizador.buscar_atualizacao(
            "2.0.6",
            abrir_url=lambda request, timeout: RespostaSimulada(resposta, atualizador.URL_API),
        )
        self.assertEqual(release.versao, "2.0.7")
        self.assertEqual(release.sha256, digest)
        self.assertEqual(release.pagina, "https://github.com/danielsolzapo-dev/teste-token/releases/tag/v2.0.7")

    def test_nao_oferece_mesma_versao(self):
        resposta = json.dumps(release_json("2.0.6", b"x", sha256(b"x").hexdigest())).encode()
        self.assertIsNone(atualizador.buscar_atualizacao(
            "2.0.6",
            abrir_url=lambda request, timeout: RespostaSimulada(resposta, atualizador.URL_API),
        ))

    def test_usa_checksum_publicado_quando_api_nao_fornece_digest(self):
        arquivo = b"binary"
        digest = sha256(arquivo).hexdigest()
        sidecar = f"{digest}  {atualizador.NOME_EXECUTAVEL}\n".encode("ascii")
        release_data = json.dumps(release_json("2.0.7", arquivo, sidecar=sidecar)).encode()

        def abrir(request, timeout):
            if request.full_url == atualizador.URL_API:
                return RespostaSimulada(release_data, request.full_url)
            return RespostaSimulada(sidecar, request.full_url)

        self.assertEqual(atualizador.buscar_atualizacao("2.0.6", abrir).sha256, digest)

    def test_recusa_url_de_asset_fora_do_repositorio(self):
        arquivo = b"x"
        dados = release_json("2.0.7", arquivo, sha256(arquivo).hexdigest())
        dados["assets"][0]["browser_download_url"] = "https://evil.example/AutoFarmTokenPirata.exe"
        resposta = json.dumps(dados).encode()
        with self.assertRaises(ErroAtualizacao):
            atualizador.buscar_atualizacao(
                "2.0.6",
                abrir_url=lambda request, timeout: RespostaSimulada(resposta, atualizador.URL_API),
            )

    def test_baixa_e_confere_tamanho_e_sha256_sem_rede(self):
        arquivo = b"portable-app-bytes"
        release = Release(
            "2.0.7", "v2.0.7",
            "https://github.com/danielsolzapo-dev/teste-token/releases/download/v2.0.7/"
            "AutoFarmTokenPirata.exe",
            sha256(arquivo).hexdigest(), len(arquivo), "https://github.com/danielsolzapo-dev/teste-token/releases/tag/v2.0.7",
        )
        with tempfile.TemporaryDirectory() as diretorio:
            caminho = atualizador.baixar_atualizacao(
                release, diretorio,
                abrir_url=lambda *_args, **_kwargs: RespostaSimulada(
                    arquivo, "https://release-assets.githubusercontent.com/object",
                ),
            )
            self.assertEqual(caminho.read_bytes(), arquivo)

    def test_sha256_incorreto_nao_publica_o_arquivo_final(self):
        arquivo = b"tampered"
        release = Release(
            "2.0.7", "v2.0.7",
            "https://github.com/danielsolzapo-dev/teste-token/releases/download/v2.0.7/"
            "AutoFarmTokenPirata.exe",
            sha256(b"expected").hexdigest(), len(arquivo), "",
        )
        with tempfile.TemporaryDirectory() as diretorio:
            with self.assertRaisesRegex(ErroAtualizacao, "SHA-256"):
                atualizador.baixar_atualizacao(
                    release, diretorio,
                    abrir_url=lambda *_args, **_kwargs: RespostaSimulada(
                        arquivo, "https://release-assets.githubusercontent.com/object",
                    ),
                )
            self.assertFalse((Path(diretorio) / atualizador.NOME_EXECUTAVEL).exists())
            self.assertEqual(list(Path(diretorio).iterdir()), [])

    def test_script_transporta_caminhos_como_dados_codificados(self):
        with tempfile.TemporaryDirectory() as diretorio:
            raiz = Path(diretorio)
            alvo = raiz / "Pasta com 'aspas' & espaços" / "Auto Farm.exe"
            novo = raiz / "novo.exe"
            novo.write_bytes(b"novo")
            alvo.parent.mkdir()
            alvo.write_bytes(b"atual")
            staged = atualizador.preparar_executavel(novo, alvo, sha256(b"novo").hexdigest())
            script = atualizador.criar_script_instalacao(staged, alvo, 123, raiz / "dados")
            texto = script.read_text(encoding="ascii")
            payload = re.search(r"FromBase64String\('([A-Za-z0-9+/=]+)'\)", texto)
            self.assertIsNotNone(payload)
            dados = json.loads(base64.b64decode(payload.group(1)))
            self.assertEqual(dados["destino"], str(alvo.resolve()))
            self.assertEqual(dados["novo"], str(staged.resolve()))
            self.assertEqual(dados["pid"], 123)
            self.assertEqual(dados["log"], str((raiz / "atualizacao.log").resolve()))
            self.assertEqual(dados["sha256"], sha256(b"novo").hexdigest())
            self.assertIn("[System.IO.File]::Replace", texto)
            self.assertNotIn(str(alvo), texto)

    def test_instalacao_automatica_so_lanca_helper_no_windows(self):
        with tempfile.TemporaryDirectory() as pasta:
            script = Path(pasta) / "apply.ps1"
            script.write_text("Write-Output 'teste'", encoding="ascii")
            with patch.object(atualizador.sys, "platform", "win32"), \
                    patch("atualizador.shutil.which", return_value="powershell.exe"), \
                    patch("atualizador.subprocess.Popen") as iniciar:
                atualizador.iniciar_instalacao(script)
        args, kwargs = iniciar.call_args
        self.assertEqual(args[0][0], "powershell.exe")
        self.assertEqual(args[0][-2], "-EncodedCommand")
        self.assertEqual(base64.b64decode(args[0][-1]).decode("utf-16le"), "Write-Output 'teste'")
        self.assertNotIn("-ExecutionPolicy", args[0])
        self.assertTrue(kwargs["creationflags"] & 0x08000000)


if __name__ == "__main__":
    unittest.main()
