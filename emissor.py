"""Emissor do vendedor para as licenças offline do Auto Farm."""

import ctypes
from ctypes import wintypes
from datetime import date, timedelta
import os
from pathlib import Path
import sys
from tempfile import mkstemp
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

import licenca


class DadosWindows(ctypes.Structure):
    _fields_ = [("tamanho", wintypes.DWORD), ("dados", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(dados, *, proteger):
    """O DPAPI padrão vincula o segredo ao perfil do usuário no Windows."""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    chamada = crypt32.CryptProtectData if proteger else crypt32.CryptUnprotectData
    chamada.argtypes = [ctypes.POINTER(DadosWindows), ctypes.c_void_p, ctypes.POINTER(DadosWindows),
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DadosWindows)]
    chamada.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(dados)).from_buffer_copy(dados)
    entrada = DadosWindows(len(dados), buffer)
    saida = DadosWindows()
    try:
        if not chamada(ctypes.byref(entrada), None, None, None, None, 1, ctypes.byref(saida)):
            raise OSError("O Windows não conseguiu acessar a proteção deste perfil de usuário.")
        return ctypes.string_at(saida.dados, saida.tamanho)
    finally:
        ctypes.memset(buffer, 0, len(dados))
        if saida.dados:
            ctypes.memset(saida.dados, 0, saida.tamanho)
            kernel32.LocalFree(saida.dados)


def _cofre():
    pasta = os.environ.get("LOCALAPPDATA")
    if not pasta:
        raise OSError("A pasta local do perfil do Windows está indisponível.")
    return Path(pasta) / "AutoFarmEmissor" / "assinatura.dpapi"


def _gravar(destino, dados, *, substituir=False):
    # Finaliza a gravação antes de tornar o arquivo disponível para outra instância.
    descritor, temporario = mkstemp(dir=destino.parent)
    try:
        with os.fdopen(descritor, "wb") as arquivo:
            arquivo.write(dados)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        if substituir:
            os.replace(temporario, destino)
        else:
            os.rename(temporario, destino)  # Windows recusa um destino existente.
    finally:
        Path(temporario).unlink(missing_ok=True)


def _guardar_no_perfil(chave, *, substituir=False):
    arquivo = _cofre()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    protegida = _dpapi(chave.private_bytes_raw(), proteger=True)
    _gravar(arquivo, protegida, substituir=substituir)


def _abrir_cofre():
    try:
        protegida = _cofre().read_bytes()
    except FileNotFoundError:
        raise FileNotFoundError("Este perfil ainda não tem o emissor. Use Restaurar backup.") from None
    return Ed25519PrivateKey.from_private_bytes(_dpapi(protegida, proteger=False))


def _conferir_emissor(chave):
    if (not isinstance(chave, Ed25519PrivateKey)
            or chave.public_key().public_bytes_raw() != licenca.CHAVE_PUBLICA):
        raise ValueError("Essa chave de emissão pertence a outro aplicativo.")
    return chave


def provisionar():
    """Inicialização única do vendedor antes de fixar a chave pública no cliente."""
    try:
        chave = _abrir_cofre()
    except FileNotFoundError:
        if any(licenca.CHAVE_PUBLICA):
            raise ValueError("Já existe um emissor para este aplicativo. Restaure o backup dele.")
        try:
            _guardar_no_perfil(Ed25519PrivateKey.generate())
        except FileExistsError:
            pass  # Uma segunda execução pode ter terminado de criar o mesmo cofre.
        chave = _abrir_cofre()
    if any(licenca.CHAVE_PUBLICA):
        _conferir_emissor(chave)
    return chave.public_key().public_bytes_raw()


def emitir(pc, expira):
    if expira is not None and expira < date.today():
        raise ValueError("A data final da licença já passou.")
    return _assinar(licenca.carga_licenca(pc, expira))


def emitir_venda(dias):
    return _assinar(licenca.carga_venda(dias))


def _assinar(carga):
    chave = _conferir_emissor(_abrir_cofre())
    return licenca.montar_chave(carga, chave.sign(carga))


def exportar_backup(destino, senha):
    if len(senha) < 12:
        raise ValueError("Use uma senha de pelo menos 12 caracteres para proteger o backup.")
    chave = _conferir_emissor(_abrir_cofre())
    protegido = chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.BestAvailableEncryption(senha.encode("utf-8")))
    try:
        _gravar(Path(destino), protegido)
    except FileExistsError:
        raise ValueError("Já existe um arquivo com esse nome. Escolha um novo nome para o backup.") from None


def restaurar_backup(origem, senha):
    if not senha:
        raise ValueError("Informe a senha do backup.")
    with Path(origem).open("rb") as arquivo:
        protegido = arquivo.read(8193)
    if len(protegido) > 8192:
        raise ValueError("Selecione o arquivo de backup do emissor (.pem).")
    try:
        chave = serialization.load_pem_private_key(protegido, password=senha.encode("utf-8"))
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValueError("O arquivo de backup ou a senha está incorreto.") from None
    _guardar_no_perfil(_conferir_emissor(chave), substituir=True)


