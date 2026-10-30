# Revisão Astra — contratos para E2/E3/E4/E7/E8

Revisão estática durante E1; nenhum áudio pessoal analisado. Localizações referem-se ao estado lido nesta revisão e podem mudar conforme os lotes.

## Decisões incorporadas à implementação futura

1. **Identidade:** ID de clipe explícito prevalece sobre caminho. Duas montagens podem compartilhar `source_id`; restauração não rejeita esse compartilhamento. Remoção de mídia depende de referências persistentes e execuções ativas.
2. **Drafts:** dados recuperáveis ficam fora de `tmp`. Publicar referências só após promover mídia; limpeza exclui apenas órfãos após persistência confirmada. Não enfraquecer a limpeza dos temporários antigos.
3. **Migração:** executar v0→v1→v2 explicitamente; nunca usar a constante da versão mais nova para rotular uma migração intermediária. Backup obrigatório, schema futuro rejeitado sem gravação e todas as entidades restauradas. Mídia ausente permanece como registro indisponível, não é apagada da biblioteca na migração.
4. **Áudio canônico:** origem identificada por hash, número de amostras e versão da conversão. Renderização determina spans, rampas, ganho/normalização congelados e, depois, ajustes locais da ocorrência e volume da rota. Não normalizar ou aplicar ganho duas vezes. Revisão fixada não muda por preferência global posterior.
5. **Packs:** fonte é deduplicada por conteúdo; clipe considera edição/metadados. Dois trims diferentes sobre os mesmos bytes sobrevivem ao import. Export renderizado zera só os ganhos já incorporados; round-trip não amplifica duas vezes.
6. **Conclusão:** registrar identidade/ownership antes de publicar voz no mixer. Evento único com motivo, fora dos locks. Não adaptar callback por tentativa repetida após `TypeError`, que pode vir do corpo do handler.
7. **Rotas:** execução congela destinos e geração das rotas. Preparação de grupo verifica todas as saídas obrigatórias. Falha de saída obrigatória encerra grupo uma vez, sem continuar pelo monitor como se transmissão tivesse sucedido. Reconexão não retoma vozes antigas.
8. **Concorrência textual:** aplicar análise sob lock com geração do pedido/draft e versão de campo, além da revisão de áudio. Job antigo não vence novo job sobre a mesma revisão.
9. **Referencial de ocorrências:** ajustes locais de trim de uma ocorrência de fluxo usam o resultado da revisão fixada. O renderer resolve o mapa para a origem.
10. **Bordas textuais:** palavra cuja âncora intersecta um intervalo mantido pode ser mostrada como parcialmente cortada, sem fabricar outro alinhamento. Texto resultante inclui a palavra uma única vez se houver áudio correspondente; limites manuais do áudio permanecem exatos. Uma âncora de bloco com lacuna preserva seus intervalos, sem tornar removido audível novamente.
11. **Limite de origem do replay:** nesta rodada, origem significa o WAV entregue pelo pipeline atual de captura, que já pode ter aparado silêncio nas bordas. Não recuperar ou prometer contexto perdido; preservar o snapshot integral de captura é evolução separada. Edição posterior nunca destrói essa origem disponível.

## Evidências principais

- `store.py`: merge por caminho em `add_or_update_clip`, exclusão sem alcançabilidade, sweep geral de `tmp`, restauração de campos conhecidos e migração intermediária usando versão corrente.
- `main.py`: restauração rejeita caminho compartilhado; publicação move arquivo antes de salvar; pack deduplica bytes antes da montagem.
- `packs.py`: export legado copia bytes e trim/volume, exigindo contrato distinto para render editado.
- `mixer.py`/`audio_engine.py`: voz publicada antes da sessão ser registrada; callback sob lock com fallback de aridade; stream morto e troca de dispositivo precisam encerrar ownership.

## Gates adicionais

Testar explicitamente: IDs diferentes com mesma origem; restauração compartilhada; purge/restart; falha entre promoção e persistência; v0/v1/v2/futuro; pack com dois trims da mesma fonte; ganho único; completion durante start; handler que lança TypeError; cancelamento tardio; perda de cabo com monitor funcionando; troca de dispositivo; jobs concorrentes sobre mesma revisão.

São critérios incorporados às etapas futuras, não correções já implementadas.
