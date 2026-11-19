"""启动入口：python run.py"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "0") == "1"
    print(f"南特金属智能客服 -> http://{host}:{port}  (API 文档: /docs)")
    uvicorn.run("app.api:app", host=host, port=port, reload=reload)
