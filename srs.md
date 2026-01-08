# ai-telegram (SRS)

Telegram-native AI bots built on fast-agent ACP. 

## Goals

- Prioritize `fast-agent --transport acp` and its ecosystem as the primary integration surface.
- Telegram-first UX: topics, thread-aware context, streaming, slash commands.
- ACP-first integration with fast-agent: modes, tool progress, and streaming output.
- No intermediate configs: per ACP instance only Telegram token and fast-agent entrypoint.
- Each ACP instance runs its own ACP service and Unix socket.

## Architecture (general)

### ACP instance linked 1:1 to Telegram Bot

- Each ACP instance corresponds to one Telegram bot.
- Each ACP instance has exactly one ACP service process and one Unix socket:
  - Socket path: `./<InstanceName>.sock` (relative to repo root).

### ACP service per instance

- A dedicated process runs fast-agent in ACP mode for each instance.
- The Telegram bot process connects to the socket and streams requests/responses.
- The service does not know chat_id; the bot client owns chat_id to session_id mapping.

### Configuration (no intermediate configs)

Only two variables per instance:

- `TELEGRAM_TOKEN__<InstanceName>`
- `ACP_CLI_COMMAND_<InstanceName>`

The entrypoint command can be:

- A Python entrypoint that defines FastAgent and starts ACP mode (example:
  `uv run python /path/to/app.py`).
- A fast-agent AgentCard path (file or directory) used with
  `uv run fast-agent serve --transport acp --card <path>`.

No extra YAML/JSON configs are allowed.

## Telegram behavior

### Commands

- `/start`: show instance goal and examples from metadata.
- `/help`: show command list, including all ACP commands received from the ACP agent.
- `/instructions`: chat/thread scoped instructions (see below). These are fast-start, unnamed prompt prefixes that are forcibly prepended to every user request.
- Commands from `project.md` metadata are exposed in menu and help.

### /instructions (chat and topic scoped)

- Storage: `instructions/instructions_<chat_id>_<thread_id>.md`
- thread_id is 0 for non-topic chats.
- Inject instructions into prompts as plain text prefix:
  `instructions + "\n" + user_input`
- `/instructions` with no args prints current instructions.
- `/instructions clear` or `/instructions -` clears instructions.
- Attach `.txt` or `.md` to set instructions.

### Target resolution and agent switching

- If the user specifies an explicit target via `@AgentName`, use that target.
- If no target is provided, use the instance orchestrator (`project.md`) via the fast-agent default agent.
- `@AgentName` can also set the default agent for the chat/thread until changed.
- Agent list is sourced from ACP modes exposed by the instance entrypoint.

## Streaming and output

- All bot replies are sent through `sendMessageDraft`.
- Stream both:
  - tool call events and tool logs, and
  - assistant message content.
- The draft message should interleave tool/log events and message text in order.
- Final message is the last draft state (no separate sendMessage).

## Attachments and multimodal input

- Telegram media and documents are forwarded as `resource_link` context items.
- Each link uses Telegram's file URL with the bot token embedded.
- Links are short-lived (about 1 hour) and must be consumed quickly.
- If there is no local download path, the agent should decide how to handle the link.

## Bot compatibility features (from specialized-bots.md)

- Default target routing to the instance orchestrator (`project.md`).
- Commands from `project.md` metadata.
- Chat/thread scoped `/instructions`.
- Natural language help in `/start`.
- Mention handling for direct and group chats with explicit target overrides.

## Instance samples

See `srs-samples.md` for concrete instance entries and paths.

## References

- fast-agent: https://fast-agent.ai
- AgentCard RFC: https://github.com/strato-space/fast-agent/blob/main/plan/agent-card-rfc.md
- AgentCard at the Summit: https://github.com/strato-space/fast-agent/blob/main/plan/agentcard-standards-mini-article.md
- ACP: https://agentclientprotocol.com/overview/introduction
- Telegram Bot API: https://core.telegram.org/bots/api
- Specialized bots: https://github.com/strato-space/call/blob/main/docs/specialized-bots.md
