# E10.A — grupos aleatórios curados e variantes

## Estado desta entrega

Implementação do lote A de E10.1/E10.2 integrada em `4deb17d`, na worktree `C:\Users\Hylex\.codex\worktrees\soundboard-evolution\soundboard-ai`. A integração preservou as mudanças de replay, medidores, microfone e roteamento de `main` (`4218e47`). O checkout pessoal em D: não foi alterado após a transferência do trabalho para C:.

**Requisitos: SPEC_PASS Sol**, com 70 testes Python e 27 JavaScript direcionados, informado pelo coordenador. **Qualidade E10.A: aguardando re-review Astra após corrigir dois P2**. A aprovação de qualidade da integração com `main` não equivale à aprovação de qualidade deste lote. As correções abaixo foram aplicadas sobre `4deb17d`; o gate integrado listado adiante precede esse delta.

O escopo implementado cobre grupos aleatórios em coleções, variantes de velocidade/tom/reverso, superfícies de operação/edição, proveniência, cancelamento e compatibilidade dos consumidores existentes. Importação ampliada e packs com grafo editável de coleções/sequências pertencem ao lote B e continuam pendentes.

Referências: [plano consolidado](../superpowers/plans/2026-10-29-evolucao-consolidada.md), [complemento E10](../superpowers/plans/2026-10-30-e10-execucao.md) e [validação da integração](2026-11-01-integracao-main.md).

## Contratos de grupos curados

O grupo é um alvo do próprio botão da coleção; referências não duplicam nem movem mídia:

```json
{
  "target": {
    "type": "shuffle",
    "items": [
      {"type": "clip", "id": "clip-a"},
      {"type": "sequence", "id": "sequence-b"}
    ]
  },
  "cooldown_ms": 800
}
```

- Entre 1 e 128 referências existentes por grupo; sem duplicatas e sem grupos aninhados. Clipe pendente não pode ser membro. A exclusão de clipe/sequência referenciado exige remover a referência da coleção.
- Saco embaralhado percorre os membros sem reposição. Na renovação do saco, evita a última escolha quando há alternativas. RNG e relógio são injetáveis nos testes.
- O estado do saco e da última escolha pertence à sessão. Mudar os membros reinicia o estado; reabrir o aplicativo também. A UI informa essa condição.
- Cooldown de 0 a 60.000 ms, aplicado também à repetição explícita. Repetir a última escolha não consome outro elemento do saco. Sem escolha anterior, a repetição falha explicitamente.
- Não há disparo provocado por fala ambiente. Clique, gesto acessível e atalho continuam entradas intencionais do dispatcher E9.
- `trigger_collection_pad(collection_id, pad_id, phase='press', repeat=False, token=None, repeat_last=False)` recebe a repetição explícita no último parâmetro. `repeat` continua representando autorepeat de teclado, que não cria outro disparo.
- A resposta bem-sucedida contém `chosen_target`. `get_operation_status()` inclui `shuffle_choices`, indexado por `collection_id:pad_id`. A UI diz **Última escolha**, sem equiparar seleção a áudio efetivamente tocado; o preparo pode falhar ou ser cancelado.
- As regras existentes de grupo de áudio, redisparo, avanço manual de sequência, loop e hold continuam aplicáveis. A escolha não cria um segundo caminho de reprodução. Release e Stop All não aguardam cooldown.

O owner continua registrado antes de qualquer preparo, com epoch capturado na admissão. Hold conserva token por gesto e tombstone para soltura antecipada. Ensaios continuam exclusivamente no monitor, e mudança de modo/Stop All cancelam preparos anteriores. As invariantes E9 de loop sem estado terminal transitório e de espera sem lock da API foram preservadas.

## Contratos de variantes

| API | Comportamento |
|---|---|
| `get_variant_capabilities()` | Informa filtros disponíveis, faixas, limite de duração e processador |
| `create_audio_variant(clip_id, options, revision_id=None)` | Publica outro clipe com nova revisão e origem compartilhada |
| `preview_audio_variant(clip_id, options, revision_id=None, token=None)` | Prepara e toca exclusivamente no monitor; aceita token conhecido antes do render |
| `stop_variant_preview(token)` | Cancela inclusive preparo em andamento ou solicitação ainda não admitida |
| `stop_editor_preview(play_id)` | Permanece disponível para parar a sessão de monitor já identificada |

`options` admite somente `speed`, `pitch_semitones` e `reverse`. Defaults: `1`, `0` e `false`. A API rejeita números não finitos, booleanos em campos numéricos e propriedades desconhecidas.

Cada revisão de variante contém:

