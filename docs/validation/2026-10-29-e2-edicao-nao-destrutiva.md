# E2 — fundação de edição não destrutiva

## Contrato e integração

- `clip_edits.py`: referência de 48.000 amostras/s, intervalos inteiros ordenados e não sobrepostos `[start, end)`, mapa saída → fonte com rejeição de limites inválidos.
- `clip_renderer.py`: render compartilhado aplica intervalos, microfades limitados a 240 amostras e à metade de cada trecho (sem remover duração), ganho da revisão e fator de normalização congelado. O volume do botão/ocorrência é aplicado depois, seguido pelo volume de rota no mixer. Revisões ignoram os antigos campos de trim/ganho durante render.
- Fontes promovidas ficam em `data/clips/sources`, endereçadas pelo conteúdo PCM canônico. O WAV disponível é a fonte: silêncio/contexto já removido pela captura anterior não é recuperável. A conversão usa `audio_processing.resample_audio` e guarda hash original, hash preservado, versão, taxa, duração e contagem de amostras.
- A cada render o hash é verificado. Uma alteração externa não modifica silenciosamente uma revisão. A identidade de derivação inclui a revisão inteira e a versão de render; esta etapa não mantém cache de áudio derivado.
- `begin_edit` promove por cópia antes da publicação e cria draft persistido; `update_edit` salva alterações sem publicar; `commit_edit` valida/renderiza fora do lock, congela nova revisão e publica atomicamente; `discard_edit` remove o draft. Rascunho de replay promovido e descartado também sai dos pendentes.
- Editor existente: abrir promove; input/change são serializados; prévia usa draft sem commit; fechar preserva; seletor **Edições preservadas** permite retomar; **Descartar alterações** é explícito. Replay capturado e ainda não aberto no editor permanece efêmero, com limpeza de startup original.
- `get_clip_waveform` representa o resultado de revisão; `get_edit_source_waveform` fornece explicitamente a régua da fonte para os controles legados de trim. Ambos usam o mesmo renderizador para fontes/revisões. Playback monitor usa draft quando disponível; playback público usa revisão publicada. Loudness de revisão usa o resultado renderizado.
- Uma abertura tardia não substitui outra edição aberta mais recentemente; um salvamento tardio não fecha o editor de outro clipe. Os controles visuais de múltiplos trechos/transcrição temporal continuam para E4, com `spans` já aceito no backend de drafts.

## Persistência e referências

- Schema 0 → 1 → 2 com backup obrigatório antes de gravar a migração. Schema futuro falha sem regravar o banco. Schema 2 conserva todas as coleções/atributos, inclusive revisões sem clipe e fontes indisponíveis. Texto/pessoa históricos começam como `unknown`; apenas valores alterados pelo editor recebem proveniência humana.
- Fontes de clipes históricos disponíveis são preservadas na migração; mídia ausente permanece registrada como indisponível. Clipes legados sem revisão continuam compatíveis; edição explícita passa ao contrato novo. Capturas/importações novas sem revisão são promovidas quando entram no editor.
- Falhas de publicação restauram somente o clipe/draft/revisão afetados sob lock. Nunca há rollback global descartando gravações concorrentes. A promoção por cópia deixa o arquivo anterior disponível se a gravação falhar; um arquivo promovido órfão pode permanecer conservadoramente no disco.
- Identidade de clipe explícita nunca deduplica por caminho. Restore aceita fonte compartilhada. Chamadores legados sem ID mantêm deduplicação por caminho.
- `source_references` considera clipes, todas as revisões preservadas, drafts, referências duráveis nomeadas (p.ex. `voice-sample:...`/sequências) e handles ativos. `acquire_source`/`release_source` são idempotentes na liberação; `collect_source` recusa fontes referenciadas e só remove arquivos sob o diretório de fontes.
- Playback retém antes do start e libera em falha/retorno vazio/conclusão; sessões reais também liberam no stop, inclusive stop interno para trocar de clipe. Não há coleta automática de revisões antigas nesta etapa: retenção conservadora evita destruir fontes necessárias a undo/futuros consumidores.

## Pacotes

- Clipes editados exportam WAV float renderizado com ganhos baked, trim integral e `volume=1`. Texto temporal é projetado a partir dos segmentos existentes quando presentes; não se inventam palavras/tempos.
- O marcador aditivo `rendered` permite ao importador fixar revisão neutra (sem renormalizar/refazer fades), mantendo o áudio do roundtrip. Exportações legadas sem revisão preservam o arquivo intacto e os campos antigos de trim.
- Identidade de importação inclui hash da mídia + metadados/edição do manifesto; mesmo pacote reimportado é idempotente, enquanto mesma mídia com dois trims/metadados distintos conserva dois clipes. Routing e referências vocais existentes não são substituídos.
- Revisão Sol: o manifesto renderizado carrega `montage_identity` (SHA-256 de fonte, receita e identidade da ocorrência, sem caminhos). Ela sobrevive ao bake/import/export neutro, distingue montagens diferentes com PCM e metadados idênticos e é renovada após novo commit de edição. Campo malformado/tamanho inválido é rejeitado antes da importação. Pacotes legados sem o campo mantêm a regra anterior de hash + metadados.
- Revisão Sol: a UI só inclui trim após interação explícita com slider, campo numérico ou alça da waveform (`editTrimDirty`). Arredondamento automático do range ao abrir, como 1/3 s → 0,33 s, não representa edição: título, autosave, prévia e save omitem os campos de corte. O backend mantém precisão por amostra e aceita cortes explícitos, sem tolerâncias que descartem alterações finas. `update_edit` também conserva spans quando limites explícitos forem iguais aos anteriores.

## Correções de qualidade Astra

- O commit sincroniza `speaker_id`/`speaker_ids` e cria a pessoa selecionada na mesma transação da revisão. Falha de persistência restaura clipe/draft/revisão e remove somente pessoas criadas nessa transação, preservando registros anteriores.
- Enrollment de clipe revisado usa somente os spans mantidos, com ganho neutro, normalização neutra e sem fades. Um WAV temporário alimenta o contrato legado do analisador e é removido em sucesso/falha; a fonte permanece retida por handle até o fim. Clipes legados continuam usando trim em segundos. Sem modelos/corpus/perfis novos.
- Ao iniciar a edição de clipe sem revisão, os bounds antigos em segundos são adaptados uma vez à contagem real da fonte. Exemplo: duração/trim legado 1,34 s para 64.128 amostras vira 1,336 s. Revisões/spans e novos cortes explícitos não usam essa adaptação: excesso continua erro e cortes de uma amostra são preservados.

## Evidências e limites

- TDD registrado por execuções RED → GREEN: contratos ausentes; merge por caminho; schema/future; draft/purge/restart/rollback; API/waveform; pack com duas montagens; renormalização duplicada no roundtrip; loudness fora da revisão; referência ativa; corrupção de fonte endereçada pelo conteúdo; save direto de revisão; descarte do replay promovido; fila UI e abertura obsoleta.
- Testes de compatibilidade ajustados: `test_c_persistence.py` passa a esperar schema 2 (duas asserções de cenário); `test_f1_backend.py` aceita restore com ID distinto e fonte compartilhada; `test_f2_packs.py` preserva entradas com os mesmos bytes e metadados distintos. Os comportamentos anteriores contrariavam os contratos E2.
- Todos os áudios de teste são sintéticos em `tmp_path`/`.worktrees`. Sem corpus pessoal, rede, modelos ou reprodução em hardware. Verificação UI é automatizada via Node e sintaxe; nenhuma validação visual/manual ou alegação de áudio físico foi feita.
