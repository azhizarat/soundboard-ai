# Editor: seleção, reprodução e IA contextual

## Comportamento entregue

- Arraste comum seleciona áudio. A alça branca move a agulha independentemente; Ctrl+arraste também funciona, inclusive perto dos marcadores de junção. As alças de início/fim e Alt+arraste continuam disponíveis.
- Um Play substitui os três modos expostos anteriormente. Inicia na agulha dentro da seleção; fora dela ou no limite final, inicia no começo. Espaço usa a mesma operação, sem texto de atalho dentro do botão.
- O backend configura posição e repetição antes de publicar a sessão ao mixer. Não há áudio inicial indevido seguido de um seek assíncrono. A repetição conserva toda a seleção, mesmo quando a primeira passagem começa no meio.
- Transcrição, título/categoria e vozes têm ações, andamento, cancelamento, propostas e resultados no próprio local. Não há aba separada de IA nem de efeitos. As variantes continuam disponíveis numa seção recolhida em Título e aparência.
- Configuração de modelo e transcrição permanece global. Preservam-se as proteções contra respostas de outro clipe, pedidos antigos e substituição de alterações humanas.

## Validação em 2026-11-02

- Python: **764 passaram**, suíte completa usando `.worktrees/startup-probe/run_pytest.py -q --basetemp=.worktrees/pytest-contextual-final`, com dispositivos reais bloqueados.
- JavaScript: **125 passaram**, `node --test tests/*.test.cjs`.
- `git diff --check`: sem erros.
- WebView2 nativo em larguras de **1280 e 768 px**, biblioteca isolada e workers sintéticos controlados: arraste comum preserva agulha; alça preserva seleção; sessão inicia na amostra solicitada; transcrição de seleção permanece na aba e atualiza palavras; proposta de título preserva o campo humano até o aceite e então atualiza título/categoria; identificação atualiza pessoa; cancelamento aparece no próprio local. Sem erros JavaScript e sem sessões de áudio restantes.
- Uma seleção parcial de palavra foi recusada pelo contrato existente; selecionar a palavra inteira pelo texto permitiu aplicar a transcrição. Não foi removida essa proteção para fazer o teste passar.

Harness local: `.worktrees/contextual-ai-smoke.py`. Evidências locais: `.worktrees/e10-parent-ydqk2jor` (1280 px), `.worktrees/e10-parent-wdm_knl8` (768 px). A validação usa API, persistência e fila reais com inferência e saída de áudio simuladas; não mede qualidade do reconhecimento nem audibilidade no hardware do usuário.

## Regras de UX confirmadas

Seleção e agulha são operações independentes. O gesto comum deve selecionar; mover a agulha não pode apagar a seleção. Uma ação de IA deve permitir acompanhar e conferir seu resultado no mesmo local. Recursos opcionais não precisam de uma aba exclusiva, mas continuam acessíveis. Não expor parâmetros globais do modelo em cada edição.
