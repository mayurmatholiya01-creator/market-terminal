from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sqlite3
import os
from datetime import datetime
from typing import List
import uvicorn
from dotenv import load_dotenv
import pyotp
from SmartApi import SmartConnect

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AngelOneAPI:
    def __init__(self):
        self.api_key = os.getenv("ANGEL_API_KEY")
        self.client_code = os.getenv("ANGEL_CLIENT_CODE")
        self.password = os.getenv("ANGEL_PASSWORD")
        self.totp_secret = os.getenv("ANGEL_TOTP_SECRET")
        self.smart = SmartConnect(api_key=self.api_key) if self.api_key else None
        self.jwt = None
        self.feed_token = None
        self.symbol_tokens = {
            "RELIANCE": "2885", "TCS": "11536", "INFY": "1594",
            "HDFCBANK": "1333", "ITC": "424", "SBIN": "3045"
        }

    def generate_session(self):
        if not self.smart: return False
        try:
            totp = pyotp.TOTP(self.totp_secret).now()
            data = self.smart.generateSession(self.client_code, self.password, totp)
            if data["status"]:
                self.jwt = data["data"]["jwtToken"]
                self.feed_token = self.smart.getfeedToken()
                return True
        except:
            pass
        return False

    def get_ltp(self, sym):
        if not self.jwt: return None
        token = self.symbol_tokens.get(sym.upper())
        if not token: return None
        try:
            resp = self.smart.ltpData("NSE", f"{sym}-EQ", token)
            if resp["status"]:
                d = resp["data"]
                return {"ltp": d["ltp"], "change": d.get("change",0), "changePercent": d.get("pChange",0)}
        except:
            pass
        return None

angel_api = AngelOneAPI()

class WatchlistCreate(BaseModel):
    name: str
    symbols: List[str] = []

class StockAdd(BaseModel):
    symbol: str

def init_db():
    conn = sqlite3.connect("market_terminal.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS watchlists (id INTEGER PRIMARY KEY, name TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS watchlist_stocks (id INTEGER PRIMARY KEY, watchlist_id INTEGER, symbol TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("INSERT OR IGNORE INTO watchlists (id,name) VALUES (1,'My Portfolio')")
    conn.commit(); conn.close()

@app.on_event("startup")
async def startup():
    init_db()
    ok = angel_api.generate_session()
    print("Angel One session:", "Connected" if ok else "Mock")

@app.get("/health")
async def health():
    return {"status":"healthy","timestamp":datetime.now().isoformat(),"angel_one_status": "Connected" if angel_api.jwt else "Mock"}

# (बाकी endpoints वही रखने हैं)

if __name__=="__main__":
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT",8000)))
