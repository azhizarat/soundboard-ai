# Edição, vozes, sequências e compartilhamento

Este guia descreve os recursos implementados. O estado de revisão e os limites de validação estão no [registro de execução](validation/2026-10-29-execution.md).

## Preparar um clipe

Abra o editor de um clipe capturado ou importado. A captura contínua existente permanece como entrada do fluxo.

1. Se houver transcrição alinhada, selecione uma palavra. Use Shift ao selecionar outra para ampliar o intervalo. Blocos corrigidos manualmente ou sem alinhamento por palavra são selecionados como um bloco.
2. Arraste as alças verde (início) e laranja (fim) na waveform. Clique ou arraste no áudio para mover a agulha sem apagar a seleção. Shift+arraste cria uma seleção; Alt+arraste move a seleção inteira. As setas movem a agulha em 10 ms; Shift+seta usa 100 ms. I e O marcam início e fim na agulha. A roda aproxima ou afasta o ponto sob o mouse. Dentro de campos de texto, as teclas continuam editando o texto normalmente.
3. Use **Recortar** para conservar apenas a parte selecionada da montagem atual. Use **Remover trecho** para retirar o meio e juntar o que restou. **Restaurar trecho** recupera áudio selecionado que havia sido removido.
4. Confira com **Seleção** (Espaço), **Resultado**, **Da agulha** (Shift+Espaço) ou clicando num marcador laranja de junção no pé da waveform. Essas prévias usam o monitor. **Parar** encerra a prévia própria; não interrompe uma transmissão externa ao editor.
5. Use **Salvar** para atualizar o clipe ou **Criar clipe da seleção** para publicar outra montagem da mesma gravação. A origem é preservada.

**Desfazer/Refazer** cobre os comandos de áudio e texto da sessão. Fechar o editor guarda o rascunho para retomada; isso é diferente de publicar as alterações com Salvar.

Corrigir a transcrição não corta áudio. Dê duplo clique numa palavra ou use o lápis para corrigir. Ctrl+Enter ou ✓ aplica a correção; Salvar também preserva o texto. Sem alinhamento, a transcrição aparece diretamente numa caixa editável, sem painel vazio ou texto de resultado duplicado. Para retirar som, use o comando de áudio correspondente. Se uma retranscrição produzir uma proposta, compare os textos e aceite explicitamente a substituição. Resultados antigos não devem sobrescrever uma correção humana feita durante a análise.

As abas organizam transcrição, título/aparência, vozes, efeitos e tarefas de IA, mantendo waveform e reprodução disponíveis. **Analisar com IA** oferece Transcrever áudio, Identificar vozes e Sugerir título/categoria. Resultados aplicados atualizam o editor; propostas protegidas exigem aceite.

Modelo, dispositivo, beam size, vocabulário, contexto e política de recursos ficam na engrenagem **Configurações**, valem globalmente e não são propriedades do clipe.

## Importar arquivos, pastas e vídeo

Em **Preparar**, use **Adicionar arquivos** ou **Adicionar pasta** na fila de importação, ou arraste arquivos/pastas para a janela. Escolha **Áudios / falas** ou **Vinhetas** para os botões da fila. Cada item informa seu estado e histórico; um arquivo inválido não impede os demais. A importação de áudio de vídeo fica disponível quando FFmpeg e ffprobe estão presentes.

Mídia com mais de 120 segundos espera que você informe início e fim de uma janela de até 120 segundos. A análise começa com essa janela, evitando processar a gravação inteira. Arquivos com conteúdo idêntico e a mesma janela são reconhecidos como duplicatas, mesmo com nomes diferentes.

**Analisar automaticamente após importar** vem marcado e respeita pausa e perfil de recursos. O áudio fica disponível antes de a IA terminar. **Abrir no editor** permite revisar os resultados, aceitar propostas e salvar a edição. Desmarque a opção para importar sem análise; **Analisar novamente** permite solicitá-la depois. A análise nunca confirma uma referência vocal por conta própria.

