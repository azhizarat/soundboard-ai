# E7 — grupos de reprodução, finalização e relógio do mixer

Data: 30/10/2026. Implementação na branch `codex/soundboard-evolution`, sem staging/commit/reset. Delta comparado ao snapshot `.worktrees/e5-approved`; alterações E0–E5 e trabalho inicial preservados. Este lote não implementa `SequenceRunner`, schema de sequências nem editor de fluxos.

## Contratos entregues

- `play_id` UUID completo identifica cada reprodução, inclusive ocorrências repetidas do mesmo clipe. `group_id` identifica o dono e pode abranger vários lotes preparados.
- `CompletionEvent` imutável contém `play_id`, `clip_id`, `group_id`, `reason` (`completed`, `cancelled`, `failed`) e `error` opcional. Cada voz aceita uma única transição terminal. Eventos naturais só saem após todas as saídas terminarem.
- A adaptação de `on_finish(clip_id, play_id)` ou `on_finish()` inspeciona a assinatura antes de chamar. Uma exceção `TypeError` no corpo não tenta novamente. Novos consumidores recebem `on_complete(event)`.
- Sessões e leases são registrados antes da publicação. Renderização, validação de todos os PCM, offsets e rotas precedem o primeiro áudio. Falha de preparação não publica parte do lote. Recusa anterior à aceitação de sessões retorna erro sem evento fictício.
- Cada saída possui `sample_clock`. Publicação atômica bloqueia todos os mixers envolvidos em ordem fixa; o começo local é `sample_clock + delay_samples`, e cada voz começa nesse ponto mais `offset_samples`. Nenhum timer frontend participa do agendamento.
- As referências de saída, seus IDs e gerações são congelados. Reconfiguração concorrente é rejeitada. Desconexão/fechamento da saída obrigatória falha todos os lotes do mesmo dono, inclusive preparados e em outras rotas, limpa as vozes e não as restaura na reconexão. Preparações obsoletas não podem iniciar.
- Preferência `overlap_audio=False` só substitui avulsos de transmissão. Não interrompe grupos nem audições. Grupos e audições nunca acionam a política global de substituir avulsos.
- Stop Group cancela o dono antes das próprias vozes. Stop All invalida preparações, cancela donos/esperas via hooks e só então libera/paralisa vozes. É seguro reentrar no Stop All a partir do hook; nenhum novo playback é aceito durante essa transição.
- Callbacks saem dos locks de sessão, voz, renderização e registro do mixer. Consumidor que lança não interrompe a limpeza nem os demais consumidores. Os callbacks podem ocorrer na thread de áudio: E8 deve encaminhar trabalho pesado para seu executor, sem bloquear o callback.
- O PCM da montagem é copiado/congelado. Ganho da revisão é aplicado pelo renderer; volume da ocorrência é aplicado uma única vez na voz. A rampa de ducking é comum à saída, inclusive com offsets. Fades curtos zeram o restante do bloco após sua conclusão.
- Liberação de origem recebida por `_release_source` é idempotente por ocorrência em sucesso, falha e cancelamento. O chamador continua responsável por adquirir um lease por ocorrência antes de transferi-lo ao engine.
- Backend `legacy` mantém clipes simples e callbacks tipados, mas recusa grupos/agendamento. Um mixer sem contratos prepare/register/start é recusado, sem fallback que publique antes de registrar.

## API para E8

```python
engine.register_group_owner(group_id, cancel_executor_and_waits)

prepared = engine.prepare_group(
    group_id,
    [
        {"clip": resolved_context_a, "offset_samples": 0},
        {"clip": resolved_context_b, "offset_samples": 14_400},
    ],
    settings,
    monitor_only=False,
    on_complete=handle_completion,
)
# Cada context contém _source e a _revision imutável resolvida pelo store.
# context['_release_source'] transfere o lease para esta ocorrência.
# prepared.voices já têm play_id, group_id e sessão registrada.
engine.start_group(prepared, delay_samples=480)
# prepared.start_samples: {'cable': ..., 'monitor': ...}, conforme rotas ativas.

engine.stop_group(group_id, fade_ms=10.0)
engine.stop_all()
engine.unregister_group_owner(group_id)
```

