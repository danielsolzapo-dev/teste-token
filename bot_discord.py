"""Bot de ativação para o Windows do vendedor."""

import argparse
import asyncio
import contextlib
import io
import json
import logging
import os
import re
from getpass import getpass
from pathlib import Path
from urllib.parse import urlsplit

import discord

import cofre_bot
import licenca
from pedido_discord import LIMITE_ARQUIVO, ler_pedido


BOTAO = "autofarm:ativar:v1"
BOTAO_WEBHOOK = "autofarm:webhook:v1"
SERVIDOR_WEBHOOK = 1431877632382730372
CANAL_WEBHOOK = 1574208782911672443
PAINEL = ("**Ativar Auto Farm**\nNo Auto Farm, clique em **Gerar arquivo do PC**. "
          "Depois, clique em **Ativar** aqui e envie sua chave de compra e o arquivo .json "
          "no formulário privado.")
PAINEL_WEBHOOK = ("**Configurar webhook do Auto Farm**\nClique no botão abaixo para criar um canal privado "
                  "e um webhook só para você. O bot envia a URL em uma resposta visível apenas para você; "
                  "cole-a em **Recompensas e webhook** no Auto Farm.")
log = logging.getLogger("ativacao")


async def receber_pedido(anexo):
    origem = urlsplit(anexo.url)
    if (not anexo.filename.lower().endswith(".json") or not 0 < anexo.size <= LIMITE_ARQUIVO
            or origem.scheme != "https" or origem.netloc not in {"cdn.discordapp.com", "media.discordapp.net"}
            or not origem.path.startswith(("/attachments/", "/ephemeral-attachments/"))):
        raise ValueError("Envie o pequeno arquivo .json criado pelo Auto Farm neste PC.")
    return ler_pedido(await anexo.read())


class Formulario(discord.ui.Modal):
    def __init__(self, bot, usuario_id):
        super().__init__(title="Ativar Auto Farm", timeout=900)
        self.bot = bot
        self.usuario_id = usuario_id
        self.chave = discord.ui.TextInput(placeholder="AFV1.…", max_length=256, required=True)
        self.arquivo = discord.ui.FileUpload(min_values=1, max_values=1, required=True)
        self.add_item(discord.ui.Label(text="Chave de compra", component=self.chave))
        self.add_item(discord.ui.Label(text="Arquivo deste PC", description="Anexe o .json gerado pelo Auto Farm.",
                                       component=self.arquivo))

    async def on_submit(self, interacao):
        if interacao.user.id != self.usuario_id or not self.bot.no_destino(interacao):
            await interacao.response.send_message("Abra o formulário pelo botão do canal de ativação.", ephemeral=True)
            return
        await interacao.response.defer(ephemeral=True, thinking=True)
        try:
            chave = self.chave.value.strip()
            licenca.verificar_venda(chave)
            if len(self.arquivo.values) != 1:
                raise ValueError("Anexe um único arquivo de pedido do Auto Farm.")
            pedido = await receber_pedido(self.arquivo.values[0])
            async with self.bot.vagas:
                recibo = await asyncio.to_thread(licenca.ativar_venda, chave, pedido.pc,
                                                 acesso_servico=self.bot.acesso)
        except ValueError as erro:
            await interacao.edit_original_response(content=str(erro))
            return
        except (OSError, discord.HTTPException):
            await interacao.edit_original_response(content="Não consegui concluir a ativação agora. "
                                                    "Tente de novo com a mesma chave e o mesmo arquivo.")
            return
        anexo = discord.File(io.BytesIO((recibo + "\n").encode("ascii")), filename=pedido.nome_licenca)
        with contextlib.closing(anexo):
            await interacao.edit_original_response(content="**Licença pronta para este PC.** Baixe o .txt em "
                                                   "**Downloads** com a janela de ativação aberta. "
                                                   "Se já a fechou, use **Abrir licença recebida** no Auto Farm.",
                                                   attachments=[anexo])


class Painel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Ativar", style=discord.ButtonStyle.primary, custom_id=BOTAO)
    async def ativar(self, interacao, _botao):
        if not self.bot.no_destino(interacao):
            await interacao.response.send_message("Use o botão no canal de ativação do vendedor.", ephemeral=True)
            return
        await interacao.response.send_modal(Formulario(self.bot, interacao.user.id))


class PainelWebhook(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Criar meu canal e webhook", style=discord.ButtonStyle.success,
                       custom_id=BOTAO_WEBHOOK)
    async def configurar(self, interacao, _botao):
        await self.bot.configurar_webhook(interacao)


