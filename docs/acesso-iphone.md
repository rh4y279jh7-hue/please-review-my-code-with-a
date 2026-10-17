# Acessar o HiveMind pelo iPhone

Este guia mostra o caminho mais fácil para usar o HiveMind a partir de um iPhone.

## O que você precisa entender primeiro

O HiveMind é um **serviço de backend** (Python + FastAPI). O iOS **não roda** esse
tipo de serviço em segundo plano e não há app do HiveMind na App Store. Portanto o
iPhone é usado como **cliente**: ele envia tarefas e lê respostas de um HiveMind que
roda em **outro lugar** (um computador seu ou um servidor na nuvem).

Resumo dos papéis:

- **Host** (um PC/Mac em casa, ou uma VM na nuvem): roda o serviço com Docker.
- **iPhone**: acessa o serviço pelo Safari ou pelo app **Atalhos (Shortcuts)**.

## Caminho 1 — PC/Mac em casa, iPhone no mesmo Wi-Fi (mais simples e grátis)

No computador que vai hospedar:

1. Instale o Docker Desktop: <https://www.docker.com/products/docker-desktop/>.
2. Na pasta do projeto, rode:

   ```bash
   ./scripts/start.sh --lan
   ```

   O script cria o `.env` (se faltar), **gera um token de acesso** automaticamente e
   sobe o serviço aceitando conexões da rede local.
3. Descubra o IP local do computador:
   - macOS: `ipconfig getifaddr en0`
   - Linux: `hostname -I | awk '{print $1}'`
   - Windows: `ipconfig` (procure "Endereço IPv4")

No iPhone (mesmo Wi-Fi):

4. Abra o **Safari** e acesse `http://IP-DO-COMPUTADOR:8000/docs`.
5. Como o acesso em rede exige token, use o botão **Authorize** na página `/docs` e
   informe o token gerado (está no arquivo `.env`, na linha `HIVEMIND_API_TOKEN`).

> Dica: mantenha o computador ligado enquanto quiser usar pelo iPhone.

## Caminho 2 — Nuvem (acessa de qualquer lugar; tem custo)

1. Crie uma VM pequena num provedor de nuvem.
2. Instale o Docker e suba o HiveMind com um proxy reverso HTTPS na frente
   (ex.: Caddy, Nginx ou Traefik) e com `HIVEMIND_API_TOKEN` configurado.
3. No iPhone, acesse `https://SEU-DOMINIO/docs` e autentique com o token.

> Nunca exponha a porta 8000 diretamente na internet sem HTTPS e token.

## Enviar tarefas com o app Atalhos (Shortcuts) do iOS

Você pode criar um botão "Enviar tarefa ao HiveMind":

1. Abra o app **Atalhos** e toque em **+** para criar um atalho novo.
2. Adicione a ação **Pedir Entrada** (tipo: Texto, pergunta: "Qual a tarefa?").
3. Adicione a ação **Obter conteúdo de URL** e configure:
   - **URL**: `http://IP-DO-COMPUTADOR:8000/tasks` (ou `https://SEU-DOMINIO/tasks`)
   - **Método**: `POST`
   - **Cabeçalhos**:
     - `Content-Type`: `application/json`
     - `Authorization`: `Bearer SEU_TOKEN` (troque `SEU_TOKEN` pelo valor de
       `HIVEMIND_API_TOKEN` do arquivo `.env`, mantendo a palavra `Bearer` e o espaço)
   - **Corpo da solicitação**: `JSON`
     - `task` (Texto): use a variável da ação "Pedir Entrada"
     - `cycles` (Número): `2`
4. Adicione **Mostrar Resultado** para ver a resposta inicial (que inclui o `id`).

Para ver o resultado final, crie um segundo atalho que faça um `GET` em
`http://IP-DO-COMPUTADOR:8000/tasks/ID_DA_TAREFA` com o mesmo cabeçalho
`Authorization`.

## Comandos úteis (no host)

```bash
# Subir só no loopback do próprio computador (modo demo, sem token):
./scripts/start.sh

# Subir aceitando a rede local (gera token automaticamente):
./scripts/start.sh --lan

# Parar o serviço:
docker compose down
# ATENÇÃO: "docker compose down -v" apaga a memória persistente (volume).
```

## Modo real (Claude, GPT, Gemini)

1. Edite o `.env` e defina `HIVEMIND_DEMO_MODE=false`.
2. Garanta um `HIVEMIND_API_TOKEN` com pelo menos 32 caracteres (o `--lan` já gera um).
3. Configure ao menos uma chave de provedor: `HIVEMIND_ANTHROPIC_API_KEY`,
   `HIVEMIND_OPENAI_API_KEY` ou `HIVEMIND_GOOGLE_API_KEY`.
4. Reinicie o serviço. Custos, limites e políticas de cada provedor se aplicam.

## Limites e segurança

- O token é compartilhado: quem o tiver acessa todas as tarefas e a mesma memória.
  Não há usuários, RBAC nem isolamento entre pessoas.
- Nenhum agente executa comandos, controla aplicativos ou acessa o seu celular.
- Respostas podem conter erros ou instruções maliciosas; revise antes de usar em
  decisões sensíveis e não envie segredos ou dados pessoais desnecessários.
