# Token · Auto Farm para Windows

Aplicativo desktop independente para Windows x64. Usa capturas da tela, imagens de referência locais e OCR para reconhecer estados visíveis do Fortnite; não lê memória do jogo, injeta DLLs nem tenta ocultar entradas do anti-cheat.

[Baixar AutoFarmTokenPirata.exe para Windows](https://github.com/danielsolzapo-dev/Token/releases/latest/download/AutoFarmTokenPirata.exe)

## Uso

1. Na tela **Ativar Auto Farm**, use **Gerar arquivo do PC** para ativar pelo Discord ou cole a chave para ativação direta. A chave de venda fica vinculada ao Windows do primeiro PC ativado. As próximas aberturas usam o comprovante salvo.
2. Deixe o Fortnite em primeiro plano, em modo janela ou tela cheia em janela, e clique em **Iniciar**. **F6** inicia ou pausa.
3. Use **Testar imagem** para inspecionar um PNG, JPG ou BMP. O diagnóstico marca a caixa e o centro detectados e exibe método e score; essa função nunca envia teclado ou mouse.

O seletor **Aparência** oferece **Padrão (Azul)**, **Escuro (Preto)**, **Oceano (Azul-marinho)**, **Claro (Rosa bebê)**, **Floresta (Verde escuro)**, **Pôr do sol (Roxo)** e **Abóbora (Halloween)**. A troca é imediata durante a sessão e a escolha é restaurada na próxima abertura. A primeira abertura usa Padrão (Azul). Use a barra de rolagem para acessar o rodapé em janelas baixas.

O app procura **Jogar Novamente**, **Continuar** e os botões do lobby. A detecção usa imagens de referência em múltiplas escalas; a alternativa amarelo+OCR para **Jogar Novamente** só aceita o rótulo dentro do controle. A leitura em português ou inglês identifica fila e diálogo de saída, e não confirma quando há mais de um diálogo plausível.

Na tela de seleção de modo, o botão amarelo **Jogar** à direita só é aceito se o OCR ler **Esquadrão** e **Preencher**. **Testar imagem** também reconhece a opção **Região de Pareamento** na aba Jogo quando o rótulo aparece cortado. A região selecionada é definida no Fortnite.

O contador avança uma vez por fila reconhecida. Depois que o app tenta entrar na fila, ele aguarda uma transição de HUD antes de iniciar o cronômetro e liberar o movimento. Se você abrir o painel durante uma partida em andamento, o HUD estável libera o anti-AFK com o cronômetro de saída desarmado. Um botão de lobby reaparecendo não reinicia a fila sozinho: depois do timeout, recomece com **Iniciar/F6** ou aguarde o reconhecimento explícito de cancelamento.

**Pronto** exige confirmação do texto no botão amarelo. Em uma partida reconhecida, o app bloqueia os cliques normais de lobby. Se pausar e retomar com **F6**, o app faz uma nova leitura da tela e descarta uma tentativa de fila ainda sem confirmação.

O anti-AFK fica bloqueado no ônibus, no inventário, em modo espectador, com HUD indisponível ou durante pausa ou perda de foco. A janela e suas coordenadas são validadas antes de cada ação e entre segmentos de entrada. O Windows recebe entradas globais; essas verificações reduzem o risco de enviar comandos fora do jogo, mas não eliminam a corrida entre a última validação e o envio.

A rotina aérea opcional salta com **Espaço** após duas leituras do aviso do ônibus. O reconhecimento usa a imagem de referência ou o texto do aviso. Funciona também ao iniciar o app já no ônibus. Duas leituras de **Entrar em queda livre** enviam **Espaço** para fechar a asa-delta uma vez por descida.

O anti-AFK mantém **W** nas caminhadas e alterna diagonais com **A** e **D**. Com o cursor capturado e fora da transição aérea reconhecida, gira a câmera e envia dois cliques esquerdos com o item que já está na mão.

O tempo padrão é 7,5 minutos. A opção **Voltar ao lobby se só eu restar no esquadrão ou na partida** vem ligada e funciona mesmo com a saída por tempo desligada. Com o seu personagem vivo, o app procura os indicadores de eliminação de todos os companheiros reconhecidos ou o número **1** ao lado do ícone de jogadores restantes. Três leituras consecutivas, separadas por pelo menos um segundo, iniciam a volta ao lobby. Se os nomes ou as barras apenas sumirem, o app continua na partida e aguarda uma leitura conclusiva.

**Próxima saída** mostra a contagem regressiva do prazo usado pela saída automática. O painel mostra um traço quando a automação está pausada ou não há saída programada. **Farmando** soma o tempo ativo desde que o app foi aberto, incluindo lobby e fila, e congela durante as pausas.

**Atualizar automaticamente** consulta as releases do GitHub ao abrir e a cada hora. Quando o app fica pausado, o atualizador valida o tamanho e o SHA-256 do executável, guarda a versão anterior como `.previous` e reinicia o app. Use **Verificar** para consultar manualmente.

Em **Recompensas e webhook**, você pode ativar o resgate quando a tela de itens adquiridos aparecer antes de uma nova fila. Cole a URL HTTPS do Discord ou de um endpoint compatível com seus anexos e clique em **Salvar**. Depois que o botão de resgate some, o app envia a captura do Fortnite feita antes do clique e o nome lido na tela. Se o nome estiver ilegível, o aviso informa isso e envia a captura. **Testar com tela atual** aguarda três segundos e envia a imagem do Fortnite em primeiro plano ou do monitor principal, com o nome de teste escolhido. [Formato do webhook do Discord](https://docs.discord.com/developers/resources/webhook#execute-webhook)

Marque **Notificar tesouros elementais encontrados** para receber avisos de descoberta no mesmo webhook. O alerta inclui uma captura do Fortnite e identifica o Acelerador de Extração, o Extrator Portátil, os Fogos de Artifício Dourados, o Localizador de Elementais ou o Localizador da Sorte quando o ícone está legível.

Quando a Loja Pirata é desbloqueada no lobby, o webhook recebe uma captura mesmo com o resgate automático desligado. Em **Recompensas e webhook**, ative **Pausar ao obter o token da Loja Pirata** para parar assim que o aviso aparecer. A opção começa desligada. A pausa mantém a recompensa na tela para conferência. Use F6 para retomar e seguir a preferência de resgate.

Se aparecer **Esta conta foi banida**, o webhook recebe a captura do aviso. Durante a restrição, a sessão aguarda o Fortnite liberar **Jogar** ou **Pronto** para retomar a fila. Nas pesquisas **Queremos ouvir você!**, o app usa **Pular** quando identifica o botão na tela.

Em **Regiões por horário**, ative a programação para Construção e Zero Build. O app usa Brasília (UTC−3) e tenta ajustar a região antes de entrar na fila pelo lobby. Das 00h às 06h, usa América do Norte. Das 06h às 11h, você pode escolher Ásia ou Oceania. Das 11h às 18h, usa Europa. Das 18h à meia-noite, você pode escolher Brasil ou América do Norte. A preferência de sub-região fica a cargo da configuração do Fortnite. [Configurações de pareamento da Epic](https://www.epicgames.com/help/c-ategory_fortnite/c-fortnite_gameplay/a10221394)

## Baús do replay

Abra **Baús do replay** no rodapé. O painel lê a última gravação concluída de `%LOCALAPPDATA%\FortniteGame\Saved\Demos` e acompanha as próximas enquanto estiver aberto. Use **Escolher arquivo** para analisar outra partida. A leitura é local e funciona com a automação pausada.

**Você** mostra as aberturas atribuídas ao jogador que gravou o replay. **Esquadrão (inclui você)** soma as aberturas desse jogador e dos colegas identificados na mesma equipe. O leitor verifica a interação de abertura e quem a realizou. Ele exclui adversários, caixas de munição e baús apenas destruídos. O painel informa quando faltou autoria ou cobertura. Uma gravação local pode omitir ações de colegas distantes.

O leitor de replay é compilado a partir de uma revisão fixa do FortniteChestTracker. A release inclui o arquivo `LeitorBaus-fonte.zip` com as fontes correspondentes e as licenças. Para usar o contador ao executar pelo código-fonte, instale o SDK .NET indicado em `leitor_replay/LEIA-ME.txt` e execute `python leitor_replay/compilar.py`. O executável de Windows já inclui o leitor. [Projeto do leitor de replay](https://github.com/Loukious/FortniteChestTracker)

## Enviar um diagnóstico

1. Reproduza a falha e pressione **F7**. Isso marca o horário no registro da sessão.
2. Clique em **Copiar diagnóstico** no painel e cole o texto ao relatar a falha.

O registro fica em `%LOCALAPPDATA%\AutoFarmToken\diagnostico.log`, com até duas cópias rotativas. Ele registra estados do jogo, decisões e os comandos que o app tentou enviar. O resultado `enviada` indica que a rotina de entrada do Windows terminou. Em caso de falha na troca de região, a última captura do menu lateral ou das configurações fica no arquivo local `debug\regiao_ultima_falha.jpg`, na mesma pasta do diagnóstico.

Os ajustes ficam em `%LOCALAPPDATA%\AutoFarmToken\ajustes.json`. A detecção depende da resolução, escala e idioma da interface do jogo e pode precisar de ajustes após atualizações do Fortnite. Confirme se automação de entradas é permitida pelas regras do jogo; não use o aplicativo se isso violar as regras ou políticas aplicáveis.

## Rota de baús da ponta leste

Em **Automação**, marque **Pousar na ponta leste e seguir a rota dos baús**. Use **Ver rota** para conferir o ponto de pouso e o trajeto. A opção começa desligada. Ao ativá-la, ela controla o salto, a asa-delta e a caminhada daquela sessão.

O app compara o minimapa visível com a ilha de referência e lê a direção na bússola. Ele espera a aproximação do ônibus, salta com Espaço e corrige o voo em direção à ponta de areia a leste de Lil’ Uzi’s Stronghold. Depois do pouso, segue a praia norte, contorna a enseada por terra e visita a construção da praia sul. Os três pontos numerados são locais de procura. A disponibilidade dos baús varia por partida.

No percurso, o app aguarda o comando de vasculhar na mira, lê a tecla de interação e a mantém pressionada. Usa **E** quando o comando está legível e a letra não aparece separadamente. A contagem de aberturas confirmadas continua em **Baús do replay**. **F6** pausa a rota. A saída por tempo e a saída por último sobrevivente continuam com prioridade.

Com a rota habilitada, **Testar imagem** mostra a posição estimada e o rumo de uma captura com o minimapa do jogo. A navegação aguarda uma leitura confiável se o mapa estiver encoberto, se a câmera não responder ou se a ilha mudar.

## Emissão de licenças

1. No Windows do vendedor, abra o [Emissor de Licenças](https://github.com/danielsolzapo-dev/Token/releases/latest/download/EmissorLicencas.exe). Na primeira utilização, use **Exportar backup**, escolha uma senha de pelo menos 12 caracteres e guarde o arquivo `.pem` em local separado do PC. Guarde também a senha em lugar seguro.
2. Escolha a validade e clique em **Gerar chave de venda**. Cada clique gera uma chave nova. Copie e entregue a chave ao comprador por mensagem privada. Até o primeiro uso, quem tiver a chave pode ativá-la.
3. O comprador ativa a chave no PC em que vai usar o app. O servidor registra uma única máquina por chave, inclusive quando dois PCs tentam ativá-la ao mesmo tempo. O prazo começa no dia da primeira ativação e inclui o último dia, pelo horário de Brasília. Repetir a ativação no mesmo Windows recupera o comprovante com o vencimento original.

Com o servidor privado, o comprador pode ativar pelo Discord. Também existe emissão offline por código do PC, com prazo contado a partir da emissão. A reinstalação do Windows pode alterar o identificador e exigir uma nova licença.

O Windows protege a chave de assinatura do vendedor no perfil que a criou. Para emitir licenças em outro Windows, abra o emissor, use **Restaurar backup** e informe a senha do arquivo exportado. Mantenha o backup e a senha sob seu controle. Quem possuir os dois poderá emitir licenças para este app.

### Ativação pelo Discord

1. No computador em que vai usar o Auto Farm, abra o aplicativo e clique em **Gerar arquivo do PC**. Salve o arquivo `.json`.
2. No canal de ativação do vendedor, clique em **Ativar**. Cole a chave de compra e anexe o arquivo ao formulário privado.
3. Baixe o `.txt` da resposta privada em **Downloads**. Com a janela de ativação aberta, o Auto Farm reconhece o arquivo e abre o painel. Se fechou o aplicativo ou salvou em outra pasta, abra-o e selecione **Abrir licença recebida**.

O botão consulta o mesmo registro da ativação direta. Repetir o formulário no mesmo PC recupera a licença. Depois de baixar e instalar a licença, o comprador pode usar o aplicativo offline.

### Bot no Windows do vendedor

Abra o [Bot de Ativação para Discord](https://github.com/danielsolzapo-dev/Token/releases/latest/download/BotAtivacaoDiscord.exe) no Windows que vai atender os pedidos. Na primeira abertura, informe o token do bot e a credencial do serviço de ativação privado. Os dois ficam no Gerenciador de Credenciais do seu usuário do Windows. Para substituí-los, execute o bot com `--configurar`. O bot precisa estar aberto e conectado à internet para atender uma nova ativação.

Instale o bot no servidor como aplicativo de servidor com as permissões **Ver canal**, **Enviar mensagens**, **Anexar arquivos** e **Ler histórico de mensagens**. O bot publica o botão em **#sukinha**, no servidor **Teste**, quando o dono do bot também é dono do servidor e o destino é único. Para indicar o destino explicitamente, copie o link do canal no Discord e abra `BotAtivacaoDiscord.exe --canal "LINK_DO_CANAL"`. O bot guarda os IDs do canal e do painel para reutilizar o mesmo botão após reiniciar. [Portal de Desenvolvedores do Discord](https://discord.com/developers/applications)

## Desenvolvimento

Use Python 3.14 e o SDK .NET 10.0.404 no Windows. Prepare os modelos OCR e compile o leitor de replays.

```powershell
py -3.14 -m pip install -r requirements.txt
py -3.14 preparar_ocr.py
py -3.14 leitor_replay/compilar.py
```

Inicie, teste e compile:

```powershell
py -3.14 app.py
py -3.14 -m unittest discover -s tests -v
py -3.14 -m py_compile app.py atualizador.py diagnostico.py embarque.py logica_autofarm.py painel_tema.py regiao.py relogio_painel.py tesouros.py texto_tela.py webhook.py windows_game.py
py -3.14 app.py --verificar-build
py -3.14 -m PyInstaller --noconfirm --onefile --windowed --name AutoFarmTokenPirata --collect-all rapidocr --copy-metadata rapidocr --add-data "modelos;modelos" --add-data "templates;templates" --add-data "fontes;fontes" --add-data "leitor_replay/binario;leitor_replay/binario" app.py
py -3.14 -m PyInstaller --noconfirm --onefile --windowed --name EmissorLicencas emissor.py
```

A interface usa Inter 4.1. As fontes incluídas contêm o conjunto de caracteres do painel e a versão em negrito usa algarismos tabulares. A licença SIL OFL está em `fontes/LICENSE.txt`. [Projeto Inter](https://github.com/rsms/inter)

O executável é gerado em `dist/`. O fluxo do GitHub Actions inclui os modelos OCR e as imagens de referência e executa verificações positivas e negativas no executável empacotado. Os testes usam detector, relógio e janela simulados e imagens sintéticas; não exigem Fortnite aberto, rede ou esperas reais. A confirmação final de foco, geometria e reconhecimento visual ainda precisa ser feita manualmente com Fortnite aberto.