def ids_do_link(link):
    m = re.fullmatch(r"https://(?:www\.)?discord\.com/channels/([1-9]\d{14,19})/([1-9]\d{14,19})/?", link.strip())
    if not m:
        raise ValueError("Cole o link do canal do servidor, no formato https://discord.com/channels/servidor/canal.")
    return {"servidor": int(m[1]), "canal": int(m[2])}


def ler_destino(arquivo):
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if (not isinstance(dados, dict) or not {"servidor", "canal"} <= dados.keys()
            or any(type(valor) is not int or valor <= 0 for valor in dados.values())):
        raise ValueError("Destino salvo inválido. Configure o link do canal com --canal.")
    return dados


def salvar_destino(arquivo, dados):
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados), encoding="utf-8")
    temporario.replace(arquivo)


def alvo_webhook(usuario_id):
    return f"AutoFarm/Webhook/{int(usuario_id)}"


def ler_destino_webhooks(arquivo):
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(dados, dict):
        raise ValueError("Lista de canais privados inválida.")
    resultado = {}
    for usuario, registro in dados.items():
        if (not usuario.isdigit() or not isinstance(registro, dict)
                or type(registro.get("canal")) is not int or registro["canal"] <= 0):
            raise ValueError("Lista de canais privados inválida.")
        resultado[usuario] = {"canal": registro["canal"]}
    return resultado


def salvar_destino_webhooks(arquivo, dados):
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados), encoding="utf-8")
    temporario.replace(arquivo)


def tem_painel(mensagem, bot_id):
    return mensagem.author.id == bot_id and any(
        getattr(componente, "custom_id", None) == BOTAO
        for linha in mensagem.components for componente in getattr(linha, "children", ())
    )


def tem_painel_webhook(mensagem, bot_id):
    return mensagem.author.id == bot_id and any(
        getattr(componente, "custom_id", None) == BOTAO_WEBHOOK
        for linha in mensagem.components for componente in getattr(linha, "children", ())
    )


