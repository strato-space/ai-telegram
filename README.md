# telegram-ai

Telegram-native AI chatbot with [fast-agent.ai](https://fast-agent.ai) and [ACP](https://agentclientprotocol.com/overview/introduction)/[MCP](https://modelcontextprotocol.io) integration.

## Goals

- Full compatibility with the fast-agent.ai ecosystem: ACP/MCP behavior, permissions, tool progress, and agent routing conventions
- Native support for Telegram AI chatbot features: topics, threaded conversations, streaming, /commands
- Deep ACP support: list/switch agents via @, streaming responses, /commands
- MCP support for legacy agents platform: call legacy agents without streaming
- Full multimedia support: images, video, audio
- Document support: Markdown, PDF, TXT as inbound/outbound attachments

## Telegram Docs Excerpt (Bots)

Source: https://core.telegram.org/bots#natively-integrate-ai-chatbots

> ... Bots natively support threaded conversations to manage several different topics in parallel.
> This is especially useful for building AI chatbots and lets users easily access information
> from previous chats. Instead of waiting for full replies, chatbots can also stream live
> responses as they are generated. ...

![Telegram bots docs image](https://core.telegram.org/file/464001186/11e04/7XO37b9iccE.133932/a29f8bf593af567fcc)

## fast-agent.ai Integration Principles

- ACP first: use it for user-visible chat flows (agent list/switch, streaming replies),
  and reserve MCP for legacy agent tool calls.
- Authoritative history: send full `message_history` including thread/topic/user context and
  chat-scoped /instructions so ACP can reason over the same timeline as the user.
- Deterministic routing: honor explicit @mentions as the target; otherwise default to the
  project orchestrator and record the decision in request metadata.
- Streaming UX parity: stream partial tokens when possible; fall back to a single final
  message when streaming is unavailable.
- Multimedia parity: pass through images, video, and audio with ACP/MCP-compatible metadata.
- Document parity: support Markdown, PDF, and TXT as inbound/outbound attachments.
- Consistent tool reporting: surface tool errors through standard ACP/MCP channels and
  merge usage stats for monitoring.
- REPL support: [#585](https://github.com/evalstate/fast-agent/pull/585) adds AgentCard in Markdown and CLI loading/lazy hot-swap with --agent-cards | --watch | --reload. Edit or create agent prompts directly from Telegram.

## Bot Compatibility Features (from `repo call` [call/docs/specialized-bots.md](https://github.com/strato-space/call/blob/main/docs/specialized-bots.md))

- Default target routing: if no explicit target is mentioned, route to the project orchestrator (`project.md`).
- Commands from metadata: read project `commands` from `project.md` and expose them in the Telegram menu, /start, and /help.
- Chat/thread-scoped instructions: `/instructions` stores per-chat/per-thread prompts and injects them into subsequent calls.
- Natural language help: `/start` shows the project goal and examples, not just raw commands.
- Mention handling: supports direct and group chat mention patterns, with explicit target overrides.

## References

fast-agent:
- fast-agent.ai: https://fast-agent.ai
- fast-agent.ai ACP: https://fast-agent.ai/acp
- fast-agent GitHub: https://github.com/evalstate/fast-agent/

ACP:
- Zed ACP overview: https://zed.dev/acp
- Agent Client Protocol intro: https://agentclientprotocol.com/overview/introduction

MCP:
- Model Context Protocol: https://modelcontextprotocol.io

Telegram:
- Telegram Bots docs: https://core.telegram.org/bots#natively-integrate-ai-chatbots
- Telegram Bot API: https://core.telegram.org/bots/api
