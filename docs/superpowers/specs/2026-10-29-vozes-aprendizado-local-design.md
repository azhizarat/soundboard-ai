# Reconhecimento de vozes com aprendizado pelas correções

Plano de execução consolidado: [etapas, prioridades, dependências e validação](../plans/2026-10-29-evolucao-consolidada.md).

Data: 29/10/2026. Status: proposta complementar; implementação não iniciada.

## Objetivo confirmado

O usuário relata que precisa reatribuir repetidamente clipes às pessoas corretas porque o reconhecimento de voz erra com frequência. Quer melhorar a aproximação de vozes e considerar aprendizado contínuo.

Esta frente passa a ser prioritária, junto da qualidade da transcrição e do editor. O objetivo é reduzir atribuições erradas e trabalho de correção sem esconder a dificuldade por meio de abstenção excessiva.

## Diagnóstico do comportamento atual

| Evidência | Consequência |
|---|---|
| `main.py:660`, `save_clip_edits`, persiste a pessoa escolhida sem cadastrar exemplo vocal. | Corrigir o interlocutor de um clipe não melhora diretamente as referências usadas na próxima identificação. |
| `main.py:783`, `enroll_speaker_from_clip`, entrega apenas o caminho do arquivo ao cadastrador. | O cadastro explícito usa o arquivo completo, sem aplicar trim ou seleção de uma fala. Pode incorporar outras vozes e partes já descartadas no editor. |
| `ai_analyzer.py:581` mistura a referência anterior e o novo embedding com pesos iguais, normalizando o resultado. | Um exemplo recente ruim pode deslocar muito o perfil; quantidade, diversidade e qualidade dos exemplos anteriores não estão representadas. |
| `ai_analyzer.py:545` usa a melhor similaridade acima de um limiar, sem exigir distância da segunda candidata nesse caminho. | Duas pessoas com pontuações próximas ainda podem produzir uma atribuição automática. |
| `ai_analyzer.py:669` cria IDs de vozes desconhecidas a partir da numeração das referências; há fallback para `interlocutor_1` em `:693`. | Uma identidade local não deve ser interpretada automaticamente como a mesma pessoa em outras capturas. |
| `ui/app.js:2255` escolhe `interlocutor_1` se não houver pessoa selecionada ao cadastrar voz. | O cadastro pode ir para uma pessoa padrão sem uma escolha explícita adequada. |
| `main.py:797` atualiza o interlocutor do clipe mesmo quando o cadastrador devolve falha. | “Pessoa corrigida” e “referência aprendida” precisam de estados separados na interface. |

São constatações do código, não uma medição dos erros nas gravações do usuário. Nenhum áudio pessoal foi analisado nesta etapa.

## Abordagem recomendada

Implementar **aprendizado incremental supervisionado das referências e da decisão de correspondência**. A cada correção confirmada, o app obtém um exemplo rotulado que pode melhorar o perfil daquela pessoa. O modelo que extrai embeddings permanece inicialmente fixo.

