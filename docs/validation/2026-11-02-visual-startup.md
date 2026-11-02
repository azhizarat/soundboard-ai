# Correção de abertura e acabamento visual

## Resultado

O carregamento antecipado de SciPy foi removido do caminho de importação da
aplicação. Mediana de cinco processos: 1,0149 s antes e 0,2945 s depois.
O método e as limitações estão em [startup](2026-11-02-startup.md): esta é
uma medição de `import main`, não do tempo total até a janela estar utilizável.

O CSS compartilhado agora cobre os campos e botões dos módulos novos. O
cabeçalho reúne roteamento, replay, pacote, parada e overlay; importação,
sequências e rascunhos ficam na barra de preparação. O editor prioriza texto
e áudio, com controles secundários em seções recolhíveis. Foram preservados
os IDs e os handlers existentes.

A fila começa recolhida quando está vazia ou contém só histórico. Trabalho
novo, nova tentativa ou pedido de intervalo abrem a fila; polling de progresso
respeita o recolhimento manual. Cancelar o seletor não abre uma fila vazia.

## Evidência visual

Capturas do WebView2 real com biblioteca e áudio sintéticos, sem abrir
dispositivos de áudio ou carregar modelos pessoais. Janela de 1280 × 900:
viewport de 1264 × 861. Janela de 768 × 900: viewport de 752 × 861.

| Verificação inicial | Antes, 1280 | Depois, 1280 | Depois, 768 |
|---|---:|---:|---:|
| Altura útil da biblioteca | 279,20 px | 498,61 px | 533,70 px |
| Transbordamento horizontal da página | Sim | Não | Não |
| Fila vazia aberta | Sim | Não | Não |

Foram exercitados e capturados biblioteca, importações, editor, análise local,
coleções, sequências, perfil vocal e modo Tocar nas duas larguras, sem erros
JavaScript. O smoke usa a API e os controladores reais, com dispositivos
substituídos. A biblioteca do usuário não foi usada na avaliação visual.

- [Biblioteca antes](2026-11-02-ui/before-library-1280.png)
- [Biblioteca depois](2026-11-02-ui/library-1280.png)
- [Editor depois](2026-11-02-ui/editor-1280.png)
- [Biblioteca em janela menor](2026-11-02-ui/library-768.png)

## Verificação integrada

- Python: **729 passaram em 17,65 s**, com hardware bloqueado e diretórios
  isolados. Comando do harness local: `python .worktrees/startup-probe/run_pytest.py
  -q -x -p no:cacheprovider --basetemp=.worktrees/pytest-final-validation`.
- JavaScript: **80 passaram**, `node --test tests/*.test.cjs`.
- Smoke nativo: oito telas em cada largura; assertions de área útil,
  transbordamento e fila inicial passaram. Harness local:
  `.worktrees/studio-visual-smoke.py`, `STUDIO_ASSERT=1` e
  `SOUNDBOARD_SMOKE_WIDTH=1280` ou `768`.
- `git diff --check`: passou.

A primeira execução Python não conseguiu criar os diretórios temporários do
pytest no TEMP do sandbox (574 erros de setup). A causa foi reproduzida com
um teste isolado; definir TEMP/TMP graváveis e `--basetemp` dentro do checkout
permitiu executar toda a suíte. Não foi necessário alterar código por isso.

## Regras confirmadas para próximas alterações

Não antecipar imports de processamento pesado apenas para mostrar a interface.
Validar novos controles no WebView, incluindo janela estreita, além dos testes
de comportamento. Painéis de histórico devem preservar espaço da biblioteca;
atualizações assíncronas não devem desfazer recolhimento escolhido pelo usuário.
