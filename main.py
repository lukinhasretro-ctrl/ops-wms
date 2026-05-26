from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
import os

from database import init
from modules.estoque import router as estoque_router
from modules.pcp import router as pcp_router

app = FastAPI(title="OPS-WMS", version="2.0")

# inicializa banco
init()

# routers
app.include_router(estoque_router)
app.include_router(pcp_router)

# frontend
@app.get("/", response_class=HTMLResponse)
def root():
    return FileResponse("templates/index.html")

static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
