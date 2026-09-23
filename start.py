"""Single command startup, including the same AI data used by Pages."""
from pathlib import Path
import os
import uvicorn
from prepare_release import prepare

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    prepare(root)
    host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1")
    uvicorn.run("main:app", host=host, port=int(os.environ.get("PORT", "8000")))
