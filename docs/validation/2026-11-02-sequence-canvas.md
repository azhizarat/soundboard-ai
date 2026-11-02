# Sequências como fluxos visuais

## Direção aplicada

O usuário rejeitou a apresentação de formulário/lista e pediu uma aplicação visual de workflows, com referências como n8n e Power Automate. O editor usa agora um canvas navegável, nós conectados por setas, ramificações simultâneas e realce do que está tocando. A biblioteca fica à esquerda, com busca por título/frase/pessoa/categoria e prévia no monitor; os ajustes pertencem somente ao nó selecionado.

O canvas projeta o documento de sequência existente. Não migra sequências nem altera os clipes originais. Conectores + representam posições explícitas antes de um bloco ou no fim de um ramo. Arrastar insere ou move uma ocorrência para esse conector. Clicar no conector e depois na biblioteca oferece a mesma operação sem arraste. Ramos continuam aceitando áudios e pausas; Juntos e Aguardar pertencem ao fluxo principal. O layout é automático, com pan e zoom locais, separado da ordem de execução.

## Causa do engasgo e correção

`_bind_native_drop` registrava um listener Python de `dragover` no documento. O pywebview serializava o evento e o DOM em cada passagem do ponteiro, embora o callback não fizesse trabalho. No mesmo WebView2 isolado, disparar um dragover no cabeçalho mediu **207,3 ms antes** e **0,20 ms depois** da correção. Isso mede o processamento desse evento, não a latência total do aplicativo.

Dragover agora é tratado em JavaScript. Somente drops de arquivos chegam ao handler nativo que conhece os caminhos completos; drops internos são filtrados. A movimentação do canvas usa Pointer Events e só captura o ponteiro após superar o limiar de arraste. Clique simples, cancelamento por Esc, fechamento e troca de fluxo têm tratamento próprio.

## Validação

- Python completo com hardware de áudio bloqueado: **770 passaram**, `.worktrees/startup-probe/run_pytest.py -q --basetemp=.worktrees/pytest-flow-final`.
- JavaScript: **131 passaram**, `node --test tests/*.test.cjs`.
- Regressões novas: metadados de busca e duração legada sem importação/decodificação; dragover sem serialização Python; arquivos ainda encaminhados; layout e conectores de ramo; inserção no meio; undo; busca sem acentos; prévia atrasada sem substituir a intenção mais recente; fechamento durante preparação.
- TDD: reproduzidos antes da correção o arraste serializado 100 vezes, a inserção que ignorava a posição e a corrida que tocava A → C → B quando C era a última escolha.
- WebView2 com entrada de mouse real em **1280 e 768 px**: buscar, ouvir/parar prévia sem adicionar, inserir no meio por arraste, mover para ramo paralelo, desfazer, salvar, pan/zoom, executar no monitor com realce, parar, adicionar por clique e cancelar gesto por Esc sem drop tardio. Sem erros JavaScript.
- O teste nativo também revelou e reproduziu um clique simples engolido pela captura antecipada de ponteiro. Corrigido capturando apenas após o limiar, mantendo observação temporária do gesto no documento.
- Revisão Astra: corrigidos captura antecipada, ticket de prévia após await, documento escolhido após await e gesto restante ao fechar/trocar. Releitura sem novos P1/P2.
- `git diff --check` limpo.

Harnesses locais: `.worktrees/sequence-drag-diagnosis.py` e `.worktrees/sequence-workflow-smoke.py`. Evidências da rodada funcional: `.worktrees/e10-parent-h69my2m1` (1280 px), `.worktrees/e10-parent-xe6onffm` (768 px). API, persistência e WebView são reais; biblioteca e áudio são sintéticos, sem tocar dispositivos físicos nem reiniciar o aplicativo do usuário.

## Regras confirmadas para este domínio

- Sequências devem comunicar ordem, ramificação e execução visualmente; formulários ficam restritos às propriedades selecionadas.
- Adicionar precisa oferecer busca, contexto do áudio e audição prévia.
- Gestos frequentes de UI ficam no navegador, sem serializar o DOM para o backend.
- Clique e arraste são intenções diferentes; captura imediata pode quebrar os cliques em botões filhos.
- Identidade do documento e intenção da prévia são capturadas antes de qualquer await.
- Cortes e volume de uma ocorrência não alteram o áudio original; rascunhos, undo e revisões fixadas continuam preservados.
