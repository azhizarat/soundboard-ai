# E4 — editor integrado de texto, origem e montagem

Data: 29/10/2026. Implementação sobre o baseline E3 aprovado em `.worktrees/e3-approved`. Sem staging, commits, reset, modelos reais, biblioteca pessoal ou reprodução física. Revisões Sol/Astra e smoke WebView2 são coordenados pelo agente principal após este congelamento.

## Implementação

- `editor_service.py`: comandos transacionais `keep`, `remove`, `restore`, `create`, `correct`, `legacy_text`, `undo` e `redo`, sempre em samples da origem a 48 kHz. Manter intersecta a montagem atual; restaurar é união explícita. Montagem vazia é rejeitada. Nova montagem publica outra referência à mesma fonte, numa gravação com rollback, sem alterar o draft pai. Proveniência humana é preservada.
- Histórico de até 100 comandos na sessão, separado do draft durável. Snapshots contêm montagem e transcrição; não revertem metadados independentes. Geração do draft avança também em undo/redo. Mudança textual externa ou troca de draft invalida o histórico incompatível; a recuperação após reinício restaura o estado final, sem prometer recuperar a pilha de comandos.
- `ui/editor.js` e `ui/transcript.js`: editor ligado às APIs reais, seleção bidirecional, blocos manuais indivisíveis, blocos sem palavras com limites reais de segmentos, múltiplas âncoras sem repetir o texto, origem atenuada e resultado separado. Clique seleciona/posiciona, sem reproduzir. Shift+clique amplia. Arraste seleciona/move bordas, Ctrl+clique posiciona cursor, setas ajustam 10 ms e Shift+setas 100 ms na waveform. Campos de texto mantêm edição nativa. Zoom, ajustar largura e deslocamento horizontal disponíveis.
- Correções textuais usam campo próprio; excluir texto não corta áudio. O textarea legado permanece como espelho oculto para os fluxos existentes, e seu baseline é atualizado junto ao resultado do editor, evitando que autosave de título transforme correção ancorada em texto legado. Seleção parcial de um bloco corrigido ainda corrige a unidade inteira. Correções sem palavras usam o ID real do segmento, sem inventar palavras/tempos.
- `ui/editor.css`/`index.html`: editor até 1180 px, duas colunas e uma coluna abaixo de 800 px; transporte e ações próximos, metadados em seção expansível, volume no editor. Regra autoral de `hidden` impede que estilos `.btn` tornem ações ocultas visíveis.
- Prévia de seleção/resultado/junção é chamada Python, monitor only. `audio_engine.render_clip` recorta a saída do renderer compartilhado, mantendo os ganhos e microfades da montagem final. Cada junção inclui no máximo 24.000 samples (500 ms) de cada trecho adjacente; não sobrepõe falas. Loop usa a sessão backend. Cursor de reprodução é remapeado de saída para origem; bloco atual recebe destaque. Prévia tardia após fechar é encerrada pelo próprio ID, sem interromper sessão de transmissão.
- Duração e LUFS apresentados usam a montagem e volume manual. Commit e criação de outro clipe persistem duração efetiva e medida atualizada sem substituir o ganho congelado por outra normalização. A biblioteca exibe duração efetiva quando disponível.
- Sugestão de bordas usa limites reais de fala com margem editável de 0 a 2 s. Sem fala alinhada, sugere a origem completa. Só altera seleção; ouvir e aplicar Manter continuam explícitos. Mensagem informa a possibilidade de conteúdo não verbal fora da fala.
- Retranscrição recebe a seleção em samples, usa a fila E3 e apresenta texto atual/proposta para aceite explícito. Alinhamento completo, parcial, por segmentos e legado aparecem distintamente. Os guards E3 de concorrência, aplicação e persistência foram preservados.

## TDD e verificações

RED observado antes da implementação principal: quatro cenários API falhando pela ausência de endpoints/comandos; módulo de seleção textual ausente; módulo de estado UI ausente. O primeiro run também revelou teardown usando nome inexistente (`shutdown` na API); corrigido para fechar o store isolado, sem tratar esse erro de fixture como evidência funcional.

RED adicionais reproduzidos: correção de segmento sem palavras; duração efetiva ausente no clipe derivado; prévia que iniciava após fechar sem ser encerrada. Todos passaram depois das respectivas correções.

