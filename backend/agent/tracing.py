"""LangSmith tracing utilities for DelibRAG agent."""
import os
from functools import wraps
from typing import Any, Callable, Optional


def is_tracing_enabled() -> bool:
    """Check if LangSmith tracing is enabled."""
    try:
        from config import get_settings
        settings = get_settings()
        return settings.langsmith_tracing_enabled and bool(settings.langsmith_api_key)
    except Exception:
        return False


def configure_langsmith():
    """Configure LangSmith environment variables."""
    try:
        from config import get_settings
        settings = get_settings()
        
        if settings.langsmith_tracing_enabled and settings.langsmith_api_key:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
            os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
            os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
            print(f"[LangSmith] Tracing enabled for project: {settings.langsmith_project}")
        else:
            os.environ["LANGCHAIN_TRACING_V2"] = "false"
            print("[LangSmith] Tracing disabled")
    except Exception as e:
        print(f"[LangSmith] Configuration failed: {e}")
        os.environ["LANGCHAIN_TRACING_V2"] = "false"


def trace_node(name: Optional[str] = None, run_type: str = "chain"):
    """
    Decorator to trace agent nodes with LangSmith.
    
    Args:
        name: Optional custom name for the trace
        run_type: Type of run (chain, retriever, llm, tool)
    """
    def decorator(func: Callable) -> Callable:
        if not is_tracing_enabled():
            return func
        
        from langsmith import traceable
        
        node_name = name or func.__name__
        
        @traceable(name=node_name, run_type=run_type)
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        
        @traceable(name=node_name, run_type=run_type)
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


def trace_retrieval(name: Optional[str] = None):
    """Decorator specifically for retrieval operations."""
    return trace_node(name=name, run_type="retriever")


def trace_llm_call(name: Optional[str] = None):
    """Decorator specifically for LLM calls."""
    return trace_node(name=name, run_type="llm")


def add_trace_metadata(metadata: dict[str, Any]):
    """
    Add metadata to the current trace run.
    Note: Metadata is automatically captured from function arguments.
    This is a no-op placeholder for compatibility.
    """
    pass


def add_trace_tags(tags: list[str]):
    """
    Add tags to the current trace run.
    Note: Tags should be added via the @traceable decorator.
    This is a no-op placeholder for compatibility.
    """
    pass
