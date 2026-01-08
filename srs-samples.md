# ai-telegram samples

Concrete ACP instance samples used by the SRS. These are examples, not a complete list.

## Connectable instances

- /home/strato-space/fast-agent-vertex-rag
  - AgentCards: /home/strato-space/fast-agent-vertex-rag/agents
  - Setup (from README):
    - `uv sync`
    - `cp fastagent.secrets.yaml.example fastagent.secrets.yaml`
    - `gcloud auth application-default login` (and grant Drive access)
  - Run (from README):
    - Batch: `uv run fast-agent go --card agents --message "<prompt>"`
    - Interactive: `uv run fast-agent go --card agents --watch`
  - Card-based entry example:
    - Instance name: `FastAgentVertexRag`
    - ACP CLI command: `uv run fast-agent serve --transport acp --card /home/strato-space/fast-agent-vertex-rag/agents`
    - Working directory: `/home/strato-space/fast-agent-vertex-rag`
    - Socket: `./FastAgentVertexRag.sock`
    - Token: `TELEGRAM_TOKEN__FastAgentVertexRag`

## Content bots (initial scope)

### MediaGenMeme (Python entrypoint)

- Instance name: `MediaGenMeme`
- ACP CLI command: `uv run python /home/strato-space/prompt/MediaGenMeme/app/MediaGenMeme.py`
- Working directory: `/home/strato-space/prompt/MediaGenMeme/app`
- Socket: `./MediaGenMeme.sock`
- Token: `TELEGRAM_TOKEN__MediaGenMeme`
- Default agent: `meme_train` (chain)

### MediaGenBlender (Python entrypoint)

- Instance name: `MediaGenBlender`
- ACP CLI command: `uv run python /home/strato-space/prompt/MediaGenBlender/app/MediaGenBlender.py`
- Working directory: `/home/strato-space/prompt/MediaGenBlender/app`
- Socket: `./MediaGenBlender.sock`
- Token: `TELEGRAM_TOKEN__MediaGenBlender`
- Default agent: `media_gen_blender`

### StratoProject (Python entrypoint)

- Instance name: `StratoProject`
- ACP CLI command: `uv run python /home/strato-space/fast/StratoProject.py`
- Working directory: `/home/strato-space/fast`
- Socket: `./StratoProject.sock`
- Token: `TELEGRAM_TOKEN__StratoProject`
- Default agent: `StratoProject`