| Gate | Resultado |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e4-final` | **396 passed**, 17,74 s |
| Node `--test` em todos `tests/*.test.cjs` | **28 passed** |
| Node `--check` em `app.js`, `analysis.js`, `fuzzy.js`, `editor.js`, `transcript.js` | exit 0 |
| `git diff --check` | somente whitespace preexistente em `tests/test_e_mixer.py:431` |

Node executado pelo binário explícito `C:\Users\Hylex\AppData\Local\Author Software\nvm\installs\v24.21.0\node.exe`; Python com `PYTHONDONTWRITEBYTECODE=1`. Testes sintéticos, serviços de fundo desativados e chamadas de áudio substituídas nos testes de prévia.

Novos testes: `tests/test_editor_api.py`, `tests/test_editor_state.test.cjs`, `tests/test_transcript_selection.test.cjs`. Cobrem rollback de comando/clone, keep sem restaurar lacunas, undo/redo, correção seguida de título/save/reabertura, clone compartilhado, recuperação de draft, job tardio, fidelidade exata de samples do preview, monitor only, blocos/segmentos/legado, teclado, controller real com DOM mínimo e prévia tardia.

## Contrato para smoke WebView2

Embuta também os novos assets `ui/editor.css`, `ui/transcript.js` e `ui/editor.js` no harness isolado. Nenhum harness do agente principal foi modificado.

Endpoints:

- `get_clip_editor(clip_id)` retorna `editor` com `spans`, `source_samples`, `source_transcript`, `result`, duração/LUFS/volume, `draft_id`, `generation`, `can_undo`, `can_redo`.
- `edit_clip_command(clip_id, action, payload)` aceita `interval` em samples, `word_ids`/`text` para correção e token opcional `draft_id`/`generation` usado pela UI. Retorna `editor` e, em create, `clip` publicado.
- `preview_clip_edit(clip_id, mode, interval, junction, loop)`, modos `selection`, `result`, `junction`, retorna `play_id` e `ranges` no resultado renderizado. `get_editor_playback(play_id)` consulta; `stop_editor_preview(play_id)` encerra apenas sessão monitor.
- `suggest_clip_edges(clip_id, margin)` retorna intervalo e explicação sem mutação.

Seletores: `editorWords button[data-unit-id]`, `editorWaveform`, `editorStart`, `editorEnd`, `editorBoundary`, `editorCorrection`, `editorApplyText`, `editorKeep`, `editorRemove`, `editorRestore`, `editorCreate`, `editorUndo`, `editorRedo`, `editorHear`, `editorHearResult`, `editorHearJunction`, `editorJunction`, `editorLoop`, `editorMetrics`, `editorResult`, `editorStatus`, `editorRetranscribe`, `analysisCurrent`, `analysisProposal`.

Hooks do controller para preparação/espera: `state.integratedEditor.setSelection([start,end])`, `.selection`, `.run(action,{interval})`, `.flush()`, `.settle()`. Input real deve usar os elementos; hooks não substituem validação de pointer capture, layout e teclado. Para montar fixture com palavras, use `transcript_model.from_segments` e dados sintéticos. Para prévia, capture `_audio.play_clip` e forneça sessão fake com `set_loop`, `status`, `stop`, `monitor_only=True`; nenhuma saída física é necessária.

## Limites e insights

Não foi feita avaliação auditiva física nem benchmark de reconhecimento. O gate automatizado demonstra contratos/samples/UI lógica; apresentação, hit testing e gesto real ficam para o smoke WebView2 do agente principal. A fonte preservada continua sendo o WAV disponível na entrada do editor, sem recuperar áudio descartado anteriormente pelo pipeline de captura.

Uma correção manual é uma unidade editorial; os intervalos da origem podem ser descontínuos, e o texto não deve ser duplicado nem redistribuído em timestamps artificiais. A prévia deve recortar o renderer final para não introduzir rampas ou ganhos diferentes na junção. O autosave legado e o editor temporal precisam ter um único dono de texto, com baseline atualizado após comandos. O histórico da sessão não pode restaurar gerações antigas, pois isso tornaria um job tardio novamente elegível.

## Correções da revisão Sol

Dois reprodutores RED no controller real confirmaram: (1) a prévia antiga continuava ativa após mudar os spans e seu cursor era convertido pela montagem nova; (2) undo restaurava o documento, mas mantinha o campo de correção com o texto anterior.

`receive` agora invalida a prévia monitor própria quando a montagem recebida muda. O ID e a geração de prévia são invalidados imediatamente, antes de solicitar stop desse ID; polls em trânsito são ignorados. Comando malsucedido conserva a prévia e o mapeamento original. Nenhuma chamada para parar todas as sessões foi adicionada. Poll também verifica a geração, e sua falha tardia não limpa uma prévia mais recente.

O campo de correção e seus IDs são recalculados a partir da seleção e do documento recebido quando não há digitação pendente. Texto pendente e seus IDs permanecem até aplicação/descarte explícitos, inclusive em falha. Após aplicar com sucesso, o campo volta a acompanhar o documento. Testes adicionais cobrem undo/redo, seleção estreita dentro de bloco antes unido, texto pendente, corte falho, poll tardio após corte e stop explícito.

Alterados nesta rodada: `ui/editor.js`, `tests/test_editor_state.test.cjs` e este relatório. Gates desta rodada: **33 testes JS aprovados**, sintaxe dos cinco módulos JS aprovada; `git diff --check` permanece somente com whitespace preexistente em `tests/test_e_mixer.py:431`. O gate Python anterior de **396 testes** permanece válido, pois esta rodada não alterou Python. Novo congelamento para recheck Sol e smoke WebView2 coordenados pelo agente principal.

## Correção da dependência de loudness e acabamento visual

O smoke nativo expôs um defeito preexistente em `loudness.py`, agora apresentado pelo medidor E4.5: o denominador do primeiro filtro continha `a2=0.69065929318241`, produzindo um polo em DC; o correto é `0.73248077421585`. Além disso, a média mono anterior cancelava canais em oposição de fase antes de medir energia.

A correção usa os coeficientes a 48 kHz das tabelas 1 e 2, filtra cada canal e soma suas energias antes dos gates de 400 ms. Mono e estéreo L/R são suportados; outros layouts são rejeitados, pois exigem pesos explícitos. O limite de ganho usa o maior pico **por amostra** entre todos os canais. Não é medição true-peak/dBTP. Referência primária consultada: [ITU-R BS.1770-5, Anexo 1, tabelas 1/2 e equação 2](https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.1770-5-202311-I%21%21PDF-E.pdf).

Reamostragem reaproveita `audio_processing.resample_audio` da E1, com filtragem antialias; foi removido o `np.interp` do medidor. Os filtros usam `scipy.signal.lfilter` compilado, com estados independentes por canal, substituindo o loop Python. Não há migração dos ganhos existentes, normalização automática da biblioteca ou mudança no renderer. O teste de medição/commit comprova gains congelados e PCM antes/depois idênticos.

TDD: **11 novos casos RED** antes da correção; todos GREEN depois. Oráculo analítico independente usa a resposta em frequência dos coeficientes oficiais, sem importar as constantes do módulo testado. Casos cobrem 220/1000/5000 Hz, mono/estéreo em fase/oposição, estimativa abaixo de 400 ms, ganho conhecido, pico de um canal, 44,1/96 kHz, rejeição de alias e layout desconhecido. Dois testes opcionais usam o FFmpeg já instalado (`ebur128=peak=sample`, saída null, entrada PCM sintética por pipe), sem instalar nada. Nesta máquina ambos foram executados e aprovados: 1 kHz, amplitude 0,1, mono **−23,0 LUFS** e estéreo em oposição **−20,0 LUFS**.

Medição sintética local do custo de `integrated_lufs`, 3 s de seno 220 Hz/amplitude 0,1/48 kHz, mediana de 15 execuções após warmup, comparada com `HEAD:loudness.py`:

| Sinal | LUFS anterior → corrigido | Mediana anterior → corrigida |
|---|---|---|
| Mono | −10,5 → −23,9 | 32,021 → 2,447 ms |
| Estéreo `[x,x]` | −10,5 → −20,9 | 33,042 → 5,274 ms |
| Estéreo `[x,−x]` | indisponível → −20,9 | 32,151 → 5,200 ms |

Esses números descrevem uma sonda local, não um SLA ou benchmark de produção. O resultado estéreo ganha aproximadamente 3,01 LU sobre mono pela soma de energias; inversão de fase não altera o nível medido. Nenhuma avaliação auditiva foi realizada.

Após inspecionar a captura nativa `.worktrees/e4-webview-data-bu03u7fr/editor.png`, inputs/selects/textarea do editor receberam cores, bordas, fonte e foco do tema escuro, sem reescrever o layout. O título comum é “Editar clipe” e o aviso ficou “Salvar atualiza o clipe; fechar guarda o rascunho.” A nova captura e os gestos reais serão validados pelo agente principal.

Gate final desta rodada: `.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e4-meter-final` → **410 passed em 16,57 s**; **33 testes JS aprovados**; sintaxe dos cinco módulos JS aprovada; `git diff --check` somente whitespace preexistente em `tests/test_e_mixer.py:431`. Nenhum teste existente precisou ter seu oráculo relaxado/alterado. Arquivos da rodada: `loudness.py`, `tests/test_loudness_reference.py`, `tests/test_editor_api.py`, `ui/editor.css`, `ui/app.js`, `ui/index.html` e este relatório. Sem staging/commits; recongelado para smoke e revisões.

## Correções da revisão Astra — 30/10/2026

O teste anterior de monitor-only substituía `AudioEngine.play_clip` e não exercitava a política de sobreposição. Três novos testes usam API e motor reais, `MixerVoice` real e apenas mixer/rotas falsos, mantendo broadcast e monitor externo já ativos. Antes do patch, a prévia executava `stop_all(10)` tanto no sucesso quanto em falha de renderização ou falha de criação da voz no mixer: **3 RED**. Depois, os três passaram, mantendo identidade das sessões externas, seus eventos de execução, preferência persistida e ausência de rota de cabo na prévia.

`preview_clip_edit` agora copia a configuração apenas para essa audição, com `overlap_audio=True`. Não muda a preferência armazenada nem a política de reprodução pública. O controller continua responsável por encerrar somente sua prévia anterior. O botão legado “Testar no fone” do rodapé agora delega à prévia do controller integrado. Fechar o editor integrado deixa a sessão pública intacta, inclusive quando transmite o mesmo clipe em edição. Esses dois caminhos tiveram **2 testes JS RED → GREEN** adicionais.

No viewport estreito, `.editor-transport` continua `sticky`, com `top:0`, padding compacto e `scroll-padding-block` no corpo do modal para reservar espaço de navegação/foco. A validação nativa a 768 px, incluindo hit testing e campos não cobertos, será repetida pelo agente principal; o harness não foi alterado.

Gates: **44 testes Python focais aprovados** (editor, motor e mixer); gate completo `.venv\Scripts\python.exe -m pytest -q --basetemp=.worktrees/e4-astra-preview-final` → **413 passed em 18,58 s**; **35 testes JS aprovados**; sintaxe dos cinco módulos JS aprovada. `git diff --check` continua apontando somente o whitespace preexistente em `tests/test_e_mixer.py:431`.

Arquivos desta rodada: `main.py`, `ui/app.js`, `ui/editor.css`, `tests/test_editor_api.py`, `tests/test_edit_draft.test.cjs` e este relatório. Sem hardware, staging ou commits. Código novamente congelado para smoke e Sol → Astra.

## Rótulo do rodapé após smoke final

O smoke nativo a 768 px do agente principal aprovou sticky/hit testing, drag, teclado, prévia do rodapé e fechamento sem global stop. A captura `.worktrees/e4-webview-data-ist8rrho/editor.png` revelou que o estado público do mesmo clipe ainda mudava o rótulo do rodapé para “Parar Teste”, embora seu handler integrado iniciasse a prévia.

Delta mínimo: no editor integrado, o rótulo permanece “Testar no Fone”, desde a primeira abertura (módulo disponível, controller ainda não criado), inclusive durante uma prévia própria. O botão dedicado Parar continua encerrando somente a prévia do controller. Teste do handler/rótulo real reproduziu **1 RED** (“Parar Teste”) e passou após a correção, cobrindo broadcast e preview do mesmo clipe.

Gate desta rodada: **36 testes JS aprovados**, sintaxe de `ui/app.js` aprovada, diff-check apenas whitespace preexistente. Python não foi alterado; o gate anterior de **413 testes** permanece válido. Alterados somente `ui/app.js`, `tests/test_edit_draft.test.cjs` e este relatório. Congelado novamente, sem staging/commits.
