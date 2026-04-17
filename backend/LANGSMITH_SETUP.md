# LangSmith Tracing Setup for DelibRAG

This document explains how to enable and use LangSmith tracing for the DelibRAG agent.

## What is LangSmith?

LangSmith is a platform for debugging, testing, and monitoring LLM applications. It provides:
- **Trace visualization**: See the complete execution flow of your agent as a tree
- **Performance metrics**: Track latency, token usage, and costs
- **Debugging tools**: Inspect inputs/outputs at each step
- **Monitoring**: Track production usage and errors

## Setup Instructions

### 1. Get a LangSmith API Key

1. Go to [https://smith.langchain.com/](https://smith.langchain.com/)
2. Sign up or log in
3. Navigate to Settings → API Keys
4. Create a new API key

### 2. Configure Environment Variables

Add the following to your `.env` file:

```bash
# LangSmith Tracing Configuration
LANGSMITH_API_KEY=your_actual_api_key_here
LANGSMITH_PROJECT=delibrag
LANGSMITH_TRACING_ENABLED=true
```

**Configuration Options:**

- `LANGSMITH_API_KEY`: Your LangSmith API key (required for tracing)
- `LANGSMITH_PROJECT`: Project name in LangSmith (default: "delibrag")
- `LANGSMITH_TRACING_ENABLED`: Set to `true` to enable tracing, `false` to disable

### 3. Restart the Backend

```bash
docker-compose restart backend
```

Or if running locally:

```bash
cd backend
uvicorn main:app --reload
```

## What Gets Traced

**LangGraph has built-in LangSmith integration!** When tracing is enabled, each chat request creates a **single trace tree** showing:

### Root Trace
- **Name**: `LangGraph` (the compiled graph execution)
- **Type**: Chain
- **Contains**: All node executions as children

### Child Nodes (automatically traced by LangGraph)
- `load_history`: Load conversation history
- `scope_check`: Query scope classification and role-topic mismatch detection
- `stakes_classify`: Stakes level classification (high/low)
- `low_stakes_retrieve` OR `high_stakes_retrieve`: Retrieval based on stakes
- `gap_detect`: Knowledge gap detection (runs twice - before and after confidence check)
- `answer_generate`: LLM answer generation
- `confidence_check`: Confidence scoring and human review flagging
- `gap_ticket_create`: Create knowledge gap tickets if needed
- `audit_log`: Log high-stakes queries
- `memory_save`: Save conversation to database

### Conditional Nodes
- `out_of_scope_response`: When query is out of scope
- `role_mismatch_response`: When query doesn't match user's role

## Viewing Traces

1. Go to [https://smith.langchain.com/](https://smith.langchain.com/)
2. Select your project (default: "delibrag")
3. View traces in the "Traces" tab
4. Click on any trace to see the complete execution tree

### Trace Structure

Each chat request creates a single trace with this structure:

```
LangGraph (root)
├── load_history
├── scope_check
│   └── [Conditional routing based on scope]
├── stakes_classify
├── low_stakes_retrieve OR high_stakes_retrieve
│   └── [Retrieval operations]
├── gap_detect (pre-answer)
├── answer_generate
│   └── [LLM call]
├── confidence_check
├── gap_detect (post-confidence)
├── gap_ticket_create
├── audit_log
└── memory_save
```

**Alternative paths:**
- If out of scope: `scope_check` → `out_of_scope_response` → `audit_log` → `memory_save`
- If role mismatch: `scope_check` → `role_mismatch_response` → `audit_log` → `memory_save`

## How It Works

LangGraph automatically integrates with LangSmith when these environment variables are set:
- `LANGCHAIN_TRACING_V2=true`
- `LANGCHAIN_API_KEY=<your_key>`
- `LANGCHAIN_PROJECT=<project_name>`

The `configure_langsmith()` function in `backend/agent/tracing.py` sets these variables based on your `.env` configuration.

**No decorators needed!** LangGraph traces the entire graph execution automatically, creating a single trace tree with all nodes as children.

## Performance Considerations

- Tracing adds minimal overhead (~10-50ms per request)
- Traces are sent asynchronously to LangSmith
- Failed trace uploads don't affect application functionality
- Disable tracing in production if not needed for monitoring

## Troubleshooting

### Traces Not Appearing

1. **Check API key**: Ensure `LANGSMITH_API_KEY` is set correctly
2. **Check enabled flag**: Ensure `LANGSMITH_TRACING_ENABLED=true`
3. **Check logs**: Look for LangSmith-related errors in backend logs
4. **Network access**: Ensure backend can reach `https://api.smith.langchain.com`

### Viewing Logs

```bash
docker-compose logs backend | grep -i langsmith
```

### Disabling Tracing

Set in `.env`:
```bash
LANGSMITH_TRACING_ENABLED=false
```

Or remove the `LANGSMITH_API_KEY` variable entirely.

## Trace Details

Each node in the trace includes:
- **Input**: The AgentState passed to the node
- **Output**: The updated AgentState returned by the node
- **Latency**: Time taken to execute the node
- **Errors**: Any exceptions raised during execution

You can inspect the state at each step to understand:
- What data was retrieved
- How confidence was calculated
- Why certain decisions were made
- What knowledge gaps were detected

## Security Notes

- **API Key Security**: Never commit your `LANGSMITH_API_KEY` to version control
- **Data Privacy**: Traces include query text and responses - ensure compliance with your data policies
- **Access Control**: Manage team access in LangSmith settings

## Support

- LangSmith Documentation: [https://docs.smith.langchain.com/](https://docs.smith.langchain.com/)
- LangGraph Tracing: [https://python.langchain.com/docs/langgraph/how-tos/tracing](https://python.langchain.com/docs/langgraph/how-tos/tracing)
- LangChain Discord: [https://discord.gg/langchain](https://discord.gg/langchain)
