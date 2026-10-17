#!/usr/bin/env bash
# Inicializador do HiveMind. Prepara o arquivo .env (se faltar) e sobe o serviço
# com Docker Compose. Uso:
#   ./scripts/start.sh            # modo demo local (sem chaves), só no loopback
#   ./scripts/start.sh --lan      # também aceita acesso pela rede local (exige token)
#
# Este script nunca grava segredos no repositório: as chaves ficam apenas no .env,
# que é ignorado pelo Git.
set -euo pipefail

cd "$(dirname "$0")/.."

LAN=false
for arg in "$@"; do
  case "$arg" in
    --lan) LAN=true ;;
    -h | --help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *)
      echo "Opção desconhecida: $arg" >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker não encontrado. Instale o Docker Desktop: https://www.docker.com/" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "O plugin 'docker compose' não está disponível. Atualize o Docker." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Criado .env a partir de .env.example (modo demo, sem chaves)."
fi

COMPOSE_FILES=(-f docker-compose.yml)

if [ "$LAN" = true ]; then
  COMPOSE_FILES+=(-f docker-compose.lan.yml)
  # Acesso pela rede local exige autenticação: gera um token se ainda não houver.
  if ! grep -qE '^HIVEMIND_API_TOKEN=.+' .env; then
    TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    # Remove qualquer linha de token (comentada ou vazia) e grava a nova.
    grep -vE '^#?\s*HIVEMIND_API_TOKEN=' .env >.env.tmp && mv .env.tmp .env
    printf 'HIVEMIND_API_TOKEN=%s\n' "$TOKEN" >>.env
    echo "Gerado HIVEMIND_API_TOKEN e salvo no .env (necessário para acesso em rede)."
    echo "Guarde este token; ele é exigido no header Authorization: ******"
  fi
  echo "Atenção: o acesso pela rede local publica a porta 8000 em todas as interfaces."
  echo "Use apenas em rede confiável. Para internet, use HTTPS e, de preferência, uma VPN."
fi

exec docker compose "${COMPOSE_FILES[@]}" up --build
