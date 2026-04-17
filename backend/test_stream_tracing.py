"""Test script to verify LangSmith tracing works with the streaming endpoint."""
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up LangSmith environment
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGSMITH_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "DelibRAG")
os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"

from langsmith import traceable


@traceable(name="test_nested_function", run_type="chain")
async def nested_function(value: str):
    """A nested function to test tracing hierarchy."""
    await asyncio.sleep(0.1)
    return f"Processed: {value}"


@traceable(name="test_main_function", run_type="chain", tags=["test", "streaming"])
async def main_function(query: str):
    """Main function that simulates the chat stream execution."""
    print(f"Processing query: {query}")
    
    # Simulate multiple steps
    result1 = await nested_function("step1")
    print(f"Step 1: {result1}")
    
    result2 = await nested_function("step2")
    print(f"Step 2: {result2}")
    
    result3 = await nested_function("step3")
    print(f"Step 3: {result3}")
    
    return {
        "answer": f"Final answer for: {query}",
        "steps": [result1, result2, result3]
    }


async def test_tracing():
    """Test the tracing setup."""
    print("Testing LangSmith tracing...")
    print(f"API Key: {os.getenv('LANGSMITH_API_KEY', 'NOT SET')[:20]}...")
    print(f"Project: {os.getenv('LANGSMITH_PROJECT', 'NOT SET')}")
    print(f"Tracing: {os.getenv('LANGCHAIN_TRACING_V2', 'NOT SET')}")
    print()
    
    result = await main_function("test query for streaming")
    print(f"\nResult: {result}")
    print("\nCheck LangSmith dashboard for trace: test_main_function")


if __name__ == "__main__":
    asyncio.run(test_tracing())
