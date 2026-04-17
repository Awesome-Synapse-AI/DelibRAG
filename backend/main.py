from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from agent.tracing import configure_langsmith
from auth.router import router as auth_router
from knowledge_gap.router import router as gap_router
from agent.router import router as chat_router
from indexing.router import router as indexing_router
from audit.router import router as audit_router

# Configure LangSmith tracing before anything else
configure_langsmith()

settings = get_settings()
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(gap_router, prefix="/gaps", tags=["knowledge-gap"])
app.include_router(chat_router, tags=["chat"])
app.include_router(indexing_router, prefix="/indexing", tags=["indexing"])
app.include_router(audit_router, prefix="/audit", tags=["audit"])


@app.get("/health")
async def health():
    return {"status": "ok"}
