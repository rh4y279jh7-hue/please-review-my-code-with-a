#!/usr/bin/env bash
# Inicializador do HiveMind. Prepara o arquivo .env (se faltar) e sobe o serviço
# com Docker Compose. Uso:
#   ./scripts/start.sh            # demo local autenticado, só no loopback
#   ./scripts/start.sh --lan      # também aceita acesso pela rede local (exige token)
#
# Este script nunca grava segredos no repositório: as chaves ficam apenas no .env,
# que é ignorado pelo Git.
set -euo pipefail
umask 077

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

if [ -L .env ] || { [ -e .env ] && [ ! -f .env ]; }; then
  echo "O arquivo .env deve ser regular e não pode ser um link simbólico." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Criado .env a partir de .env.example (modo demo, sem chaves)."
fi

chmod 600 .env

COMPOSE_FILES=(-f docker-compose.yml)

if ! grep -qE '^HIVEMIND_API_TOKEN=.+' .env; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  ENV_TMP="$(mktemp .env.XXXXXX)"
  trap 'rm -f -- "$ENV_TMP"' EXIT
  # Preserva o restante do arquivo e remove somente definições antigas do token.
  grep -vE '^[[:space:]]*#?[[:space:]]*HIVEMIND_API_TOKEN=' .env >"$ENV_TMP" || [ "$?" -eq 1 ]
  printf 'HIVEMIND_API_TOKEN=%s\n' "$TOKEN" >>"$ENV_TMP"
  mv -- "$ENV_TMP" .env
  trap - EXIT
  unset TOKEN ENV_TMP
  echo "Gerado HIVEMIND_API_TOKEN e salvo no .env (obrigatório para toda API)."
  echo "Use esse token no header Authorization: Bearer; o valor não é exibido."
fi

if [ "$LAN" = true ]; then
  COMPOSE_FILES+=(-f docker-compose.lan.yml)
  echo "Atenção: o acesso pela rede local publica a porta 8000 em todas as interfaces."
  echo "Use apenas em rede confiável. Para internet, use HTTPS e, de preferência, uma VPN."
fi

exec docker compose "${COMPOSE_FILES[@]}" up --build
