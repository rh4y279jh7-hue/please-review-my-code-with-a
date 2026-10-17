# Time NORTE Ω: 8 agentes especialistas

Base: o `AGENTS.md` (protocolo NORTE Ω). Cada agente herda o AGENTS.md e acrescenta só o próprio papel.
Princípio: AUTOR ≠ REVISOR ≠ DECISOR.

| Agente | Papel | Edita? |
|---|---|---|
| arquiteto | plano pronto para decisão | não |
| implementador | executa o plano com diff mínimo | sim |
| cacador_de_bugs | reproduz → causa raiz → correção | sim |
| revisor_red_team | revisão hostil com arquivo:linha | não |
| guardiao_seguranca | segredos, injeção, supply chain | não |
| verificador | tenta refutar o "pronto" | não (só roda checagens) |
| pesquisador_de_fatos | fatos que mudam, com fonte oficial | não |
| condutor_de_pr | branch, PR, CI, merge autorizado | sim, com pedido |

## Instalação
Neste repositório já estão instalados em `.codex/agents/` e `.claude/agents/`. Para usar em outro lugar:
- **Codex (projeto):** copie `.codex/agents/*.toml` para `<repo>/.codex/agents/`.
- **Codex (todos os projetos):** copie para `~/.codex/agents/`.
- **Claude Code (projeto):** copie `.claude/agents/*.md` para `<repo>/.claude/agents/`.
- **Claude Code (todos os projetos):** copie para `~/.claude/agents/`.
- **Coordenação:** neste repositório já está no §14 do `AGENTS.md`. Em outro projeto, copie essa seção para o AGENTS.md dele.

## Limites conhecidos (verificados na documentação em 2026-10-17)
- Codex não tem "somente leitura" por agente: os campos `sandbox_mode`/`approval_policy` no arquivo do agente são ignorados; o subagente herda o sandbox da sessão. A restrição de não editar está nas instruções.
- Claude Code: os agentes de leitura bloqueiam `Edit, Write, NotebookEdit`, mas ainda têm `Bash` (para rodar testes), que tecnicamente pode escrever.
- Os prompts citam caminhos do HiveMind (`src/hivemind`, `uv run`, `.antigravity/`). Em outro repositório funcionam pelo §13 do AGENTS.md local, mas os exemplos ficam específicos.
- Arquivos de instrução orientam o modelo; não garantem obediência.