```json
{
  "variant": {"speed": 2, "pitch_semitones": 0, "reverse": true},
  "variant_origin": {
    "clip_id": "clip-a",
    "revision_id": "revisao-publicada-escolhida",
    "source_id": "origem-preservada"
  },
  "variant_processor": "ffmpeg-atempo-asetrate-v1"
}
```

A revisão contém sua própria montagem e ganhos congelados; não depende da revisão atualmente escolhida pelo clipe pai para reproduzir. A origem PCM e seu hash permanecem intactos. A publicação grava clipe/revisão atomicamente e desfaz a inserção em memória se a persistência falhar. Um lease conserva a origem durante o processamento.

A UI usa a revisão publicada selecionada ao abrir o editor. Alterações ainda em draft exigem salvar e reabrir para serem usadas na criação da variante; esse limite aparece no painel. Não é permitido encadear variantes: deve-se partir do clipe original.

### Processamento e tempo

- PCM mono/estéreo a 48 kHz; até 120 segundos de montagem de entrada.
- Velocidade entre 0,5 e 2x, independente do tom; tom entre -12 e +12 semitons, independente da duração. Reverso usa inversão de amostras por NumPy.
- Velocidade/tom usam FFmpeg já disponível, com `asetrate`, `aresample` e `atempo`. O caminho configurado pelo aplicativo é respeitado nas capacidades, criação e reprodução. Sem esses filtros, velocidade/tom ficam indisponíveis; reverso continua possível. Não foram instaladas dependências ou modelos para o lote.
- A consulta de filtros tem timeout de 10 s; o processamento tem timeout de 60 s. A duração final é fixada em amostras por `round(total / speed)`; pequenas diferenças de blocos do filtro são cortadas ou completadas com zeros.
- Duração efetiva e loudness são calculados sobre a saída processada, considerando o volume local na medição. O ganho congelado da montagem permanece aplicado uma única vez.
- A waveform de edição mantém a régua da origem sem efeitos. A waveform da biblioteca representa o resultado renderizado. Prévia de seleção/junção converte os intervalos da montagem para a saída processada.
- Se esse mapeamento produzir zero amostras, a API rejeita a prévia antes de obter lease ou chamar reprodução. O renderer também rejeita `_preview_ranges: []`; somente a ausência da chave significa resultado inteiro.
- A projeção temporal transforma limites de saída, preservando âncoras na origem. O cursor da UI desfaz velocidade/reverso antes de mapear lacunas da montagem. Em reverso, a UI identifica o texto como descrição da origem; não apresenta esse texto como uma transcrição da fala invertida.

Os sinais sintéticos verificam frequência, comprimento, igualdade de amostras e coerência de mapas. Não medem qualidade perceptual. Alterações de velocidade/tom podem gerar artefatos; transientes e timbre de fala real não foram avaliados com corpus.

### Cancelamento da prévia

O frontend gera um token por prévia antes de chamar o bridge; existe fallback para WebView sem `crypto.randomUUID`. Stop e fechar editor enviam `stop_variant_preview(token)` imediatamente, mesmo sem `play_id` recebido. O backend registra owner/cancelamento e epoch antes do render e impede publicação depois de release/Stop All. A resposta antiga que eventualmente contém uma sessão também é parada pelo seu próprio ID.

A UI bloqueia pedidos concorrentes de criação no mesmo painel e ignora atualizações visuais de uma abertura anterior. Prévia exige monitor distinto da transmissão e não altera a configuração global de sobreposição.

## Proveniência vocal e consumidores existentes

`selected_source_file` remove explicitamente a variante, ganhos e microfades ao montar o trecho destinado à referência vocal. O cadastro continua baseado nos intervalos originais e na confirmação humana do fluxo E5. O filho não copia `voice_action_id`, propostas/gerações de análise ou identidade anterior de pack. Evidência de origem e de múltiplas vozes é preservada; mudar rótulo não transforma áudio processado em nova evidência original.

Sequências E8 fixam revisão e intervalos na duração **processada**. O teste de revisão anterior compara PCM depois de editar o clipe atual: a ocorrência fixada continua equivalente e rejeita intervalos fora da sua própria saída.

O pack compacto existente renderiza efeitos e volume no WAV exportado. Sua identidade de montagem inclui efeito e versão de processador, evitando colisão entre receitas diferentes. O teste compara o PCM extraído do ZIP com a saída do renderer. Isso não representa entrega do pack editável com grafo, reservado ao lote B.

O texto derivado da variante e do manifesto compacto usa a mesma projeção sobre os spans da revisão escolhida. A análise e as correções humanas continuam ancoradas à origem: não se inventa um histórico textual inexistente por revisão de áudio. Correções alinhadas têm precedência sobre texto reconhecido antigo dos segmentos. Segmentos/evidência vocal originais não são apagados por essa projeção. Texto legado sem âncoras continua explicitamente sem alinhamento, sem inventar recortes por palavra.

