from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import engine, Base
from app.api.routes import auth, businesses, blocks, media, search, endpoint_verification, intent, registry_search, claims, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (use Alembic in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="TETA+PI API",
    description="Trust infrastructure for the agent economy",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(businesses.router, prefix="/api/v1")
app.include_router(blocks.router, prefix="/api/v1")
app.include_router(blocks.blocks_router, prefix="/api/v1")
app.include_router(media.router, prefix="/api/v1")
app.include_router(media.devices_router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(endpoint_verification.router, prefix="/api/v1")
app.include_router(intent.router, prefix="/api/v1")
app.include_router(registry_search.router, prefix="/api/v1")
app.include_router(claims.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/")
async def root() -> dict:
    return {
        "name": "TETA+PI API",
        "description": "Trust infrastructure for the agent economy",
        "docs": "/docs",
        "mcp": "/mcp/sse",
    }
