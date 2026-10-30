# E8 — fluxos sonoros completos

Data: 30/10/2026. Implementação concluída na branch `codex/soundboard-evolution`, com delta sobre `.worktrees/e7-approved`. Sem staging, commit, reset ou descarte de alterações anteriores. Revisões Sol/Astra são a próxima verificação do coordenador.

## Contratos entregues

- Banco schema **4**, com coleção `sequences`. Migração explícita preserva campos anteriores, revisões, rascunhos e banco vocal. Backup `soundboard.v{versão_original}.sequences.bak.json` contém os bytes textuais de entrada; divergência de backup impede migração. Não substitui os backups anteriores. Execução ativa nunca é persistida ou retomada após reiniciar o app.
- Documento schema **1**: `id`, `name`, `revision` otimista, `hotkey`, `blocks`. Cada bloco tem ID próprio. Tipos `play`, `wait`, `parallel`, `manual`. `play` fixa `clip_id` + `revision_id`, `interval: [start_sample,end_sample)` e `volume`. O intervalo usa PCM a 48 kHz **já renderizado da revisão**, inclusive montagens com lacunas. Alterações de corte/volume são locais; não alteram fonte, revisão nem outras ocorrências.
- Validação completa antes de iniciar: referências, arquivos disponíveis, intervalos, volumes finitos 0–3, esperas finitas não negativas, IDs únicos, árvore sem ciclos, dois ou mais ramos não vazios, limite de 512 blocos. Sem paralelo aninhado ou acionamento manual dentro de ramo. O editor permite montagem incompleta local; salvar/executar exige documento válido. A prévia de um bloco isolado valida seu recorte, permitindo escutá-lo enquanto outros ramos ainda estão em construção.
- `save_sequence` cria referências nomeadas `sequence:{sequence_id}:{occurrence_id}` às fontes. Revisões permanecem imutáveis. Salvar/remover sequência e referências usa transação com rollback sob o lock do store. Exclusão de clipe utilizado é bloqueada com os nomes das sequências afetadas. Remover ocorrência/sequência não exclui clipe ou fonte.
- `sequence_details` informa ocorrências com revisão nova. Atualizar referência é explícito; na UI reinicia o intervalo para a nova revisão e preserva volume. Nenhuma transcrição atual é apresentada como âncora da revisão antiga.
- `SequenceRunner` executa no backend com relógio monotônico injetável. Prepara **todos** os sons/rotas antes do primeiro início, registra dono antes da preparação e adquire lease por ocorrência. Cada partida usa UUID novo em `execution_id`/`group_id`. Documento e settings são copiados/congelados para a execução.
- Primeiros sons dos ramos compartilham um lote E7 e a mesma referência de amostras por saída; esperas iniciais viram offsets. Áudio só avança em `CompletionEvent.reason == completed`. A barreira inclui todos os ramos, esperas finais e ramos compostos apenas por espera. Esperas consecutivas preservam deadlines, sem acumular atraso de polling.
- Callback de áudio só sinaliza cancelamento ou publica evento/faz wake do executor. Não decodifica, consulta store ou inicia o próximo áudio nessa thread. Chamadas ao engine ficam fora do lock de estado. Cancelamento/falha interrompe apenas o dono e seus lotes presentes/futuros, preservando avulsos externos. Stop All cancela o dono e esperas antes das vozes.
- Uma execução ativa. Substituição/reinício exige `replace=True`; identidade cancelada nunca é reutilizada. Avanço antecipado retorna imediatamente sem esperar um lock e sem se acumular; nova checagem da posição impede consumir outra espera manual. Callbacks/avançar com execução antiga são ignorados. Latch de key-down/keyup rejeita repeat e permanece durante refresh de atalhos.
- UI utilizável integrada: biblioteca arrastável, blocos/ramos lado a lado, inclusão, reordenação por setas ou arraste, transferência a ramo, duplicação com novos IDs, remoção, corte/volume local, undo/redo, aviso/atualização de revisão, salvar, botões salvos na coleção, atalho global, Iniciar/Avançar/Parar/Reiniciar, prévia de bloco/grupo/tudo somente no monitor. Polling frontend apenas consulta estado; não dispara etapas.
- Status mostra bloco atual, próximo bloco, ramos e espera pelo operador. Duração com espera manual é `known_duration_ms` mais quantidade de acionamentos; `duration_ms` é `null`. Backend legacy explica indisponibilidade, sem simular paralelo com threads.