## Schema e persistência

Schema global atualizado de 5 para **6**, para que o binário anterior não aceite silenciosamente revisões que não sabe renderizar. A migração preserva registros existentes e exige backup verificável `soundboard.v5.variants.bak.json` quando a entrada é schema 5. Backup divergente aborta sem regravar o banco. Migrações anteriores seguem sua cadeia e suas verificações.

As expectativas de versão nos testes antigos foram atualizadas para 6. A correção de substituição atômica transitória no Windows pertence à integração, com evidência e limites descritos no [relatório próprio](2026-11-01-integracao-main.md); não é atribuída ao processamento de variantes.

## UI e arquivos

O painel usa `variantSpeed`, `variantPitch`, `variantReverse`, `variantPreview`, `variantStop`, `variantCreate`, `variantCapabilities` e `variantMessage`. As coleções oferecem `btnCollectionShuffle`, membros com `data-shuffle-target`, campo `cooldown_ms` e botões de repetição com `data-repeat-pad-id`.

Criar variante atualiza as opções de coleção automaticamente. Abrir o editor de coleções também atualiza a biblioteca. O refresh usa o modelo local vigente, preserva texto digitado antes de blur e o alvo selecionado; respostas antigas não substituem a atualização mais nova.

| Área | Arquivos do lote |
|---|---|
| Processamento/proveniência | Novo `audio_variants.py`; `clip_renderer.py`, `audio_engine.py`, `editor_service.py` |
| API/persistência | `main.py`, `store.py` |
| Grupos curados/operação | `sound_collections.py`, `operation_service.py` |
| Consumidores | `sound_sequences.py`, `sequence_service.py`, `packs.py` |
| Interface | Novo `ui/variants.js`; `ui/editor.js`, `ui/collections.js`, `ui/collections.css`, `ui/index.html`; integração pontual em `ui/app.js` |
| Testes novos | `tests/test_audio_variants.py`, `tests/test_shuffle_groups.py`, `tests/test_variants_ui.test.cjs` |
| Testes ampliados | `tests/test_collections.test.cjs`, `tests/test_editor_state.test.cjs` |
| Expectativa de schema | `tests/test_collections.py`, `tests/test_edit_persistence.py`, `tests/test_sound_sequences.py`, `tests/test_voice_profiles.py` |

Esse inventário não atribui ao lote as alterações preexistentes E0–E9 nem as mudanças do usuário em `main`. O implementador não realizou staging/commit; o coordenador realizou a integração registrada em `4deb17d`.

## TDD e verificação

| Ciclo | RED observado | GREEN observado |
|---|---|---|
| Núcleo shuffle/variantes | 7 falhas: saco/alvo curado/API/render/mapas ausentes | 33 Python com editor e política de disparo |
| UI inicial | 5 falhas: módulo, gesto de repetição e cursor de variante ausentes | 22 JavaScript direcionados |
| Integração de áudio | 4 falhas: intervalo E8, FFmpeg configurado, prévia sem monitor e identidade do pack | 8 testes de variantes antes de ampliar cancelamento/schema |
| Schema e cancelamento antecipado | 3 falhas: schema 5 e API sem token/stop por token | 14 Python de variantes/shuffle |
| Concorrência frontend | 2 falhas: token de prévia ausente e duas criações simultâneas | 4 JavaScript de variantes |
| WebView sem randomUUID | Falha reproduzida sem chamada ao bridge | Teste com fallback passou; smoke nativo posterior passou |
| Variante → coleção | 3 falhas: opção ausente, texto digitado perdido e resposta antiga | 10 testes de coleção; 33 JavaScript com editor/draft/variantes |

Antes da transferência para C:, a regressão direcionada do implementador passou com **185 Python em 22,86 s** e **24 JavaScript**. Esses números são parciais históricos, não o gate final integrado.

Gate integrado confirmado pelo coordenador e registrado nos arquivos da worktree:

- **660 Python PASS em 17,13 s**, com harness bloqueando streams/play/rec nativos e PyAudio: `.worktrees/integration-audio/final.log`.
- **71 JavaScript PASS** e checagem de sintaxe de todos os módulos UI: `.worktrees/integration-audio/node-final.log`.
- Diffcheck do delta integrado limpo. Na etapa anterior em D:, existia aviso de whitespace preexistente em `tests/test_e_mixer.py:432`; não foi tratado como defeito novo E10.A.
- Backend com FFmpeg real, sinais sintéticos, engine/store/mixer reais e streams simulados: **PASS**, `.worktrees/e10-parent-mo8xam2g`.
- WebView2 real em 768 px: **PASS**, `.worktrees/e10-parent-x95cqwwq`, com capturas `variant.png` e `shuffle.png`. Cobriu criar variante, Stop durante render pendente, opção recém-criada na coleção, shuffle/repetição e ensaio monitor-only.
- Revisão de requisitos Sol: **SPEC_PASS**, 70 Python + 27 JavaScript direcionados. Revisão Astra de qualidade E10.A ainda pendente neste registro.

