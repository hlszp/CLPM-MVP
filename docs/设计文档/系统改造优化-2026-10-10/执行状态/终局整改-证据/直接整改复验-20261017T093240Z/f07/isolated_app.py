from contextlib import asynccontextmanager
from app.main import create_app
from app.core.db import dispose_engine
app=create_app()
@asynccontextmanager
async def isolated_lifespan(app):
    yield
    await dispose_engine()
app.router.lifespan_context=isolated_lifespan
