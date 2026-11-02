# Sincronização da agulha — 2026-11-02

## Causa e correção

O editor redesenhava a agulha somente após consultas ao motor, separadas por 80 ms mais o tempo do bridge. Ao encerrar, a sessão saía do motor antes da consulta seguinte; a UI recebia apenas `playing=false` e descartava a posição final. Isso era especialmente visível em memes de poucas centenas de milissegundos.

A UI agora interpola a posição em `requestAnimationFrame`, com novas âncoras do motor a cada 60 ms. A extrapolação é limitada a 250 ms quando o bridge demora. Pausa, loop, seek e cancelamento mantêm suas semânticas. Erro transitório de consulta preserva o ID da prévia e tenta novamente. Arrastar para trás usa uma identidade de intenção separada da geração usada para rejeitar polls atrasados: concluir um seek anterior não invalida o último gesto.

A API fornece posição/duração sem arredondamento de centésimos, em segundos e amostras. Um cache de até 256 metadados preserva o motivo e a posição terminal por ID; não retém PCM nem sessões. Conclusão natural leva a agulha ao limite final da prévia, inclusive quando o áudio acaba antes da resposta inicial. Parada/falha não são tratadas como conclusão natural. O mapeamento continua respeitando montagem, lacunas, velocidade e reverso.

## Validação

- 744 testes Python passaram com acesso ao hardware de áudio bloqueado.
- 114 testes JavaScript passaram. Cobertura adicional: frames intermediários, fim anterior ao primeiro poll/retorno, pausa, loop, extrapolação limitada, Stop All, erro transitório e seeks pendentes para trás. Este último reproduziu RED (agulha presa em 1,5 s) antes de GREEN (0,516 s após seek para 0,5 s e frame de 16 ms).
- WebView2 com mixer real avançado por blocos PCM sintéticos a 48 kHz, sem dispositivo físico: seis prévias de 137/2400/4800/19200/4800/2400 amostras (aprox. 3–400 ms) atingiram exatamente seus limites finais. Trechos de 100 ms tiveram 16 posições distintas observadas, e o de 400 ms teve 52.
- Sete voltas de loop observadas. Stop All congelou a agulha numa posição intermediária, sem deslocá-la ao fim.
- Evidência local: `.worktrees/e10-parent-u_l174ho/playhead-result.json`. Isso valida relógio/estado/UI e não mede a latência acústica de um dispositivo físico.

## Regra para próximas alterações

Nunca inferir conclusão pela ausência de sessão. Guardar estado terminal leve, separar a intenção do usuário da geração de consulta, e não usar frequência do bridge como frequência de desenho da interface.
