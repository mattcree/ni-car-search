import uvicorn

from .app import app
from .config import HOST, PORT

uvicorn.run(app, host=HOST, port=PORT)
