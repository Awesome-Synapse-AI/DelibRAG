# LangSmith Tracing Implementation

## Overview
LangSmith tracing has been implemented to track the complete execution flow of the DelibRAG agent, showing each node execution as a separate step in the trace tree with proper inputs and outputs.

## Configuration

### Environment Variables
The following environment variables must be set in `.env`:

```env
LANGSMITH_API_KEY=<your-langsmith-api-key>
LANGSMITH_PROJECT=DelibRAG
LANGSMITH_TRACING_ENABLED=true
```

### Files Modified

1. **backend/requirements.txt**
   - Added `langsmith` package

2. **backend/config.py**
   - Added LangSmith configuration settings:
     - `langsmith_api_key`
     - `langsmith_project`
     - `langsmith_tracing_enabled`

3. **backend/agent/tracing.py**
   - Created tracing utilities module
   - `configure_langsmith()`: Sets up LangSmith environment variables
   - `is_tracing_enabled()`: Checks if tracing is enabled

4. **backend/main.py**
   - Calls `configure_langsmith()` on application startup

5. **backend/agent/nodes.py**
   - Added `@traceable` decorator to all key node functions:
     - `load_history_node` - Loads conversation history
     - `scope_check_node` - Checks if query is in scope
     - `stakes_classify_node` - Classifies query stakes (high/low)
     - `low_stakes_retrieve_node` - Retrieves for low stakes queries
     - `high_stakes_retrieve_node` - Retrieves for high stakes queries
     - `confidence_check_node` - Checks confidence threshold
     - `out_of_scope_response_node` - Handles out of scope queries
     - `gap_detect_node` - Detects knowledge gaps
     - `gap_ticket_create_node` - Creates gap tickets
     - `audit_log_node` - Logs to audit trail
     - `memory_save_node` - Saves to session memory
     - `answer_stream` - Streams LLM response

6. **backend/agent/router.py**
   - **GET /chat/stream endpoint**: Uses `ls.trace()` context manager to wrap entire execution
   - Proper inputs and outputs captured for the trace

7. **docker-compose.yml**
   - Added LangSmith environment variables to backend service

## Implementation Details

### Trace Structure

Each chat request creates ONE trace with the following structure:

```
DelibRAG_Chat_Stream (root trace)
├── Input: {session_id, query, user_role, user_department}
├── load_history (child trace)
├── scope_check (child trace)
├── stakes_classify (child trace)
├── low_stakes_retrieve OR high_stakes_retrieve (child trace)
├── gap_detect (child trace)
├── answer_stream (child trace - LLM call)
├── confidence_check (child trace)
├── gap_detect (child trace - second check)
├── gap_ticket_create (child trace)
├── audit_log (child trace)
├── memory_save (child trace)
└── Output: {answer, citations, confidence, stakes_level, gap_ticket_id, requires_human_review}
```

### Streaming Endpoint Implementation

The `/chat/stream` endpoint uses the `ls.trace()` context manager:

```python
trace_inputs = {
    "session_id": session_id,
    "query": query,
    "user_role": working_state["user_role"],
    "user_department": working_state["user_department"],
}

with ls.trace(
    name="DelibRAG_Chat_Stream",
    run_type="chain",
    project_name=settings.langsmith_project,
    inputs=trace_inputs,
    tags=["chat", f"role_{working_state['user_role']}"],
    metadata={"session_id": session_id}
) as rt:
    # Execute all nodes - each node has @traceable decorator
    working_state = await load_history_node(working_state)
    working_state = await scope_check_node(working_state)
    # ... more nodes ...
    
    # Set trace outputs
    rt.end(outputs={
        "answer": working_state.get("answer", ""),
        "citations": working_state.get("citations", []),
        "confidence": working_state.get("confidence"),
        "stakes_level": working_state.get("stakes_level"),
        "gap_ticket_id": working_state.get("gap_ticket_id"),
        "requires_human_review": working_state.get("requires_human_review"),
    })
```

### Key Design Decisions

1. **Context Manager Approach**: Using `ls.trace()` context manager for the root trace with explicit inputs/outputs

2. **Decorator on Nodes**: Each node function has `@traceable` decorator to appear as child traces

3. **Proper Input/Output Capture**: 
   - Root trace shows query, session_id, user info as inputs
   - Root trace shows answer, citations, confidence as outputs
   - Each node trace shows its state transformations

4. **Run Types**: 
   - `chain` for orchestration nodes
   - `retriever` for retrieval nodes
   - `llm` for LLM calls

5. **Conditional Tracing**: Tracing only activates when `LANGSMITH_TRACING_ENABLED=true`

## Viewing Traces

1. Go to [LangSmith Dashboard](https://smith.langchain.com/)
2. Select project: **DelibRAG**
3. Look for traces named: **DelibRAG_Chat_Stream**
4. Each trace shows:
   - **Input tab**: Query, session_id, user_role, user_department
   - **Output tab**: Answer, citations, confidence, stakes_level, etc.
   - **Trace tree**: All node executions as child runs with their own inputs/outputs

## Testing

### Frontend Testing
1. Start the application: `docker compose up`
2. Log in to the frontend
3. Send a chat message
4. Check LangSmith dashboard for new trace
5. Expand the trace to see all node executions

### Expected Trace View

You should see:
- **Root trace**: DelibRAG_Chat_Stream
  - Input: Shows your query and user info
  - Output: Shows the generated answer and metadata
- **Child traces**: Each node execution (load_history, scope_check, stakes_classify, etc.)
  - Each child shows what it did and how it transformed the state

## Troubleshooting

### No Traces Appearing

1. **Check environment variables**:
   ```bash
   docker compose exec backend env | grep LANGSMITH
   docker compose exec backend env | grep LANGCHAIN
   ```

2. **Check backend logs**:
   ```bash
   docker compose logs backend | grep LangSmith
   ```
   Should see: `[LangSmith] Tracing enabled for project: DelibRAG`

3. **Verify API key**: Ensure the API key in `.env` is correct

### No Input/Output Showing

This was the previous issue - now fixed by:
- Using `ls.trace()` context manager with explicit `inputs` parameter
- Using `rt.end(outputs={...})` to set outputs
- Each node has `@traceable` decorator to show as child traces

### Trace Shows Tuple Output

This was the previous issue - now fixed by properly structuring the outputs as a dictionary instead of returning a tuple.

