"""Interface de linha de comando para executar a simulação offline do HiveMind."""

import argparse
import asyncio
import logging

from hivemind.config import Settings
from hivemind.core.swarm import SwarmOrchestrator
from hivemind.logging import configure_logging
from hivemind.memory import KnowledgeGraph

_LOGGER = logging.getLogger("hivemind.cli")


def main(arguments: list[str] | None = None) -> None:
    """Interpreta comandos e apresenta métricas sem expor conteúdo sensível."""
    parser = argparse.ArgumentParser(prog="hivemind")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="executa a simulação offline")
    run.add_argument(
        "--cycles", type=int, default=5, help="número de gerações (1..100)"
    )
    run.add_argument(
        "--task",
        default="Como um formigueiro digital pode compartilhar conhecimento?",
        help="pergunta que inicia a exploração",
    )
    options = parser.parse_args(arguments)
    if not 1 <= options.cycles <= 100:
        parser.error("--cycles deve estar entre 1 e 100")
    configure_logging()
    settings = Settings()
    engine = SwarmOrchestrator(KnowledgeGraph(settings.graph_path))
    result = asyncio.run(engine.run(options.task, options.cycles))
    for index, metrics in enumerate(result["cycles"], start=1):
        print(
            f"Geração {index}: nós={metrics['nodes_created']}, "
            f"trilhas reforçadas={metrics['trails_reinforced']}, "
            f"tarefas geradas={metrics['tasks_generated']}, "
            f"tarefas consumidas={metrics['tasks_consumed']}"
        )
        _LOGGER.info(
            "geracao_concluida ciclos=%d nos=%d trilhas=%d tarefas_geradas=%d "
            "tarefas_consumidas=%d",
            index,
            metrics["nodes_created"],
            metrics["trails_reinforced"],
            metrics["tasks_generated"],
            metrics["tasks_consumed"],
        )
    print(f"Métricas finais: {result['metrics']}")


if __name__ == "__main__":
    main()
