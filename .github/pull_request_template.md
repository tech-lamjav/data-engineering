<!--
Se este PR fecha uma issue do MESMO repo, a PRIMEIRA linha do corpo (antes de qualquer
outro texto) deve ser "Closes #<N>", bare — sem o prefixo DE#/AE# usado em conversa e sem
formatá-la como link markdown. É a ÚNICA sintaxe que o GitHub reconhece para fechar a
issue automaticamente ao mergear; "Closes DE#92" e "[DE#92](url)" NÃO fecham (já
aconteceu: PR #154 no analytics-engineering, PR #99 aqui).

Se a issue for de OUTRO repo, use o path completo: "Closes owner/repo#N".

Depois de mergear, confirme com `gh issue view N --json state` — se continuar OPEN, feche
à mão com `gh issue close N` e registre o link do PR num comentário.

Pode repetir a referência como link legível (ex. [DE#92](url)) no corpo do PR — isso não
substitui a linha "Closes #N" acima, só complementa.
-->

Closes #

## Por que a mudança

## Resumo da mudança