`prepare_group` exige `group_id` não vazio e ao menos uma ocorrência. Offset/atraso devem ser inteiros de amostras não negativos (booleanos recusados). PCM não vazio, mono/estéreo, finito; volume da ocorrência e das saídas finito entre 0 e 3. Todas as saídas solicitadas pelo grupo são obrigatórias. Para audição, `monitor_only=True` impede a rota cabo e exige monitor audível distinto.

Preparar não inicia. Cada `PreparedGroup` só inicia uma vez. Cancelar um dono invalida permanentemente aquela identidade nesta instância do engine: uma nova execução usa novo UUID. `unregister_group_owner` remove o hook após conclusão normal; não reabilita uma identidade cancelada. E8 continua responsável por ignorar eventos atrasados por identidade da execução e por avançar apenas em `completed`.

`AudioMixer.prepare_group(group_id, items, settings, ...)` aceita itens `{clip, data, offset_samples, play_id?}` já renderizados. A API de engine deve ser preferida para preservar leases, epochs e registro. `AudioMixer.start_group`, `stop_group`, `cancel_prepared` e `DeviceMixer.invalidate_route` completam os contratos internos.

Fachada: `get_playback_capabilities()` retorna `groups`, `scheduled_playback` e `unavailable_reason`; o snapshot inicial inclui `playback_capabilities`. `stop_playback_group(group_id)` cancela o dono. Stop All de UI e `HotkeyManager.trigger_stop_all()` chegam à mesma transição do engine. Não há novo controle de composição nesta etapa.

## Evidência TDD

1. RED inicial: **14 falhas esperadas, 1 compatibilidade já verde**. Reproduzido callback que lançava `TypeError` chamado duas vezes; demais falhas exigiam contratos ausentes de grupos, eventos e ownership. GREEN integrado: **59 pass**.
2. RED: **5 falhas novas** para upgrade fade→stop imediato, release no preflight falho, evento legacy, capabilities da fachada e dispatch do hotkey. GREEN: **95 pass** com regressões de editor/API/perfis.
3. RED: **2 falhas novas** para Stop All reentrante e rampa de ducking compartilhada entre offsets. Testes adicionais de publicação atômica, montagem congelada, falha de consumidor e desconexão via stream falso já passaram. GREEN: **47 pass**.
4. RED: **2 falhas novas** para avulso ainda renderizando após Stop All e reconfiguração concorrente de dispositivo. GREEN: **41 pass**.
5. RED: **1 falha nova** para mixer sem contrato publicar voz antes do registro. Removido caminho inseguro usado apenas pelo fake antigo; fixture E4 passa a usar `AudioMixer` real com streams falsos. GREEN: **39 pass**.

Os três arquivos E7 contêm **39 casos de teste** após as correções das revisões Sol/Astra abaixo. Usam PCM sintético, engine/mixers reais, `render_block` real, barreiras/eventos em corridas e streams/dispositivos falsos; não instalam hotkeys, captura ou hardware reais.

## Gates e isolamento

Ambiente explícito em todas as execuções Python: `PYTHONDONTWRITEBYTECODE=1`, `SOUNDBOARD_MODELS_DIR=D:\repos\soundboard-ai\.worktrees\e7-models`, `SOUNDBOARD_SEPARADOS_ROOT=D:\repos\soundboard-ai\.worktrees\e7-separados`. Python `.venv\Scripts\python.exe` 3.13, sem depender de `PYTHONUTF8`. Basetemps exclusivos `.worktrees/e7-red*`, `.worktrees/e7-green*`, `.worktrees/e7-full*`. Não foram lidas bibliotecas/modelos pessoais.

