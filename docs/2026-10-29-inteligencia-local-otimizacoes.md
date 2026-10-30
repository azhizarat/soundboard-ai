# Inteligência local e otimizações — oportunidades priorizadas

Plano de execução consolidado: [etapas, prioridades, dependências e validação](superpowers/plans/2026-10-29-evolucao-consolidada.md).

Data: 29/10/2026. Avaliação complementar à [proposta do editor](superpowers/specs/2026-10-29-editor-transcricao-design.md). São recomendações, não mudanças implementadas.

**Prioridade atualizada pelo usuário:** atribuições erradas e repetidas de pessoas exigem uma frente específica de [reconhecimento de vozes com aprendizado pelas correções](superpowers/specs/2026-10-29-vozes-aprendizado-local-design.md). A proposta detalha referências por trecho, múltiplas amostras, tratamento de dúvida e reversão de aprendizado incorreto.

## Direção

O maior ganho provável está em melhorar a entrada do reconhecimento, aproveitar contexto, preservar correções e evitar processamento repetido. Um modelo maior é uma opção a comparar, não a primeira solução para todos os problemas.

| Prioridade | Melhoria | Resultado esperado | Evidência |
|---|---|---|---|
| 1 | Reamostragem adequada | Não introduzir frequências falsas antes da IA | Aliasing reproduzido com sinal sintético na função atual |
| 1 | Reanálise respeitando edição e correções | Texto e título descrevem o clipe que será ouvido | Hoje a IA recebe o arquivo inteiro e substitui metadados |
| 1 | Estados explícitos de análise | Falta de reconhecimento não vira categoria incorreta | Falha e ausência de fala podem devolver o mesmo texto vazio |
| 2 | Reconhecimento com contexto e tempos por palavra | Menos correções e seleção textual do áudio | Recursos disponíveis na biblioteca instalada; ganhos de qualidade ainda não medidos |
| 2 | Cache por etapa e processamento seletivo | Não refazer tarefas independentes da alteração | Cache de áudio já existe; não há cache equivalente de resultados no pipeline inspecionado |
| 1 | Referências por trecho, aprendizado pelas correções e identidades desconhecidas separadas | Menos atribuições incorretas de interlocutor | Correção comum não atualiza referências; cadastro usa arquivo inteiro; IDs locais podem ser confundidos com pessoas |
| 3 | Preferências e correções locais reutilizáveis | Personalização de nomes, gírias e categorias | Proposta; não é aprendizado implementado |
| 3 | Perfil de recursos para uso durante jogos | Priorizar reprodução e limitar concorrência da IA | Proposta a medir no hardware real |

## 1. Corrigir a preparação do áudio antes de trocar o modelo

`load_audio_mono_16k` em `ai_analyzer.py:374` reduz a taxa por interpolação linear (`np.interp`). Não existe uma etapa explícita de filtragem antialiasing nesse caminho. O carregador de reprodução faz conversão semelhante em `audio_engine.py:381`.

Foi executado um experimento isolado com a função extraída do AST do arquivo atual. A leitura de `soundfile` foi substituída por sinais sintéticos em memória; o corpo de conversão foi mantido. Não houve leitura de gravações pessoais, carregamento de modelos ou alteração do código.

| Entrada, 1 segundo a 48 kHz | Saída a 16 kHz: componente dominante | RMS da saída |
|---|---:|---:|
| Seno de 1 kHz, amplitude 1 | 1.000 Hz | 0,7061 |
| Seno de 12 kHz, amplitude 1 | 3.999 Hz | 0,5774 |

O segundo resultado demonstra energia acima do limite de 8 kHz reaparecendo em uma frequência inferior. Não demonstra uma taxa específica de erro em fala. A correção proposta é padronizar a conversão com um reamostrador filtrado, como o já acessível via FFmpeg ou uma implementação polifásica validada. A [documentação oficial de resample_poly](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html) descreve filtragem passa-baixas antes da redução da taxa.

Critério: preservar frequência/amplitude dentro da banda útil, atenuar componentes fora dela, manter duração e mapeamento temporal. Comparar transcrições reais somente depois dessa correção.

## 2. Fazer a IA analisar o conteúdo que o usuário realmente manteve

`reanalyze_clip_ai` (`main.py:806`) envia o arquivo completo para análise. O trim é aplicado ao cálculo de loudness, mas não à transcrição/metadados. O resultado sobrescreve título, categoria, tags e transcrição.

Isso permite que uma fala já cortada continue influenciando o texto e o título. Também torna a reanálise arriscada para correções manuais já realizadas.

Proposta:

- Separar **transcrição da origem** de **transcrição do resultado editado**.
- Derivar o texto final dos trechos preservados e de seus tempos, sem necessariamente executar reconhecimento novamente a cada corte.
- Quando uma nova análise for necessária, usar o trecho pertinente com contexto controlado.
- Separar ações: retranscrever fala, rever interlocutores e sugerir título/categoria. Pedir um novo título não deve executar reconhecimento de voz novamente.
- Registrar, por campo, origem automática ou correção manual e revisão da análise. Apresentar propostas em vez de substituir silenciosamente campos corrigidos.
- Invalidar resultados tardios de trabalhos iniciados antes de uma edição mais recente.

