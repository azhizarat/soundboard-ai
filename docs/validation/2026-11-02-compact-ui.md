# Interface compacta com acesso direto

## Direção confirmada pelo usuário

Preservar funcionalidades e atalhos importantes visíveis. As novas opções
devem ser concisas, sem depender de menus adicionais para as ações principais.
O problema observado nas capturas de janela normal e fullscreen era a soma
de barras altas, avisos e informação técnica acima dos sons.

## Alterações

- Cabeçalho e barras de preparação/operação mais baixos, sem remover seus
  controles. Pastas detectadas continuam com importação em um clique, junto
  das outras entradas de importação.
- Fila acessível na barra de operação, abrindo sobre a biblioteca sem reduzir
  sua altura. Fechar e Escape devolvem foco ao acionador. Abertura por trabalho
  novo e respeito ao recolhimento manual continuam funcionando.
- Status de saída no rodapé, com conteúdo completo no tooltip e erros
  destacados. Reprodução mantém pausa, reinício, loop, parada, seek e volume.
- Cards com títulos/transcrições mais legíveis e ações de ouvir, favoritar e
  editar sempre com contraste. Cores das pessoas/categorias preservadas.
- Na fila, nome do arquivo em destaque; caminho completo no tooltip e em
  “Histórico e origem”, permitindo copiar o caminho.

## Evidência e limites

WebView2 real, com 88 clipes sintéticos, duas pastas detectadas e uma sequência
salva. Hardware de áudio e modelos pessoais isolados. As imagens não usam a
biblioteca do usuário.

| Janela | Viewport | Altura útil da biblioteca |
|---|---|---:|
| Antes: 1280 × 820 | 1264 × 781 | 382,61 px |
| Depois: 1280 × 820 | 1264 × 781 | 509 px |
| Depois: 1920 × 1080 | 1904 × 1041 | 802 px |
| Depois: 768 × 900 | 752 × 861 | 592,45 px |

A comparação 1280 usa a mesma fixture e o commit anterior `8f78d73`: ganho de
126,39 px, aproximadamente 33%. Não é comparação entre bibliotecas diferentes.

- [Antes, janela normal](2026-11-02-compact/before-1280.png)
- [Depois, janela normal](2026-11-02-compact/library-1280.png)
- [Depois, 1920](2026-11-02-compact/library-1920.png)
- [Depois, 768](2026-11-02-compact/library-768.png)
- [Fila aberta](2026-11-02-compact/imports.png)

Verificações desta revisão:

- **80 testes JavaScript passaram**: `node --test tests/*.test.cjs`.
- Oito telas exercitadas nas três larguras: biblioteca, importação com sucesso
  e falha, editor, IA, coleções, sequências, perfil vocal e modo Tocar.
- Os 18 controles principais permanecem dentro da viewport. Na janela de 768,
  os centros também foram conferidos contra `elementFromPoint`, sem outro
  elemento cobrindo o alvo.
- Clique nativo abre a fila; fechar e Escape recolhem e devolvem o foco.
  A altura da biblioteca não muda ao abrir a fila.
- Na janela estreita, conferidos os botões dos cards nas três densidades e
  os controles do rodapé em estado sintético de reprodução. Isto verifica
  layout; não constitui teste de reprodução em hardware real.
- Sem erros JavaScript ou transbordamento horizontal nos smokes.
- Nenhum ID existente removido ou duplicado; `git diff --check` passou.
- Backend inalterado; a suíte Python não foi repetida nesta revisão visual.

Harness local: `.worktrees/studio-visual-smoke.py`, com `STUDIO_ASSERT=1`,
`SOUNDBOARD_SMOKE_WIDTH` e `SOUNDBOARD_SMOKE_HEIGHT`. O harness usa as APIs
reais com diretórios sintéticos e bloqueio de hardware.

## Regras para próximas alterações

Não confundir consistência de CSS com boa distribuição de espaço. Validar
sempre biblioteca densa, pastas detectadas e estado de reprodução. Compactar
e agrupar antes de ocultar funcionalidades; preservar ações importantes com
o mesmo número de cliques. Medir espaço útil com a mesma fixture e viewport.