- Full intermediário: `python -m pytest -q --basetemp=.worktrees/e7-full1` → **497 pass, 65,70 s**.
- Full antes da revisão Sol, após remoção do fallback inseguro: `python -m pytest -q --basetemp=.worktrees/e7-full2` → **498 pass, 60,01 s**. O patch da revisão tem gate focado próprio abaixo; o full final fica com o coordenador.
- JavaScript: Node real `C:\Users\Hylex\AppData\Local\Author Software\nvm\installs\v24.21.0\node.exe`, seis arquivos `test_*.test.cjs` → **41 pass**. Nenhum módulo UI foi alterado por E7.
- `git diff --check`: somente `tests/test_e_mixer.py:431: new blank line at EOF`, preexistente. Não limpo incidentalmente.

O coordenador executou smoke independente `.worktrees/e7-engine-smoke.py`, **PASS exit 0**, repetido após ajustes de concorrência/ducking. Usou Store, renderer, engine e DeviceMixer reais com streams falsos: montagem `[[0,960],[1920,2880]]`, volumes 1 e 0,5, offset 240 e delay 480; clocks cabo 13/monitor 29 produziram inícios 493/509 e PCM esperado nas duas saídas. Confirmou registro antes de start, eventos somente após ambas as saídas, 8 leases liberados uma vez, isolamento de monitor externo, falha por perda de cabo, owner antes de eventos no Stop All, invalidação de grupo preparado e hash/revisão preservados. Dados sintéticos finais `.worktrees/e7-engine-data-q3mew229`.

## Arquivos e limites

Produto: `audio_engine.py`, `mixer.py`, `main.py`, `hotkeys.py`. Testes novos: `test_completion_events.py`, `test_mixer_schedule.py`, `test_playback_groups.py`. Fixture atualizada: `test_editor_api.py`. Este relatório é a documentação própria do lote.

Sem sincronização física perfeita entre dispositivos: cada saída tem seu próprio relógio e latência; o agendamento garante alinhamento de vozes dentro da mesma saída. Não houve audição ou desconexão em hardware real; a entrega ao vivo continua condicionada a esse aceite. Backend legado não ganha simultaneidade por threads. Esperas de sequência e barreiras entre etapas pertencem a E8; somente hooks, grupos e offsets foram preparados aqui.

Insights: registro precisa preceder publicação mesmo para áudios curtíssimos; callbacks compatíveis exigem adaptação de assinatura, nunca retry por erro do corpo; o epoch de Stop All precisa nascer antes do decode/render de avulsos; ducking é envelope da saída, não de cada ramo; fixtures que publicam antes de registrar ocultam justamente a corrida que grupos precisam eliminar.

## Revisão Sol — propagação entre lotes do mesmo dono

Sol reproduziu um P2: com dois lotes de um `group_id` usando rotas distintas, a perda do cabo encerrava apenas o lote dessa rota. O hook cancelava as esperas, mas o lote monitor continuava e terminava `completed`. O engine deve parar áudio do dono sem delegar essa obrigação ao hook.

RED: **2 falhas** no teste parametrizado `test_owner_termination_reaches_other_routes_and_prepared_batches_preserving_external`, por perda de saída e por cancelamento individual de uma voz: retornava um evento quando três eram exigidos. O teste inclui cabo ativo, monitor ativo, monitor preparado, hook que só registra cancelamento de esperas e outro dono no monitor. A primeira tentativa verde revelou uma omissão no oracle do teste externo (comparava PCM a 0,92 sem o limitador); o controle passou a usar volume 0,25 e comparação linear explícita.

Correção restrita a `audio_engine.py`, `mixer.py` e testes E7: uma primeira finalização não natural invalida o dono e termina todos os seus lotes com o motivo original; a seleção por perda de rota também inclui os demais lotes daquele dono. `group_id=None` permanece ausência de dono, nunca um grupo comum de avulsos. A fila de completion do engine é drenada iterativamente fora dos locks: finalizar irmãos ou parar outro dono dentro de um consumidor não reentra callbacks.