Os gates finais acima foram executados pelo coordenador. Este relatório não declara uma repetição adicional desses gates pelo implementador durante a escrita documental.

### Correções da revisão Astra

Astra confirmou dois P2 no reprodutor independente `.worktrees/e10a-astra-review/test_review_edges.py`:

1. Em velocidade 2x, seleção de uma amostra `[0, 1]` arredondava para zero amostras, mas a API retornava sucesso e o renderer interpretava lista vazia como ausência de recorte, reproduzindo a montagem inteira.
2. Ao criar variante de uma revisão anterior, o texto do clipe atual era copiado sem reprojeção. Áudio e âncoras pertenciam aos spans antigos, enquanto `child.transcript` e o pack podiam descrever os spans atuais. Texto antigo de segmentos também podia sobrescrever correção humana no manifesto.

Foram acrescentados três testes em `tests/test_audio_variants.py`: rejeição na API sem chamar áudio, rejeição direta no renderer e coerência entre revisão anterior/filho/projeção/pack com correção humana e evidência vocal preservadas. **RED: 3 falhas / 11 passes. GREEN direcionado: 118 Python em 5,42 s**, abrangendo variantes, editor, packs, modelo temporal e correções vocais. Delta de produto: `main.py`, `audio_engine.py`, `audio_variants.py` e `packs.py`. Sem execução de full, staging ou commit nesta correção. Aguardando re-review e o gate posterior coordenado.

## Incidente de isolamento e correção

Durante a implementação inicial, a prévia mudou de `play_clip` para `prepare_group` para permitir cancelamento antes da publicação. Um teste novo continuava mockando somente `play_clip`, deixando o caminho de preparo nativo acessível. O pytest em Windows terminou com **access violation**, exit 1, durante uma execução direcionada. Essa execução falhou e não é evidência de aprovação.

O problema foi uma lacuna de isolamento do fixture. Não é correto afirmar que todas as tentativas anteriores nunca puderam alcançar hardware; o teste deixou essa possibilidade aberta. O incidente não comprova reprodução audível nem qualidade em driver real. O aplicativo pessoal não foi reiniciado.

Correções: fixture `no_native_audio` automático em `tests/test_audio_variants.py` substitui `DeviceMixer.ensure_stream`; o caso sem monitor configura explicitamente monitor ausente e proíbe chegar a `prepare_group`; os testes de cancelamento usam mixers simulados. O gate final do coordenador adiciona bloqueio amplo dos acessos nativos de áudio/PyAudio. Os testes direcionados corrigidos e o gate integrado posterior passaram.

Não foram usados biblioteca, modelos, vozes ou capturas pessoais como dados de validação. A evidência válida distingue processamento real de sinais sintéticos, lógica de mixer com streams simulados e interação da WebView. Não há aceite acústico em hardware, medição de latência física, validação em aplicativo de chamada ou avaliação por corpus neste lote.

## Insights e pendências

- Um efeito precisa fazer parte da revisão consumida por todos os renderizadores: ajustar só a reprodução deixa waveform, loudness, sequência e exportação inconsistentes.
- Cancelamento precisa de identidade conhecida antes do processamento. Parar apenas após receber `play_id` permite início tardio de uma prévia já fechada.
- A referência temporal da edição e a referência da saída são distintas; reverso exige declarar que o texto continua descrevendo a origem.
- Atualizar a biblioteca não autoriza substituir o draft da coleção. A resposta remota e o estado local têm ciclos diferentes.
- Quando o caminho de áudio muda, os doubles dos testes precisam acompanhar a nova fronteira. O bloqueio nativo no harness reduz o risco de uma chamada esquecida alcançar dispositivo real.

Encerramento: Sol re-review **SPEC_PASS**, 71 focados; Astra re-review **QUALITY_PASS**, 88 focados incluindo os dois repros independentes. Coordenador: **663 Python em 15,82 s** após as correções, 71 JS aprovados sem delta JavaScript posterior, diffcheck limpo; smoke ampliado `.worktrees/e10-parent-4hlly9_h` PASS. E10A concluída. O lote B e a avaliação acústica com hardware/corpus não estão entregues por este relatório.
