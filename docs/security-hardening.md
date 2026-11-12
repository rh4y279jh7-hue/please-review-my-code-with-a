# Reforço de segurança do HiveMind

Estado: mudanças propostas neste PR; aplicação na `main`, implantação e controles
administrativos da plataforma precisam de evidências separadas. Este documento
não comprova proteção absoluta nem equivalência a sistemas governamentais.

## Escopo das mudanças

- Autenticação: a API passa a exigir token, inclusive em modo demo. O modo demo
  continua funcionando sem chaves dos provedores; o cliente precisa autenticar.
- Memória: respostas geradas entram em quarentena; a recuperação padrão considera
  somente registros aprovados, com origem e validade registradas. A promoção é
  uma decisão local explícita, sem endpoint público de aprovação automática.
- Chamadas a provedores: a cota acumulada por processo começa em zero; liberar
  chamadas reais exige configuração deliberada. A cota limita chamadas, não mede
  reais gastos em dinheiro nem substitui o orçamento do provedor.
- CI: o Integrity fixa as mesmas Actions do Quality por SHA completo e mantém
  `contents: read`, timeout, checkout sem credenciais persistentes e execução da
  versão do guardião que está na base do PR.
- Governança: mudanças em `.github/`, `.codex/`, `.claude/`, documentos de
  política e testes de blindagem recebem achado sensível, mesmo quando novas.
  Renomear um arquivo para fora do caminho protegido também recebe achado.
- Cabeçalhos: remover linhas de controle CSP ou HSTS passa pela detecção do
  guardião. Isso é uma detecção conservadora de diff, não uma prova de segurança
  de toda política de cabeçalhos.

As partes anteriores só devem ser descritas como verificadas junto com os
resultados observados no SHA do PR. Os itens R1–R12 da diretriz original conservam
seu registro histórico; este arquivo não os promove a executados na plataforma.

## Portão da plataforma ainda pendente

Arquivos no repositório não tornam os checks obrigatórios. A integração disponível
nesta tarefa não fornece acesso administrativo para aplicar o ruleset de `main`.
O responsável pela plataforma precisa executar e reler a configuração antes de
afirmar que o portão está ativo.

Configuração pretendida, restrita a este repositório:

1. Exigir PR para `main`, bloquear deleção e force-push.
2. Exigir os checks `quality` e `integridade`, atualizados com a base, e resolução
   das conversas. Verificar os nomes efetivos antes de cadastrar a regra.
3. Não conceder bypass a bots ou agentes. Decidir antes a rota do Antigravity:
   ele sincroniza `.antigravity/` e o novo ruleset pode bloquear o push direto.
   Este PR preserva esses dados e não altera a sincronização.
4. Com dono único, não exigir aprovação de uma segunda identidade inexistente.
   Se houver revisor humano independente designado, a contagem e a exigência de
   code owner podem ser elevadas com validação específica.

Aceitação: a leitura administrativa mostra a regra ativa e uma tentativa de
incorporar PR com check pendente ou falhando é recusada. A validação não deve
fazer merge de conteúdo de teste nem alterar a `main`.

Risco: nomes incorretos de check, exigência de aprovação impossível ou interrupção
da sincronização podem bloquear o desenvolvimento. Reversão: o responsável
administrativo restaura a configuração anterior registrada; agentes não criam
exceções para contornar o bloqueio.

## Revisão e liberação do PR

Este PR altera o guardião e o workflow de integridade. A versão da base deve
continuar apontando esses arquivos como sensíveis. Esse resultado é esperado e
não deve ser apagado por rerun, commit vazio ou alteração do gate. A decisão
manual prevista na diretriz pertence ao dono; agentes não aplicam o rótulo de
liberação e não fazem auto-merge.

Aceitação do código: testes de criação/alteração/exclusão de caminhos protegidos,
renomeação e remoção de cabeçalhos passam; todas as Actions ficam pinadas; a
suíte anterior continua passando, com as mudanças intencionais de contrato
documentadas. Auth, memória e cota precisam de seus próprios testes offline.

Reversão do código: PR de reversão dos commits desta entrega. Registros legados
da memória não devem ser apagados nem promovidos automaticamente para facilitar
o rollback. Não reverter proteções de forma automática.

## Limites restantes

- A aprovação da memória não prova verdade factual; requer curadoria com fontes.
- Uma API com token único continua sendo de um único operador. Ela não oferece
  usuários, papéis ou isolamento entre clientes que compartilhem esse token.
- O estado em execução, custos reais, retenção dos provedores, transporte TLS e
  recuperação de backups dependem da implantação e não foram validados por um PR.
- A cota por processo reinicia com o processo; cota persistente financeira e
  isolamento multiusuário exigem desenho e validação próprios.
- Merge, implantação, configurações de conta, credenciais e operação do iMac
  não fazem parte desta entrega de código.