**Cancelar** impede a publicação de uma importação ainda em andamento. Cancelar a análise de um áudio já importado mantém o clipe. Use **Tentar novamente** depois de reparar um arquivo ou uma falha. Ao reabrir o aplicativo, trabalhos incompletos aparecem interrompidos e não reiniciam inferência sozinhos.

Selecione itens para aplicar categoria, título e tags em lote. Campos modificados depois da importação, inclusive em uma edição humana aberta, são preservados e aparecem na contagem de conflitos. O histórico visível é limitado aos 512 itens mais recentes; isso não remove os clipes já publicados.

## Corrigir a pessoa e aprender sua voz

Na aba **Vozes** do editor, escolha a pessoa e habilite **Aprender com minhas correções** quando quiser aproveitar os exemplos confirmados. Selecione a fala pertinente antes de salvar. **Cadastrar voz** permite solicitar o cadastro explicitamente.

A atribuição da pessoa e o aprendizado têm resultados separados. Se o modelo falhar ou o trecho não for adequado, a pessoa corrigida continua salva. O exemplo pode ficar pendente e não participar do reconhecimento.

Se o app fechar durante o aprendizado, a próxima abertura informa que ele foi interrompido. A pessoa permanece salva; solicite um novo cadastro quando quiser tentar novamente. A inferência não é retomada automaticamente.

Quando a gravação contém várias vozes, a seleção precisa ter informação temporal suficiente para associar o intervalo a uma só voz. Escolher a gravação inteira ou repetir o cadastro não remove essa proteção. Em material antigo sem essas marcações, confira a análise de pessoas e a seleção antes de tentar novamente.

Repetir o mesmo exemplo não aumenta seu peso. Desfazer uma correção remove a influência criada por aquela ação, respeitando exemplos que já existiam antes dela. Previsões automáticas não são cadastradas como exemplos confirmados.

## Revisar um perfil

Use o botão de lápis da pessoa na barra lateral para abrir **Perfil de voz**. A tela permite ouvir cada exemplo no monitor, reatribuir a pessoa e remover o exemplo. Remover uma referência retira sua influência do perfil. As referências podem continuar preservadas quando o clipe que as originou é removido.

Em **Critérios de correspondência e revisão**, limiar e margem controlam a decisão entre pessoa identificada, caso duvidoso e voz desconhecida. O escore de similaridade não é uma porcentagem de certeza. Comece pelos valores iniciais e avalie tanto os erros quanto os casos que deixam de receber identificação automática.

**Gerar propostas para o acervo** apresenta sugestões antes de alterar clipes. Marque as propostas desejadas e aplique-as explicitamente. Atribuições manuais são preservadas; alguns clipes podem precisar de análise de pessoas antes de haver uma proposta.

Referências legadas sem versão conhecida do extrator ficam preservadas com backup, mas não participam do reconhecimento. Cadastre novos exemplos confirmados para reconstruir esses perfis. Arquivos legados fora da biblioteca só são importados pela ação explícita da tela.

Os testes desta entrega usam dados sintéticos. Eles verificam os fluxos e a reversibilidade; a melhora real do reconhecimento precisa ser medida com gravações corrigidas e separadas para avaliação.

## Controlar a IA local

Na engrenagem **Configurações**, abra **Qualidade local e recursos**. **Revisando** permite processar a fila normalmente. **Jogando** adia trabalhos em segundo plano e mantém os pedidos explícitos disponíveis. A inferência continua serializada; esses perfis não prometem uma taxa de quadros específica no jogo.

**Pausar novas análises** impede o início dos trabalhos aguardando. Uma inferência já ativa termina sua execução; cancelá-la impede a aplicação do resultado, sem fingir que o modelo foi interrompido imediatamente. Os trabalhos da fila podem ser cancelados ou priorizados individualmente no painel. Priorizar um trabalho não ignora a pausa.

A captura de replay continua funcionando com a IA pausada. O áudio fica disponível como clipe ou rascunho, com aviso de análise adiada; edite manualmente ou retome a IA e solicite a etapa desejada. A captura não recebe transcrição ou pessoa inventadas.