class BotAtivacao(discord.Client):
    def __init__(self, acesso, arquivo_destino, pasta_dados=None):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.acesso = acesso
        self.arquivo_destino = arquivo_destino
        self.pasta_dados = Path(pasta_dados) if pasta_dados is not None else Path(arquivo_destino).parent
        self.destino = ler_destino(arquivo_destino)
        self.destino_webhook = {"servidor": SERVIDOR_WEBHOOK, "canal": CANAL_WEBHOOK}
        self.arquivo_painel_webhook = pasta_dados / "painel_webhook.json"
        destino_salvo = ler_destino(self.arquivo_painel_webhook)
        if (destino_salvo.get("servidor") == SERVIDOR_WEBHOOK
                and destino_salvo.get("canal") == CANAL_WEBHOOK):
            self.destino_webhook["mensagem"] = destino_salvo.get("mensagem")
        self.publicar = asyncio.Lock()
        self.publicar_webhook = asyncio.Lock()
        self.vagas = asyncio.Semaphore(4)
        self.vagas_webhook = asyncio.Lock()
        self.nova_tentativa = None

    async def setup_hook(self):
        self.add_view(Painel(self))
        self.add_view(PainelWebhook(self))

    async def configurar_webhook(self, interacao):
        if (interacao.guild_id != SERVIDOR_WEBHOOK or interacao.channel_id != CANAL_WEBHOOK
                or not isinstance(interacao.channel, discord.TextChannel)):
            await interacao.response.send_message("Use o botão no canal de configuração do servidor.", ephemeral=True)
            return
        await interacao.response.defer(ephemeral=True, thinking=True)
        async with self.vagas_webhook:
            try:
                guild = interacao.guild
                membro_bot = guild.me or await guild.fetch_member(self.user.id)
                permissoes = interacao.channel.permissions_for(membro_bot)
                if not (permissoes.view_channel and permissoes.manage_channels and permissoes.manage_webhooks):
                    await interacao.followup.send(
                        "O bot precisa de **Ver canal**, **Gerenciar canais** e **Gerenciar webhooks** "
                        "neste canal para configurar seu espaço privado.", ephemeral=True)
                    return

                registro = ler_destino_webhooks(self.pasta_dados / "canais_webhook.json")
                usuario = str(interacao.user.id)
                canal = guild.get_channel(registro.get(usuario, {}).get("canal", 0))
                url = cofre_bot.obter(alvo_webhook(interacao.user.id))
                canal_novo = not isinstance(canal, discord.TextChannel)
                if canal_novo:
                    sobrescritas = {
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        interacao.user: discord.PermissionOverwrite(
                            view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
                        membro_bot: discord.PermissionOverwrite(
                            view_channel=True, send_messages=True, read_message_history=True,
                            manage_webhooks=True),
                    }
                    nome = "autofarm-" + re.sub(r"[^a-z0-9-]+", "-", interacao.user.name.casefold()).strip("-")[:70]
                    canal = await guild.create_text_channel(
                        nome or f"autofarm-{interacao.user.id}", overwrites=sobrescritas,
                        reason=f"Canal privado de webhook solicitado por {interacao.user.id}")
                    registro[usuario] = {"canal": canal.id}
                    salvar_destino_webhooks(self.pasta_dados / "canais_webhook.json", registro)
                if not url:
                    try:
                        webhook = await canal.create_webhook(name="Auto Farm")
                        url = webhook.url
                        try:
                            await webhook.send("Webhook do Auto Farm conectado. Este canal privado está pronto.",
                                               allowed_mentions=discord.AllowedMentions.none())
                        except discord.HTTPException:
                            await webhook.delete(reason="Falha ao enviar a confirmação do webhook")
                            raise
                        cofre_bot.guardar(alvo_webhook(interacao.user.id), url)
                    except (discord.HTTPException, OSError, ValueError):
                        if canal_novo:
                            await canal.delete(reason="Falha ao preparar webhook privado")
                        raise
                registro[usuario] = {"canal": canal.id}
                salvar_destino_webhooks(self.pasta_dados / "canais_webhook.json", registro)

                await interacao.followup.send(
                    f"Canal privado pronto: {canal.mention}. Copie a URL abaixo para **Auto Farm → "
                    f"Recompensas e webhook → URL do webhook**.\n||{url}||", ephemeral=True)
            except discord.Forbidden:
                await interacao.followup.send(
                    "O Discord negou a criação. Confira as permissões do bot no servidor e tente novamente.",
                    ephemeral=True)
            except (discord.HTTPException, OSError, ValueError):
                await interacao.followup.send(
                    "Não consegui criar o canal e o webhook agora. Tente novamente em instantes.", ephemeral=True)

    def no_destino(self, interacao):
        return (bool(self.destino) and interacao.guild_id == self.destino["servidor"]
                and interacao.channel_id == self.destino["canal"])

    def localizar_canal(self):
        if self.destino:
            guild = self.get_guild(self.destino["servidor"])
            canal = guild.get_channel(self.destino["canal"]) if guild else None
            return canal if isinstance(canal, discord.TextChannel) else None
        # Ao usar apenas nomes, publique somente em um servidor do próprio dono do bot.
        candidatos = [c for g in self.guilds if g.name.casefold() == "teste" and g.owner_id == self.application.owner.id
                      for c in g.text_channels if c.name.casefold() == "sukinha"]
        return candidatos[0] if len(candidatos) == 1 else None

    async def garantir_painel(self):
        async with self.publicar:
            if self.nova_tentativa is not None:
                self.nova_tentativa.cancel()
                self.nova_tentativa = None
            canal = self.localizar_canal()
            if canal is None:
                log.info("Aguardando o canal de ativação. Para indicar um canal, use --canal com o link do Discord.")
                return
            try:
                mensagem = None
                if self.destino.get("mensagem"):
                    try:
                        existente = await canal.fetch_message(self.destino["mensagem"])
                    except discord.NotFound:
                        pass
                    else:
                        if tem_painel(existente, self.user.id):
                            mensagem = existente
                if mensagem is None:
                    async for anterior in canal.history(limit=100):
                        if tem_painel(anterior, self.user.id):
                            mensagem = anterior
                            break
                if mensagem is None:
                    mensagem = await canal.send(PAINEL, view=Painel(self))
                self.destino = {"servidor": canal.guild.id, "canal": canal.id, "mensagem": mensagem.id}
                salvar_destino(self.arquivo_destino, self.destino)
                log.info("Bot online. Painel de ativação: %s", mensagem.jump_url)
            except discord.Forbidden:
                log.error("Libere Ver canal, Enviar mensagens, Anexar arquivos e Ler histórico no canal de ativação.")
            except discord.HTTPException as erro:
                log.error("Discord indisponível ao conferir o painel (HTTP %s).", erro.status)
                self.nova_tentativa = asyncio.get_running_loop().call_later(30, self._repetir_publicacao)
            except OSError:
                log.error("Não foi possível salvar o destino no perfil do vendedor.")

    async def garantir_painel_webhook(self):
        async with self.publicar_webhook:
            guild = self.get_guild(SERVIDOR_WEBHOOK)
            canal = guild.get_channel(CANAL_WEBHOOK) if guild else None
            if not isinstance(canal, discord.TextChannel):
                log.info("Aguardando acesso ao canal de configuração de webhook informado.")
                return
            try:
                mensagem = None
                if self.destino_webhook.get("mensagem"):
                    try:
                        existente = await canal.fetch_message(self.destino_webhook["mensagem"])
                    except discord.NotFound:
                        pass
                    else:
                        if tem_painel_webhook(existente, self.user.id):
                            mensagem = existente
                if mensagem is None:
                    async for anterior in canal.history(limit=100):
                        if tem_painel_webhook(anterior, self.user.id):
                            mensagem = anterior
                            break
                if mensagem is None:
                    mensagem = await canal.send(PAINEL_WEBHOOK, view=PainelWebhook(self))
                self.destino_webhook = {"servidor": guild.id, "canal": canal.id, "mensagem": mensagem.id}
                salvar_destino(self.arquivo_painel_webhook, self.destino_webhook)
                log.info("Painel de configuração de webhook publicado: %s", mensagem.jump_url)
            except discord.Forbidden:
                log.error("No canal de webhook, libere Ver canal, Enviar mensagens e Ler histórico.")
            except discord.HTTPException as erro:
                log.error("Discord indisponível ao conferir o painel de webhook (HTTP %s).", erro.status)
            except OSError:
                log.error("Não foi possível salvar o destino do painel de webhook.")

    def _repetir_publicacao(self):
        self.nova_tentativa = None
        if not self.is_closed():
            asyncio.create_task(self.garantir_painel())

    async def on_ready(self):
        await asyncio.gather(self.garantir_painel(), self.garantir_painel_webhook())

    async def on_guild_join(self, guild):
        await asyncio.gather(self.garantir_painel(), self.garantir_painel_webhook())

    async def on_guild_channel_create(self, canal):
        if isinstance(canal, discord.TextChannel):
            await asyncio.gather(self.garantir_painel(), self.garantir_painel_webhook())

    async def on_guild_channel_update(self, antes, depois):
        if depois == self.localizar_canal():
            await self.garantir_painel()
        if depois.id == CANAL_WEBHOOK:
            await self.garantir_painel_webhook()


@contextlib.contextmanager
def instancia_unica(pasta):
    import msvcrt

    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / "bot.lock").open("a+b") as arquivo:
        arquivo.seek(0)
        try:
            msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ValueError("O bot já está aberto neste Windows.") from None
        yield  # Fechar o arquivo também libera o bloqueio.


