# Política de segurança

## Versões suportadas

Só a branch `main` recebe correções de segurança. O projeto não publica versões.

## Como reportar uma vulnerabilidade

Não abra issue, pull request nem discussão pública sobre a falha. Use o relato
privado de vulnerabilidades do GitHub, na aba **Security** deste repositório.

Descreva arquivo, linha e como reproduzir. Nunca inclua o valor de
`HIVEMIND_API_TOKEN`, de chaves de provedores (`HIVEMIND_*_API_KEY`) nem o
conteúdo de um `.env`: mascare os valores.

## Credencial exposta

Se um token ou chave vazar (commit, log, issue ou conversa com uma IA):

1. Revogue a credencial no provedor e gere outra.
2. Atualize o `.env` local com a nova credencial.
3. Só depois limpe o que for preciso. Reescrever o histórico da `main` é decisão
   do dono do repositório.