GREEN: **94 pass em 5,62 s**, comando `python -m pytest tests/test_playback_groups.py tests/test_completion_events.py tests/test_mixer_schedule.py tests/test_e_mixer.py tests/test_audio_engine.py tests/test_editor_api.py tests/test_api_state.py -q --basetemp=.worktrees/e7-sol-green2`, com o mesmo isolamento de ambiente. Verificados três eventos e três leases exatamente uma vez, hook antes dos eventos, locks livres por aquisição em outra thread, monitor externo preservado, preparado recusado após término, avulsos sem dono independentes e consumidor sem reentrada. Sem full repetido neste patch, conforme coordenação; revisão e gate final independentes permanecem com o coordenador.

## Revisão Astra — reserva e instalação de handles de saída

Astra reproduziu um P1 na configuração concorrente: A abria dispositivo 1 e aguardava no construtor; B concluía dispositivo 2; ao retornar, A sobrescrevia o handle instalado e vazava o de B. O lote B podia emitir seu PCM pelo dispositivo 1 apesar da rota lógica congelada apontar para 2.

RED inicial: **3 falhas reais** com `AudioMixer` e `OutputStream` falso: vencedor sobrescrito, shutdown durante abertura ressuscitando stream, e exceção de `start()` deixando handle construído aberto. Um segundo RED fechou a janela entre health-check bem-sucedido e congelamento da rota: fechar o handle nessa janela ainda permitia preparar um grupo sem saída instalada.

Correção por reserva, sem lock durante I/O PortAudio: configuração troca dispositivo e invalida a reserva anterior numa transição protegida; cada abertura captura dispositivo e epoch. Somente a reserva ainda atual pode instalar seu handle. Perdedores e handles cujo `start()` falhou são fechados individualmente, sem tocar o vencedor. Shutdown invalida reservas pendentes e bloqueia novas preparações. Fechamento tenta `close()` mesmo se `stop()` lançar.

Callbacks carregam a mesma identidade. A validação do callback de áudio ocorre dentro do lock que lê vozes, produzindo silêncio antes da instalação e após obsolescência, sem consumir amostras/relógio do vencedor. `finished_callback` de handle antigo/perdedor não invalida a rota nova. As notificações de perda carregam geração, para que uma notificação atrasada não atinja lotes preparados depois daquela transição. A última reserva nativa fica registrada após close, permitindo rejeitar ausência de handle entre health-check, congelamento e início.

GREEN final do patch: **99 pass em 5,07 s**, nos mesmos sete arquivos do gate Sol, `--basetemp=.worktrees/e7-astra-green3`. Testes usam dois threads/eventos, callbacks dentro de `start()`, `finished_callback` em outra thread durante close e notificação tardia explícita. Com B ativo, o handle perdedor emite somente silêncio; somente o handle 2 emite PCM 0,3; ambos ficam fechados após shutdown. Também cobertos shutdown durante construtor, erro em start, encerramento durante start e fechamento após health-check. A fixture antiga de stream saudável em `test_e_mixer.py` recebeu seu token de identidade; o blankEOF preexistente permanece, agora na linha 432.

Delta desta correção: `mixer.py`, `tests/test_mixer_schedule.py`, uma linha de fixture em `tests/test_e_mixer.py` e este relatório. Sem hardware e sem full repetido; Sol→Astra e full final independentes ficam com o coordenador.

## Encerramento pelo coordenador

SPEC_PASS Sol e QUALITY_PASS Astra, ambos com 59 testes focados; Astra repetiu independentemente o cenário dos dois handles e confirmou dispositivo/PCM corretos e ambos fechados. Gate completo nativo: **507 Python em 28,16 s + 41 JavaScript**; sintaxe dos módulos UI aprovada. Só permanece o whitespace preexistente em `tests/test_e_mixer.py:432`. Smoke Store/renderer/engine/mixers do coordenador aprovado com nove leases, três lotes do dono em rotas distintas e externo preservado; dados `.worktrees/e7-engine-data-0ubtfvae`. E7 concluída, sem prova de sincronização ou recuperação em hardware físico.