## APIs e hooks

```python
store.save_sequence(document)                  # retorna documento com revision incrementada
store.get_sequence(sequence_id)
store.sequence_details(sequence_id)             # document, duration_ms, known_duration_ms,
                                                # manual_count, new_revisions
store.resolve_sequence_occurrence(block)         # _source, _revision, _preview_ranges, volume
store.delete_sequence(sequence_id)

runner = SequenceRunner(store, engine, clock=time.monotonic, background=True)
runner.start(document, settings, monitor_only=False, replace=False)
runner.snapshot()
runner.advance(execution_id, repeat=False)
runner.stop(execution_id=None)
runner.close()
# Para relógio de teste: background=False, alterar clock e chamar pump().
runner.pump()
```

Fachada em `sequence_service.py`, herdada por `SoundboardAPI`; atributo `api._sequence_runner`:

- `get_sequences()` → `{ok, sequences, clips, capabilities, status}`. Sequências inválidas continuam listadas com erro para reparo.
- `save_sequence(document)`, `delete_sequence(sequence_id)`, `validate_sequence(document)`.
- `pin_sequence_clip(clip_id)` → `{ok, occurrence}`. Clipe legado sem revisão pode criar sua revisão inicial; edição pendente precisa ser salva explicitamente antes.
- `start_sequence(sequence_id, replace=False)` e `preview_sequence(document, block_id=None, replace=False)` → `{ok, status}`.
- `advance_sequence(execution_id, repeat=False)`, `stop_sequence(execution_id=None)`, `get_sequence_status()`.
- Erros de validação/persistência retornam `{ok: False, error}`. UI exibe o motivo. `get_playback_capabilities` mantém o contrato E7.

Status: `idle`, `preparing`, `playing`, `waiting`, `manual`, `completed`, `cancelled`, `failed`, com `execution_id`, `sequence_id`, `document`, `monitor_only`, `error`, `active_blocks`, `current_block`, `next_block`, `branches` e duração.

Hook de UI `window.sequenceEditor`: `open(sequenceId?)`, `model()`, `render()`, `reload()`, `save()`, `start(replace=false)`, `preview(blockId=null)`, `status()`. Modelo: `value()`, `command(action,payload)`, `undo()`, `redo()`, `load(document)`.

IDs: `btnSequences`, `sequencePanel`, `sequenceName`, `sequenceHotkey`, `sequenceClipSelect`, `sequenceLibrary`, `sequenceBlocks`, `sequenceMessage`, `sequenceDuration`, `sequenceCapability`, `sequenceStatus`, `sequenceNext`, `sequenceBranchesStatus`, `sequencePads`. Controles `btnSequenceSave/Start/Restart/Advance/Stop/Preview/Undo/Redo/New/Reload/Delete`. Cards `[data-block-id]`, ações `[data-action]`, inputs `[data-field=start/end/volume/ms]`, listas de ramo `.sequence-block-list[data-parent][data-branch]`.

## TDD e regressões

1. RED inicial dos 11 casos de contrato: persistência, validação e schema ausentes. GREEN após implementar documento/store e corrigir chamada do teste ao contrato existente `update_edit(clip_id, fields)`.
2. RED dos nove casos iniciais do executor. GREEN com engine/mixer reais e relógio controlado; corrigida leitura da estrutura real `_voice_states` no teste de início comum.
3. RED de quatro casos de fachada/atalhos. GREEN integrado **24 Python**.
4. RED dos seis casos de editor JavaScript. GREEN **6/6**, incluindo identidade de duplicações, ramos, ajustes locais, história e integração dos controles.
5. RED adicional **4 falhas**: avanço enfileirado antes da espera, atraso acumulado em esperas consecutivas, thread ociosa e refresh rearmando tecla mantida. GREEN **74** com persistência/vozes. Cobertura complementar também verificou cancelamento no meio da preparação e corte após uma lacuna da montagem.
6. RED de prévia de bloco válido durante montagem incompleta. GREEN da fachada **6/6**.
7. Primeiro full: **527 pass / 4 falhas**, todas expectativas antigas `schema_version == 3`. Ajustadas apenas para schema 4 nos cenários existentes de `test_edit_persistence.py` e `test_voice_profiles.py`, preservando todas as verificações de dados/backups.
8. Gate final: **538 Python pass, 47 JavaScript pass**. E8 adiciona **31 casos Python + 6 JavaScript** sobre baseline 507/41. Logs `.worktrees/e8-full-final.log` e `.worktrees/e8-js-final.log`. Python final: 76,67 s, exit 0. Node: 47 pass, exit 0.