Informe um termo por linha no **Dicionário pessoal confirmado**, marque a confirmação e salve. Os termos orientam o reconhecimento como contexto; não substituem palavras automaticamente em transcrições existentes. Perfil, pausa e dicionário são preservados ao reabrir o app. Descritores acústicos descrevem o sinal; categorias e títulos continuam sugestões editoriais.

A ferramenta `tools/evaluate_audio_pipeline.py` compara configurações com cadastro, calibração e teste separados. O relatório mostra erro textual, erro temporal, confusão de pessoas, precisão e cobertura, além da origem das medições de tempo. Dados sintéticos validam a ferramenta; para escolher modelo ou limiar com base em qualidade real, é necessário um corpus independente corrigido.

## Montar um diálogo com vários sons

Abra **Sequências**, dê um nome e adicione os blocos na ordem desejada. Clique em um clipe da biblioteca para acrescentá-lo ao fim ou arraste para um ramo. O seletor de clipe também alimenta os botões **+ Clipe** de cada posição.

- **Tocar:** uma ocorrência de clipe com início, fim e volume próprios. Esses controles não alteram o clipe da biblioteca.
- **Esperar:** uma pausa em milissegundos.
- **Paralelo:** ramos iniciados juntos; o fluxo só continua quando todos terminam, incluindo suas pausas finais. Os ramos aceitam clipes e esperas.
- **Aguardar acionamento:** pausa até **Avançar** ou um novo acionamento do atalho. Manter a tecla pressionada não deve pular etapas.

Por exemplo: pergunta → espera de 300 ms → resposta e risada em dois ramos paralelos → aguardar acionamento → conclusão. A duração mostra o tempo calculável e a quantidade de acionamentos manuais; a espera pelo operador não recebe uma duração fictícia.

Selecione um bloco para editar suas propriedades no inspetor. ↑/↓ navegam, Alt+↑/↓ reordenam, Ctrl+D duplica, Delete remove, Ctrl+Z/Y desfaz/refaz e Ctrl+S salva, inclusive um campo ainda em edição. Os demais atalhos não interferem na digitação em campos. Use **Duplicar** para repetir um trecho com ajustes independentes e **Desfazer/Refazer** para recuperar alterações da sessão. **Salvar sequência** persiste a montagem. Cada ocorrência fixa a revisão do clipe usada ao adicioná-la. Se o clipe mudar, **Atualizar referência** permite adotar explicitamente a nova versão; o corte é reiniciado para essa versão e o volume local é mantido.

**Atualizar biblioteca** atualiza as opções sem apagar seu rascunho. **Recarregar documento salvo** recupera a definição persistida; se houver alterações locais, use **Descartar alterações e recarregar** somente quando quiser abandoná-las. Salvar ou iniciar continua vinculado à sequência escolhida no clique, mesmo se você abrir outra enquanto a resposta estiver em andamento.

**Ouvir no monitor** testa um bloco, grupo ou sequência pelas saídas de monitor. **Iniciar** usa as saídas configuradas. O app permite uma execução de sequência por vez; **Reiniciar/substituir** é a ação explícita para trocar a execução atual. O rodapé mostra estado, próximo bloco e andamento dos ramos. **Parar sequência** cancela esse fluxo; **Parar tudo** também cancela suas esperas e qualquer reprodução futura. Uma sequência interrompida não retoma áudio automaticamente ao reabrir o aplicativo.

Sequências salvas aparecem como cartões e podem receber atalho global. Para excluir um clipe ainda usado por uma sequência, remova primeiro as referências da montagem ou exclua a sequência.

## Organizar e tocar coleções

Em **Preparar**, abra o editor de coleções, crie uma coleção e adicione referências a clipes ou sequências. Escolha o rótulo, a posição e o número de colunas. As posições permanecem fixas quando os títulos dos clipes mudam; espaços vazios são preservados.

Cada pad tem grupo **Fala**, **Trilha** ou **Efeito**. Uma fala nova substitui a fala anterior, preservando trilhas e efeitos. Configure o que um novo acionamento faz: reiniciar, parar ou ignorar enquanto estiver tocando. **Segurar para tocar** encerra o som ao soltar; **Loop** repete até a parada. Para sequências aguardando acionamento, acionar novamente avança a mesma execução.

