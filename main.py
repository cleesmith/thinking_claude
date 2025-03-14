# uvicorn main:app --workers 1 --host 127.0.0.1 --port 3000
# 
# https://docs.render.com/deploy-fastapi
#   https://keypoints-oc6g.onrender.com = BASE_URL
#   uvicorn main:app --workers 1 --host 0.0.0.0 --port $PORT
# 
# start ollama like this:
# OLLAMA_ORIGINS="http://127.0.0.1,https://app.novelcrafter.com" ollama serve
import frontend
from frontend import *

import os
import time
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from starlette.config import Config
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, RedirectResponse
from authlib.integrations.starlette_client import OAuth, OAuthError

# from icecream import ic
# ic.configureOutput(includeContext=True, contextAbsPath=True)


# this 'app' is global to all users:
app = FastAPI() # different from nicegui's "app"

config = Config(".env") # starlette.config

APP_ENV = config.get("APP_ENV", default="local") # set to production on Render.com

app.add_middleware(SessionMiddleware, secret_key=config("SECRET_KEY"))

frontend.init(config("SECRET_KEY"), app)

if __name__ == '__main__':
	print("start it this way:")
	print("uvicorn main:app --workers 1 --host localhost --port 3000")