Comando Python final:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:NODE_BINARY = 'C:\Users\Hylex\AppData\Local\Author Software\nvm\installs\v24.21.0\node.exe'
$env:SOUNDBOARD_MODELS_DIR = 'D:\repos\soundboard-ai\.worktrees\e8-models'
$env:SOUNDBOARD_SEPARADOS_ROOT = 'D:\repos\soundboard-ai\.worktrees\e8-separados'
.\.venv\Scripts\python.exe -m pytest tests -q --tb=short --basetemp=.worktrees/e8-full-final
& $env:NODE_BINARY --test tests/*.test.cjs
```

Use basetemp novo ao reproduzir. UTF-8 explícito nas leituras/escritas JSON. Nenhuma biblioteca/modelo pessoal, captura, hotkey ou dispositivo físico foi acionado pelos testes. `git diff --check` continua apontando somente o blank EOF preexistente em `tests/test_e_mixer.py:432`; arquivos E8 em LF.

## Demonstração M2 e inspeção visual

O coordenador executou `.worktrees/e8-webview-smoke.py` com WebView2 real, entradas nativas, fontes sintéticas, engine/mixers reais e clock/pump em passos de 480 amostras. Hardware, inferência e biblioteca pessoal ficam fora desse harness.

- Backend integrado: **PASS**, diálogo com **2,1 s calculáveis + acionamento manual**, quatro ocorrências distintas, mesma origem em duas ocorrências, corte/volume local, revisão antiga após editar origem, prévia só no monitor, Stop All em áudio/espera/manual sem som futuro e hashes de fontes preservados. Evidência `.worktrees/e8-webview-data-hntez9ai`.
- UI nativa: **PASS** para M2, duplicação, reordenação, undo/redo, prévia no monitor, avanço e status; modal sem overflow em 1280 e 768 px. Primeira inspeção encontrou botões estáticos de baixo contraste; CSS corrigido com fundo/texto, hover, disabled e foco explícitos.
- Revalidação visual final em 768 px: **PASS**, botões legíveis e M2/undo-redo preservados, exit 0. Captura `.worktrees/e8-webview-data-2sgmpn5u/editor.png`.

## Arquivos e limites

Novos: `sound_sequences.py`, `sequence_runner.py`, `sequence_service.py`, `ui/sequences.js`, `ui/sequences.css`, os quatro arquivos `tests/test_sound_sequences.py`, `test_sequence_runner.py`, `test_sequence_api.py`, `test_sequence_editor.test.cjs` e este relatório.

Alterados neste lote: `store.py`, `main.py`, `hotkeys.py`, `ui/index.html`, `ui/app.js`, expectativas de schema em `tests/test_edit_persistence.py` e `tests/test_voice_profiles.py`. Guia e log consolidado pertencem ao coordenador e não foram editados pelo implementador E8.

Limites previstos: uma execução ativa, grupos sem aninhamento, sem manual em ramo, sem pausa/retomada global, ciclos, saltos, exportação de áudio ou pack de fluxos. Prévia compartilha o mesmo executor e não substitui transmissão ativa sem ação explícita. Histórico de montagem fica na sessão da UI; a definição salva persiste. Não foi validado hardware real ou diálogo pessoal; usuário autorizou corpus sintético. A sincronização garantida é a referência do mixer por saída, não latência física idêntica entre dispositivos.

Insights confirmados: a barreira pertence ao estado completo dos ramos, não apenas às vozes; o instante de aceitação do avanço deve preceder espera por locks; refresh de registro não equivale a key-up; uma prévia local precisa validar seu próprio recorte sem exigir que toda a montagem em edição esteja pronta.

## Correção da revisão Sol — fronteira entre início e Stop All

Sol reproduziu uma corrida: início publicado como `preparing`, registro do dono suspenso, Stop All retorna sem conhecer esse dono, registro retoma e inicia áudio na geração seguinte. A mesma janela existia durante validação, antes de publicar `_run`.

RED **2/2** confirmou ambas as fronteiras com barreiras determinísticas e store/engine/mixer reais: após Stop All, o estado incorreto era `playing`. Correção: `SequenceRunner.start` chama `engine.capture_playback_epoch()` na entrada, antes do lock serial e da validação; `register_group_owner(..., expected_epoch=epoch)` compara geração e registra atomicamente sob o lock do engine. Solicitação obsoleta recebe sinal de cancelamento **fora do lock** e é recusada. Stop All não espera validação ou I/O. Registro existente sem `expected_epoch` preserva compatibilidade E7.

GREEN **72/72** em `test_sequence_runner.py`, `test_sequence_api.py`, `test_sound_sequences.py`, `test_playback_groups.py`, `test_completion_events.py` e `test_mixer_schedule.py`, basetemp `.worktrees/e8-green-owner-epoch`, exit 0. Os testes também confirmam ausência de sessões/vozes/leases após a corrida e aceitação de uma solicitação realmente posterior à parada.

Delta adicional: `audio_engine.py`, `sequence_runner.py`, `tests/test_sequence_runner.py`. Agora há **33 casos Python E8**. O full **538/47** acima antecede esta correção; full atualizado fica para o fechamento das revisões, conforme coordenação. Nenhuma alteração de UI nesta correção.

## Correção da revisão Astra — identidade assíncrona e recarga de conflito

RED **5 casos JavaScript** reproduziu `Iniciar A → salvar A pendente → abrir B → resposta de A`, que iniciava B e aplicava a revisão de A no modelo B; também reproduziu ausência de recarga efetiva, cache obsoleto, resposta de abertura antiga e edição feita durante recarga. Salvar/iniciar agora capturam modelo/documento/ID na entrada. Há no máximo um salvamento pendente por modelo; nova ação no mesmo modelo é recusada. Edições feitas durante o await permanecem locais e dirty; apenas a revisão do modelo capturado avança. Tickets impedem abertura/refresco obsoletos de substituir a seleção mais recente.

`refreshLibrary()` atualiza somente lista/metadados. `reload({discardLocal:false})` recarrega o documento salvo do modelo capturado e reinicia seu histórico, mas recusa alterações locais. `reload({discardLocal:true})` é a ação explícita de descarte; se o usuário editar depois do clique e antes da resposta, a recarga é recusada e a edição é preservada. Reabrir cache limpo adota uma revisão remota nova; cache dirty não é descartado por abertura ou polling.

Controles: `btnSequenceRefreshLibrary` (**Atualizar biblioteca**), `btnSequenceReload` (**Recarregar documento salvo**), `btnSequenceDiscardReload` (**Descartar alterações e recarregar**), visível somente quando o modelo tem alterações locais. Botões usam contraste escuro e quebra de texto na sidebar estreita.

RED **2 casos adicionais** demonstrou a mesma troca indevida de destino ao adicionar clipe ou atualizar referência com uma resposta pendente; ambas as ações agora mantêm o modelo capturado. GREEN **13/13** no editor de sequências e **54/54 JavaScript** na regressão completa, log `.worktrees/e8-astra-js.log`. E8 passa a ter **13 casos JavaScript**. Backend não mudou nesta rodada; full Python aguarda fechamento da revisão.

Smoke nativo do coordenador em 768 px: **PASS** ao suspender resposta saveA após gravar, editar nome de A localmente, abrir B e liberar A: executor inicia A, B conserva sua revisão, reabrir A preserva nome local e revisão avançada. Recarregar recusa dirty; descarte explícito restaura salvo e oculta seu botão. M2 continua passando. Captura `.worktrees/e8-webview-data-jtp0ofnc/editor.png`, exit 0; a inspeção identificou texto cortado no novo botão da sidebar, corrigido com wrap/max-width antes do novo freeze.