## 3. Reconhecimento de fala com contexto, vocabulário e revisão localizada

Hoje o modelo é `base`, fixo (`ai_analyzer.py:516`). A transcrição usa português, `beam_size=3`, sem VAD e sem preservar tempos (`ai_analyzer.py:589`). Há transcrição por turno de interlocutor; quando vários segmentos pertencem a uma única pessoa, o caminho de agregação pode ainda executar uma transcrição integral adicional (`ai_analyzer.py:765`).

A versão instalada expõe `word_timestamps`, `hotwords`, `initial_prompt`, `vad_filter` e `condition_on_previous_text`. A [documentação oficial do faster-whisper](https://github.com/SYSTRAN/faster-whisper#usage) também documenta tempos por palavra e VAD. A disponibilidade da opção não garante qualidade neste tipo de gravação.

Proposta de avaliação:

1. Comparar o pipeline atual com transcrição de janelas de fala com contexto, associando interlocutores aos tempos posteriormente.
2. Preservar palavras e segmentos temporais; alimentar o editor com esses dados.
3. Usar vocabulário curto e editável de nomes, apelidos, jogos e gírias. Evitar prompts enormes e substituições automáticas que inventem palavras esperadas.
4. Comparar modelos/configurações em um pequeno corpus representativo de português, ruído de jogo e fala sobreposta. Escolher padrão por resultado e orçamento de recursos.
5. Oferecer refinamento de seleção problemática. Uma segunda passagem pode melhorar o texto, mas não deve ser tratada como correção garantida.

VAD identifica regiões de fala para orientar o reconhecimento. Não deve apagar risadas, efeitos ou outras partes do áudio original. Para cortes precisos, começar pelos tempos disponíveis e avaliar alinhamento adicional se o erro nas bordas justificar a complexidade. O [WhisperX](https://github.com/m-bain/whisperX#limitations) documenta limitações de alinhamento e fala sobreposta: sua adoção não elimina esses problemas automaticamente.

## 4. Não confundir falha de IA com ausência de conteúdo

`transcribe` retorna texto vazio quando o modelo está indisponível ou ocorre uma exceção (`ai_analyzer.py:589`). O classificador pode tratar texto vazio como Vinhetas (`ai_analyzer.py:328`, `ai_analyzer.py:350`). O chamador não recebe motivo suficiente para distinguir esses casos.

Além disso, a consulta opcional ao Ollama tem timeout de dois segundos e fallback silencioso (`ai_analyzer.py:243`). Não foi reproduzida uma falha por timeout; o tempo de carregamento e geração precisa ser medido. A interface não deveria apresentar resultado de regras locais como se tivesse vindo do modelo opcional.

Proposta: resultados estruturados com sucesso, ausência de fala, falha de decodificação, modelo ausente, cancelamento e falha de inferência. Mostrar origem e estado da análise em linguagem simples: “transcrição indisponível”, “título sugerido por regras”, “analisando”. Permitir repetir só a etapa que falhou. Logs devem registrar tempo/erro/modelo, sem despejar áudio ou transcrição pessoal por padrão.

## 5. Personalização local útil e verificável

Em vez de prometer que o app aprende automaticamente com tudo, usar três mecanismos controlados:

- **Vocabulário pessoal:** correções frequentes podem ser oferecidas para inclusão no dicionário de reconhecimento, com contexto e confirmação. Uma palavra corrigida uma vez não vira substituição universal.
- **Preferências de biblioteca:** usar exemplos confirmados pelo usuário para sugerir categorias, tags e estilo de título. Manter alternativa “sem classificação”.
- **Perfis de voz:** guardar múltiplas amostras confirmadas e seus indicadores de qualidade, permitindo remover uma amostra ruim.

O cadastro atual mistura a referência anterior e a nova com pesos iguais (`ai_analyzer.py:581`). A amostra mais recente passa a ter forte influência, independentemente do número e qualidade dos exemplos anteriores. Uma referência limpa e várias referências representativas devem ser comparadas antes de atualizar o perfil.

Treinar/fine-tunar um modelo inteiro fica fora da primeira rodada. Dicionário, perfis e exemplos locais já oferecem personalização auditável.

## 6. Interlocutores: desconhecido é um resultado válido

Em `diarize_and_identify` (`ai_analyzer.py:669`), os IDs de novas vozes derivam das referências cadastradas. Sem uma nova referência persistida, capturas diferentes podem reutilizar a mesma numeração para pessoas diferentes. Há também fallback para `interlocutor_1` (`ai_analyzer.py:693`). Isso não prova identidade entre gravações.

`analyze_file` atribui `speaker_similarity=1.0` quando já há um ID (`ai_analyzer.py:759`). Esse valor não representa uma similaridade efetivamente medida para todos esses casos e não deve virar uma confiança exibida ao usuário.

Proposta:

- “Voz A nesta captura” é uma identidade local, distinta de uma pessoa cadastrada.
- Promover uma voz para identidade global apenas por confirmação ou correspondência avaliada com critérios explícitos.
- Usar amostras suficientes e evitar consolidar perfis a partir de voz sobreposta/ruidosa.
- Preservar evidência da correspondência e permitir corrigir a atribuição sem executar todas as outras análises.

## 7. Evitar trabalho repetido e competição com o jogo

O motor já tem cache LRU de áudio decodificado, com orçamento de 256 MiB e buffers imutáveis (`audio_engine.py:240`, `audio_engine.py:364`). Os objetos dos modelos também são reutilizados na instância do analisador. Não faz sentido propor essas otimizações como se não existissem.

Oportunidades adicionais:

| Alteração | Trabalho necessário | Trabalho a preservar |
|---|---|---|
| Corrigir título/categoria | Persistir metadados | Transcrição, embeddings, decodificação |
| Corrigir interlocutor | Atualizar associação e projeções | Texto e áudio reconhecidos |
| Alterar corte | Recalcular montagem, duração e loudness; projetar texto | Análise da origem ainda válida |
| Alterar modelo de fala | Nova transcrição/alinhamento | Áudio decodificado e identificação independente |
| Alterar uma referência de voz | Rever correspondências afetadas | Transcrição e features acústicas independentes |

Cachear resultados pela identidade do conteúdo, versão do modelo, parâmetros e intervalo analisado. A chave deve incluir a versão das referências para resultados de identidade. Distinguir embeddings reutilizáveis de decisões de correspondência que ficaram desatualizadas.

Propor fila limitada, prioridade para o clipe em edição e cancelamento de trabalho obsoleto. Em perfil “jogando”, limitar paralelismo e oferecer pausa das análises pesadas. Em perfil “revisando”, permitir maior orçamento. Mais tarefas simultâneas podem piorar latência, memória e frame time; não assumir que paralelizar tudo é uma otimização.

Duas oportunidades menores, a medir antes de alterar:

- `ReplayRingBuffer.snapshot` concatena todo o buffer antes de selecionar a janela final (`audio_engine.py:224`); selecionar apenas os blocos necessários pode reduzir cópia e tempo sob lock.
- O LRU mantém pelo menos uma entrada, mesmo acima do orçamento (`audio_engine.py:425`). Arquivos longos exigem limite por item, leitura por janela ou estratégia de streaming. Não é motivo para trocar o cache dos clipes curtos que já funciona.

## 8. Classificação mais honesta e busca mais útil

O caminho determinístico combina palavras-chave e características como energia e frequência fundamental (`ai_analyzer.py:163`, `ai_analyzer.py:316`). Rótulos como Rage ou Risada são heurísticas. Volume de entrada, efeitos e música podem influenciá-los; não representam leitura confiável de emoção.

Evolução proposta:

- Separar propriedades acústicas (“alto”, “curto”, “com pausas”) de categorias editoriais (“rage”, “meme”).
- Categorias sugeridas são editáveis e podem ficar indefinidas.
- Gerar títulos a partir do trecho final, com preferência por uma frase realmente reconhecida, e limitar invenção do LLM.
- Mais adiante, oferecer busca por significado sobre transcrições e tags, junto da busca literal existente: por exemplo, encontrar uma reclamação sem lembrar a frase exata. Avaliar com exemplos reais antes de adicionar embeddings e outro modelo.

## Ordem de execução recomendada

1. Resolver preparação do áudio, estados de erro, reanálise que desconsidera cortes/correções e cadastro de voz que ignora o trecho selecionado.
2. Implementar a base temporal necessária ao editor e comparar reconhecimento com contexto e configurações de qualidade.
3. Implementar referências vocais rastreáveis e aprendizado pelas correções confirmadas, com validação de confusões entre pessoas.
4. Acrescentar cache por etapa, invalidação correta, fila com prioridade do usuário, dicionário pessoal e sugestões de metadados.
5. Avaliar busca semântica e classificadores adicionais apenas depois de medir o que continua custando trabalho manual.

Métricas: tempo até primeira prévia, tempo por etapa, picos de RAM/VRAM, impacto em reprodução/jogo, erros de transcrição, precisão temporal, atribuições erradas de pessoas e ações manuais até salvar o clipe. Ganhos percentuais dependem dessas medições; não foram estimados nesta avaliação.

## Estado de validação e insights

- Confirmado no código: reamostragem por interpolação, reanálise integral com substituição de campos, fallback de texto vazio, regras de classificação, cadastro de voz por média, cache existente e APIs da biblioteca instalada.
- Confirmado por execução isolada: aliasing na conversão 48 → 16 kHz para sinal sintético.
- Não validado: melhora de reconhecimento com outro modelo, velocidade de GPU, qualidade das identidades, ganho de cache ou impacto durante gameplay.
- Nenhuma alteração de código de aplicação, download de modelo, processamento de gravação pessoal ou teste completo da suíte foi realizado.
- Insight principal: reduzir correções depende de fidelidade do áudio e do vínculo entre origem, edição e resultados da IA. Adicionar modelos sem resolver esses contratos pode aumentar custo e inconsistência.