async def verificar_build():
    painel = Painel(None)
    estrutura = Formulario(None, 1).to_dict()
    if not painel.is_persistent() or [c["component"]["type"] for c in estrutura["components"]] != [4, 19]:
        raise RuntimeError("O formulário de ativação não está disponível.")


def main():
    parser = argparse.ArgumentParser(description="Bot privado de ativação do Auto Farm")
    parser.add_argument("--verificar-build", action="store_true")
    parser.add_argument("--configurar", action="store_true", help="substituir as credenciais do vendedor")
    parser.add_argument("--canal", help="link do canal de ativação copiado do Discord")
    args = parser.parse_args()
    if args.verificar_build:
        asyncio.run(verificar_build())
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    logging.getLogger("discord").setLevel(logging.WARNING)
    pasta = Path(os.environ["LOCALAPPDATA"]) / "AutoFarmBotDiscord"
    with instancia_unica(pasta):
        destino = pasta / "painel.json"
        if args.canal:
            salvar_destino(destino, ids_do_link(args.canal))
        for alvo, titulo in ((cofre_bot.TOKEN, "Token do bot do Discord"),
                             (cofre_bot.SERVICO, "Credencial do servidor privado de ativação")):
            if args.configurar or not cofre_bot.obter(alvo):
                segredo = getpass(f"{titulo} (entrada oculta): ").strip()
                if segredo:
                    cofre_bot.guardar(alvo, segredo)
                elif not cofre_bot.obter(alvo):
                    raise ValueError(f"Informe a credencial: {titulo}.")
        bot = BotAtivacao(cofre_bot.obter(cofre_bot.SERVICO), destino, pasta)
        bot.run(cofre_bot.obter(cofre_bot.TOKEN), log_handler=None)


if __name__ == "__main__":
    try:
        main()
    except discord.LoginFailure:
        raise SystemExit("O Discord recusou o token do bot. Use --configurar para atualizá-lo.") from None
    except (ValueError, OSError) as erro:
        raise SystemExit(str(erro)) from None
