import argparse
import asyncio
from types import SimpleNamespace

from fast_agent import FastAgent
from fast_agent.types import PromptMessageExtended


def _get_default_agent_name(app) -> str:
    for name, agent in app._agents.items():  # noqa: SLF001
        config = getattr(agent, "config", None)
        if config and getattr(config, "default", False):
            return name
    return next(iter(app._agents.keys()))


async def handle_message(
    app,
    base_agent_name: str,
    chat_id: str,
    history: list[PromptMessageExtended],
    message: str,
) -> str:
    base = app[base_agent_name]
    clone = await base.spawn_detached_instance(name=f"{base.name}[{chat_id}]")
    clone.load_message_history(history)
    response = await clone.send(message)
    history[:] = clone.message_history
    return response


async def main() -> None:
    parser = argparse.ArgumentParser(description="FastAgent clone-per-chat example")
    parser.add_argument(
        "--message",
        default="ping",
        help="Message to send via a cloned agent (one-shot).",
    )
    parser.add_argument(
        "--card",
        default="agents",
        help="AgentCard file or directory to load.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the default model (CLI-style --model).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Enable AgentCard watch (reload on file change).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable manual reload support for AgentCards.",
    )
    args = parser.parse_args()

    fast = FastAgent(config_path="fastagent.config.yaml")
    # Mimic CLI flags for reload/watch behavior in programmatic mode.
    fast.args = SimpleNamespace(
        watch=args.watch,
        reload=args.reload or args.watch,
        model=args.model,
        name=None,
        quiet=False,
        server=False,
        transport=None,
    )
    fast.load_agents(args.card)

    chat_histories: dict[str, list[PromptMessageExtended]] = {}
    async with fast.run() as app:
        chat_id = "chat-123"
        history = chat_histories.setdefault(chat_id, [])
        base_agent_name = _get_default_agent_name(app)
        reply = await handle_message(app, base_agent_name, chat_id, history, args.message)
        print(reply)


if __name__ == "__main__":
    asyncio.run(main())

# CLI equivalents (run from ai-telegram directory):
# uv run fast-agent go --card agents --watch
# uv run fast-agent go --card agents --message "ping" --model gpt-5-mini
# uv run fast-agent go --card agents --reload         # refresh cards mid-run
#
# ACP entrypoint conventions used in ai-telegram:
# uv run fast-agent serve --transport acp --card agents
# uv run python /path/to/app.py  # custom ACP-capable entrypoint
