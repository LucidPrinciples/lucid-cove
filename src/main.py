"""Cove-core agent — entry point.

Port is read from agent.yaml instance config.
uvicorn imports app from src.dashboard.app.
"""
import uvicorn
from src.config import get_instance
from src.dashboard.app import app

if __name__ == "__main__":
    instance = get_instance()
    port = instance.get("port", 8200)
    uvicorn.run(app, host="0.0.0.0", port=port)
