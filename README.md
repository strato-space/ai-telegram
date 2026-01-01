# telegram-ai

Telegram-native AI bot experiments with fast-agent ACP/MCP integration.

## Goals

- Natively Integrate AI Chatbots (Telegram docs excerpt + image below)
- Deep ACP support: list/switch agents via @, streaming responses
- MCP support for legacy agents platform (call agent via MCP)

## Telegram Docs Excerpt (Bots)

Source: https://core.telegram.org/bots#natively-integrate-ai-chatbots

> Bots natively support threaded conversations to manage several different topics in parallel.
> This is especially useful for building AI chatbots and lets users easily access information
> from previous chats. Instead of waiting for full replies, chatbots can also stream live
> responses as they are generated. You can enable topics in private chats by toggling on
> Threaded Mode via @BotFather. This feature is subject to an additional fee for Telegram Star
> purchases as described in Section 6.2.6 of the Terms of Service for Bot Developers.

![Telegram bots docs image](https://core.telegram.org/file/464001186/11e04/7XO37b9iccE.133932/a29f8bf593af567fcc)

## Bot Features (from specialized-bots.md)

- Default target routing: if no explicit target is mentioned, route to the project
  orchestrator (project.md).
- Commands from metadata: read project-level commands from YAML and expose them in /start
  and /help.
- Chat-scoped instructions: /instructions stores per-chat prompts and injects them into
  the next calls.
- Natural language help: /start shows project goal and examples, not just raw commands.
- Mention handling: supports direct and group chat mention patterns, with explicit
  target overrides.

## References

- Telegram Bots docs: https://core.telegram.org/bots#natively-integrate-ai-chatbots
- fast-agent ACP: https://fast-agent.ai/acp/