Isso aproveita a arquitetura existente sem exigir retreinar uma rede neural após cada ação. A separação entre extração de embeddings e comparação de similaridade é descrita na [documentação de reconhecimento de pessoas do NeMo](https://docs.nvidia.com/nemo/speech/nightly/asr/speaker_recognition/intro.html). O [exemplo oficial do sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/blob/master/java-api-examples/SpeakerIdentification.java) também demonstra cadastro com múltiplas gravações por pessoa.

Os detalhes de seleção de amostras e calibração abaixo são propostas para este produto, não garantias de resultado fornecidas por essas bibliotecas.

## Fluxo de correção

1. O usuário troca a pessoa atribuída e salva. A atribuição manual é preservada contra reanálises automáticas posteriores.
2. Com a preferência visível **Aprender com minhas correções** habilitada, a correção gera um candidato a referência apenas para o intervalo de fala correspondente.
3. O app distingue atribuição de clipe inteiro de atribuição de segmento. Uma captura com várias vozes exige selecionar a fala da pessoa para cadastrar uma amostra; não deve incorporar toda a mistura ao perfil.
4. Amostras elegíveis entram no perfil em segundo plano. Exibir um resultado discreto: “Pessoa corrigida; exemplo adicionado” ou “Pessoa corrigida; este trecho não foi usado como referência”, com motivo acessível.
5. Se o usuário desfizer ou alterar novamente a correção, a referência derivada é retirada ou reatribuída e as decisões dependentes são invalidadas.

A preferência não converte automaticamente a biblioteca antiga em material confirmado. Rótulos históricos podem ter vindo do reconhecedor e conter justamente os erros relatados. O cadastro automático considera somente ações manuais explicitamente rastreadas como confirmação/correção.

## Amostras e perfis

- Preservar amostras individuais com pessoa, origem, intervalos, ação de confirmação, versão do extrator, embedding e indicadores de qualidade.
- Usar somente os trechos selecionados ou mantidos da voz confirmada. Não extrair referência de trechos removidos na montagem.
- Avaliar quantidade de fala útil, silêncio, saturação e sinais de mistura/sobreposição. Esses indicadores também podem falhar; casos duvidosos ficam pendentes de revisão.
- Separar variação legítima de gravação ruim: fala normal, risada, grito e mudança de microfone podem exigir referências representativas diferentes. Não rejeitar um exemplo apenas porque é diferente do centro atual.
- Deduplicar por origem e intervalo. Repetir a mesma correção ou cadastrar várias cópias da mesma fala não pode aumentar artificialmente o peso dessa voz.
- Manter um banco limitado e representativo por pessoa. Avaliar agregação robusta e múltiplos protótipos; escolher a estratégia com dados de validação. Os limiares e o limite do banco serão parâmetros calibrados e registrados, não números apresentados como universais.
- Não permitir que uma pessoa com mais exemplos ganhe vantagem apenas pela quantidade de comparações. A política de pontuação deve ser validada com bancos de tamanhos diferentes.
- Exibir as amostras no perfil da pessoa: ouvir, excluir, corrigir identidade e ver se foram usadas no perfil atual.

O armazenamento de amostras e embeddings é local. Excluir uma referência precisa desfazer sua influência nas agregações. Excluir um clipe deve indicar se uma amostra confirmada será mantida separadamente; não deixar referências ocultas ou quebradas.

## Decisão de identidade

Separar três resultados:

- **Identificada:** correspondência suficiente com uma pessoa cadastrada e margem adequada sobre alternativas.
- **Duvidosa:** candidatas próximas; mostrar opções curtas de confirmação e acesso à prévia da fala.
- **Desconhecida:** evidência insuficiente para uma pessoa cadastrada; usar identidade local à captura.

Pontuação de similaridade não é probabilidade calibrada. Não exibir “95% de certeza” a partir de um cosseno arbitrário.

Uma correção “era B, não A” fornece um exemplo positivo confirmado para B e evidencia uma confusão com A. Registrar esse caso para calibração e diagnóstico, sem punir indiscriminadamente todo o perfil de A nem alterar outras atribuições manuais.

Nunca adicionar previsões automáticas ao banco confirmado apenas por serem pontuadas como fortes. Isso poderia realimentar os próprios erros. Exemplos sugeridos automaticamente podem ser apresentados para confirmação, mantendo origem distinta.

## Revisão do acervo existente

Oferecer revisão opcional de clipes com atribuições automáticas após atualizar um perfil. Mostrar sugestões antes de aplicar mudanças em lote; preservar pessoas atribuídas manualmente.

Migrar as referências atuais como **referências legadas**, sem fingir que existem múltiplas amostras ou avaliações de qualidade que não foram armazenadas. Manter backup e permitir reconstruir o perfil com exemplos confirmados. Não sobrescrever o arquivo legado durante a avaliação.

A revisão pode agrupar erros recorrentes entre duas pessoas e facilitar ouvir/confirmar vários trechos, mas uma aprovação em lote deve indicar exatamente quais intervalos serão usados como referências. Não é autorização para usar a captura inteira.

## Desempenho e reversibilidade

- Reutilizar embeddings da mesma origem, intervalo e versão do modelo.
- Uma correção de pessoa não dispara nova transcrição.
- Recalcular apenas o perfil afetado; invalidar correspondências dependentes da versão desse perfil.
- Persistir referências e suas revisões de forma atômica. Se aprendizado falhar, manter a correção manual e informar a falha de cadastro separadamente.
- Executar o trabalho fora do caminho de reprodução. Disponibilizar cancelamento e reversão.
- Manter histórico suficiente para localizar a amostra que introduziu uma regressão.

## Treinamento do modelo: possibilidade posterior

Retreinar ou adaptar o extrator neural só deve ser considerado depois de corrigir seleção de áudio, banco de referências e decisão de identidade, e reunir exemplos confirmados suficientes para uma avaliação independente.

A alternativa inicial é comparar um extrator compatível com outro, usando exatamente os mesmos dados separados por captura/sessão. A aprovação de um novo modelo exige melhoria nos casos difíceis sem regressão relevante nos demais, orçamento de recursos aceitável e migração versionada dos embeddings. Embeddings de modelos diferentes não são comparados diretamente.

## Validação e critérios de aceite

1. Corrigir pessoa gera referência elegível somente da fala selecionada, com origem manual rastreável.
2. Uma captura com duas pessoas não cadastra o áudio misturado como se fosse uma só.
3. Corrigir, desfazer e recadastrar a mesma fala não duplica peso nem deixa influência residual.
4. Um modelo ausente ou falha de extração não desfaz a correção manual; o app informa que não aprendeu a referência.
5. Uma nova pessoa ou disputa entre candidatas pode permanecer sem identificação automática.
6. Uma reanálise não altera identidades confirmadas manualmente.
7. Adicionar exemplo ruim pode ser revertido sem perder os outros exemplos da pessoa.
8. Configuração, referências e versões sobrevivem ao reinício; exclusões são consistentes.
9. A avaliação separa capturas/sessões entre cadastro, calibração e teste. Trechos sobrepostos da mesma gravação não ficam em lados diferentes, evitando uma melhora artificial.
10. Medir atribuições erradas, precisão das atribuições automáticas, cobertura automática, casos duvidosos/desconhecidos, correções por pessoa e latência. Mostrar matriz de confusão para encontrar pares de vozes problemáticos.

“Errou menos” só conta como melhoria se vier acompanhado da cobertura e do esforço manual. Um reconhecedor que passa a responder desconhecido para tudo não resolve o problema.

## Ordem proposta

1. Fazer cadastro respeitar seleção/trim, remover pessoa padrão implícita e distinguir correção de aprendizado.
2. Introduzir amostras rastreáveis e reversíveis, mantendo compatibilidade com referências legadas.
3. Alimentar perfis pelas correções confirmadas e melhorar decisão com margem/abstenção.
4. Calibrar e avaliar com casos reais separados por captura; revisar pares de vozes confundidos.
5. Comparar outros extratores ou treinamento adicional apenas se a avaliação justificar.

## Insights registrados

A dificuldade relatada não demonstra, por si só, falta de treinamento do modelo. O fluxo atual não reaproveita a correção comum e o cadastro explícito pode analisar áudio fora do corte. Corrigir esses dois contratos é pré-requisito para qualquer aprendizado contínuo confiável.

Não foram implementadas mudanças nem alteradas referências de voz nesta etapa.