Salve e ative a coleção, depois mude para **Tocar**. Atalhos da coleção ativa são verificados contra os comandos globais; coleções inativas podem reutilizar as mesmas teclas. Um clipe ou sequência ainda referenciado precisa ser removido da coleção antes de ser excluído.

**Ensaio** envia os disparos ao monitor, incluindo atalhos e sequências. Escolha um monitor diferente da saída do cabo. Mudar o modo interrompe as execuções existentes; elas não são retomadas automaticamente. **Parar tudo** cancela também os sons que ainda estão em preparação. A parada de uma execução pode ser imediata ou usar a saída suave configurada.

Se uma saída falhar, a operação bloqueia novos disparos e informa o problema. Reconectar o dispositivo não retoma áudio: selecione explicitamente a saída e faça um novo acionamento. Os testes automatizados verificam essa lógica com mixers e dispositivos simulados; confira o roteamento físico no seu ambiente antes de uma apresentação.

## Sortear um grupo de sons

No editor da coleção, escolha o primeiro clipe ou sequência e use **Grupo aleatório**. Marque os demais membros e configure o cooldown em milissegundos. O grupo percorre as escolhas em ordem embaralhada e evita repetir imediatamente quando existem alternativas. **Repetir última escolha** dispara explicitamente o alvo indicado abaixo do pad, respeitando o cooldown.

As escolhas continuam usando o grupo de fala/trilha/efeito e as regras de reprodução do pad. Atualizar a biblioteca traz novos clipes e variantes às opções sem descartar a edição da coleção.

## Criar variantes de um clipe

Salve o clipe e abra **Variantes de velocidade, tom e reverso** no editor. Ajuste velocidade de 0,5 a 2x, tom de -12 a +12 semitons e, se desejar, reverso. Velocidade e tom são independentes. **Prévia no monitor** permite conferir; **Parar prévia** também cancela uma preparação ainda em andamento.

**Salvar como variante** cria outro clipe, mantendo a gravação e a revisão escolhida. A operação usa a revisão publicada ao abrir o editor: salve e reabra para incluir cortes novos. É possível processar até 120 segundos da montagem; velocidade e tom exigem os filtros FFmpeg indicados pelo aplicativo. Para outra combinação, volte ao original em vez de encadear variantes.

A waveform do editor mostra a gravação original para selecionar os trechos; a waveform da biblioteca mostra a saída processada. Duração, loudness e tempos da saída são recalculados. O cadastro vocal continua usando a fala original confirmada, sem os efeitos. Alterar depois o clipe original não muda a variante nem uma revisão já fixada numa sequência.

## Compartilhar packs e guardar montagens editáveis

No menu **Pack**, escolha o modo e exporte a biblioteca, os clipes filtrados, uma coleção ou uma sequência. Coleções e sequências levam suas dependências, incluindo revisões fixadas, regras, posições e ajustes locais.

- **Compacto** leva o áudio reproduzível selecionado. Trechos removidos e transcrição fora dos intervalos exportados ficam de fora. Quem recebe não consegue recuperar os cortes anteriores por esse pack.
- **Backup editável** leva as gravações de origem e o histórico de revisões necessário para continuar editando. Pode conter partes retiradas da montagem atual; confira esse modo antes de compartilhar.

Nenhum desses modos leva o banco de exemplos vocais local. Pessoas recebidas são rótulos, não novas referências confirmadas. O resumo informa o conteúdo e os avisos. Uma sequência com acionamento manual permanece uma sequência, não um arquivo de áudio único.

**Importar pack** também lê o formato antigo. Packs novos são validados integralmente antes de publicar o grafo. IDs recebem novos valores, configurações de microfone/cabo/monitor permanecem como estavam e atalhos conflitantes são retirados dos itens importados com aviso. Importar novamente o mesmo pack não duplica seus nós enquanto a cópia importada continua completa; alterações locais não são revertidas.