PRAZOS = {"30 dias": 30, "90 dias": 90, "365 dias": 365}


def duracao_em_dias(prazo, dias):
    if prazo == "Vitalícia":
        return 0
    if prazo in PRAZOS:
        quantidade = PRAZOS[prazo]
    else:
        try:
            quantidade = int(dias)
        except ValueError:
            quantidade = 0
    if not 1 <= quantidade <= 36500:
        raise ValueError("Informe um prazo de 1 a 36.500 dias.")
    return quantidade


def vencimento(prazo, dias, hoje):
    quantidade = duracao_em_dias(prazo, dias)
    # O próprio dia da emissão já permite usar o aplicativo.
    return hoje + timedelta(days=quantidade - 1) if quantidade else None


class Emissor:
    def __init__(self, raiz):
        self.raiz = raiz
        raiz.title("Auto Farm · Emissor de licenças")
        raiz.columnconfigure(0, weight=1)
        painel = ttk.Frame(raiz, padding=20)
        painel.grid(sticky="nsew")
        painel.columnconfigure(0, weight=1)
        self.offline = tk.BooleanVar(raiz, False)
        self.pc = tk.StringVar(raiz)
        self.prazo = tk.StringVar(raiz, "30 dias")
        self.dias = tk.StringVar(raiz, "30")
        self.resultado = tk.StringVar(raiz)
        self.situacao = tk.StringVar(raiz)
        ttk.Label(painel, text="Emitir licença", font=("Segoe UI", 16, "bold")).grid(sticky="w")
        self.orientacao = ttk.Label(painel, wraplength=420)
        self.orientacao.grid(sticky="w", pady=(4, 10))
        ttk.Checkbutton(painel, text="Emitir licença offline para um PC específico",
                        variable=self.offline).grid(sticky="w")
        self.dados_pc = ttk.Frame(painel)
        self.dados_pc.grid(sticky="ew", pady=(8, 0))
        self.dados_pc.columnconfigure(0, weight=1)
        ttk.Label(self.dados_pc, text="Código do PC do cliente").grid(sticky="w")
        ttk.Entry(self.dados_pc, textvariable=self.pc, width=54).grid(sticky="ew")
        opcoes = ttk.Frame(painel)
        opcoes.grid(sticky="w", pady=(12, 0))
        ttk.Label(opcoes, text="Validade").grid(row=0, column=0, sticky="w")
        ttk.Combobox(opcoes, textvariable=self.prazo, width=16, state="readonly",
                     values=(*PRAZOS, "Vitalícia", "Personalizado")).grid(row=1, column=0, sticky="w")
        ttk.Label(opcoes, text="Dias").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.campo_dias = ttk.Entry(opcoes, textvariable=self.dias, width=8, state="disabled")
        self.campo_dias.grid(row=1, column=1, padx=(10, 0))
        self.botao_gerar = ttk.Button(painel, command=self.gerar)
        self.botao_gerar.grid(sticky="w", pady=12)
        self.botao_varias = ttk.Button(painel, text="Gerar várias chaves de venda",
                                       command=self.gerar_varias)
        self.botao_varias.grid(sticky="w", pady=(0, 12))
        ttk.Label(painel, text="Licença para enviar ao cliente").grid(sticky="w", pady=(0, 4))
        ttk.Entry(painel, textvariable=self.resultado, state="readonly", width=54).grid(sticky="ew")
        self.copiar = ttk.Button(painel, text="Copiar licença", command=self.copiar_licenca, state="disabled")
        self.copiar.grid(sticky="w", pady=(6, 0))
        ttk.Label(painel, textvariable=self.situacao).grid(sticky="w", pady=(4, 0))
        ttk.Separator(painel).grid(sticky="ew", pady=12)
        botoes = ttk.Frame(painel)
        botoes.grid(sticky="w")
        ttk.Button(botoes, text="Exportar backup", command=self.exportar).grid(row=0, column=0)
        ttk.Button(botoes, text="Restaurar backup", command=self.restaurar).grid(row=0, column=1, padx=6)
        for campo in (self.pc, self.dias, self.prazo):
            campo.trace_add("write", self.limpar_resultado)
        self.offline.trace_add("write", self.alternar_modo)
        self.alternar_modo()
        self.botao_gerar.focus_set()

    def alternar_modo(self, *_):
        if self.offline.get():
            self.dados_pc.grid()
            self.orientacao.configure(text="Use o código do PC do cliente. A validade começa hoje.")
            self.botao_gerar.configure(text="Gerar licença offline")
        else:
            self.dados_pc.grid_remove()
            self.orientacao.configure(text="O primeiro PC que ativar a chave ficará vinculado a ela. "
                                           "O prazo começa nessa ativação.")
            self.botao_gerar.configure(text="Gerar chave de venda")
        self.limpar_resultado()

    def limpar_resultado(self, *_):
        self.campo_dias.configure(state="normal" if self.prazo.get() == "Personalizado" else "disabled")
        self.resultado.set("")
        self.situacao.set("")
        self.copiar.state(["disabled"])

    def gerar(self):
        self.limpar_resultado()
        try:
            prazo, dias = self.prazo.get(), self.dias.get()
            if self.offline.get():
                expira = vencimento(prazo, dias, date.today())
                chave = emitir(self.pc.get(), expira)
                situacao = f"Válida até {expira:%d/%m/%Y}, inclusive." if expira else "Licença vitalícia."
            else:
                quantidade = duracao_em_dias(prazo, dias)
                chave = emitir_venda(quantidade)
                situacao = "Chave de venda gerada. Copie para enviar ao cliente."
        except (OSError, ValueError) as erro:
            messagebox.showerror("Emitir licença", str(erro), parent=self.raiz)
            return
        self.resultado.set(chave)
        self.situacao.set(situacao)
        self.copiar.state(["!disabled"])

    def gerar_varias(self):
        if self.offline.get():
            messagebox.showinfo("Gerar várias chaves", "A geração em lote vale para chaves de venda. "
                                "Para licença offline, cada chave exige o código do PC correspondente.",
                                parent=self.raiz)
            return
        quantidade = simpledialog.askinteger("Gerar várias chaves", "Quantas chaves de venda deseja gerar?",
                                             minvalue=2, maxvalue=500, parent=self.raiz)
        if quantidade is None:
            return
        destino = filedialog.asksaveasfilename(
            parent=self.raiz, title="Salvar chaves de venda", defaultextension=".txt",
            initialfile="chaves_autofarm.txt", filetypes=[("Lista de chaves", "*.txt")])
        if not destino:
            return
        try:
            dias = duracao_em_dias(self.prazo.get(), self.dias.get())
            chaves = [emitir_venda(dias) for _ in range(quantidade)]
            conteudo = ("\n".join(chaves) + "\n").encode("ascii")
            _gravar(Path(destino), conteudo, substituir=True)
        except (OSError, ValueError) as erro:
            messagebox.showerror("Gerar várias chaves", str(erro), parent=self.raiz)
            return
        self.resultado.set("")
        self.copiar.state(["disabled"])
        self.situacao.set(f"{quantidade} chaves salvas em {destino}. Guarde o arquivo em local seguro.")

    def copiar_licenca(self):
        if self.resultado.get():
            self.raiz.clipboard_clear()
            self.raiz.clipboard_append(self.resultado.get())
            self.situacao.set("Licença copiada para a área de transferência.")

    def exportar(self):
        destino = filedialog.asksaveasfilename(parent=self.raiz, title="Guardar backup do emissor",
                                               defaultextension=".pem", initialfile="emissor-autofarm.pem",
                                               filetypes=[("Backup do emissor", "*.pem")])
        if not destino:
            return
        senha = simpledialog.askstring("Exportar backup", "Escolha uma senha de pelo menos 12 caracteres.",
                                       show="*", parent=self.raiz)
        if senha is None:
            return
        confirmar = simpledialog.askstring("Exportar backup", "Repita a senha do backup.", show="*", parent=self.raiz)
        if confirmar is None:
            return
        try:
            if confirmar != senha:
                raise ValueError("As senhas digitadas são diferentes.")
            exportar_backup(destino, senha)
        except (ValueError, OSError) as erro:
            messagebox.showerror("Exportar backup", str(erro), parent=self.raiz)
            return
        self.situacao.set("Backup salvo. Guarde o arquivo e a senha para recuperar o emissor.")

    def restaurar(self):
        origem = filedialog.askopenfilename(parent=self.raiz, title="Restaurar backup do emissor",
                                            filetypes=[("Backup do emissor", "*.pem")])
        if not origem:
            return
        senha = simpledialog.askstring("Restaurar backup", "Digite a senha do backup.", show="*", parent=self.raiz)
        if senha is None:
            return
        try:
            restaurar_backup(origem, senha)
        except (ValueError, OSError) as erro:
            messagebox.showerror("Restaurar backup", str(erro), parent=self.raiz)
            return
        self.situacao.set("Emissor restaurado neste perfil do Windows.")


def main():
    raiz = tk.Tk()
    if sys.argv[1:] == ["--verificar-build"]:
        raiz.withdraw()
        Emissor(raiz)
        Ed25519PublicKey.from_public_bytes(licenca.CHAVE_PUBLICA)
        amostra = b"Auto Farm"
        if _dpapi(_dpapi(amostra, proteger=True), proteger=False) != amostra:
            raise RuntimeError("Falha no teste da proteção do Windows.")
        raiz.destroy()
        return
    Emissor(raiz)
    raiz.mainloop()


if __name__ == "__main__":
    main()
