from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from auth.router import router as auth_router
from knowledge_gap.router import router as gap_router


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


@app.get("/health")
async def health():
    return {"status": "ok"}
