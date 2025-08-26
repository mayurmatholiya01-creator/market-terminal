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
from smartapi import SmartConnect
from Crypto.Cipher import AES  # Provided by pycryptodome

load_dotenv()

app = FastAPI(title="Market Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Angel One API Integration
class AngelOneAPI:
    def __init__(self):
        self.api_key = os.getenv('ANGEL_API_KEY')
        self.client_code = os.getenv('ANGEL_CLIENT_CODE')
        self.password = os.getenv('ANGEL_PASSWORD')
        self.totp_secret = os.getenv('ANGEL_TOTP_SECRET')
        if self.api_key:
            self.client = SmartConnect(api_key=self.api_key)
            self.access_token = None
        else:
            self.client = None
            self.access_token = None

    def generate_session(self):
        if not self.client:
            return False
        try:
            totp = pyotp.TOTP(self.totp_secret).now()
            resp = self.client.generateSession(
                clientCode=self.client_code,
                password=self.password,
                totp=totp
            )
            if resp.get("status"):
                token = resp["data"]["jwtToken"]
                self.client.set_jwtToken(token)
                self.access_token = token
                print("✅ Angel One session created successfully")
                return True
            else:
                print(f"❌ Session failed: {resp.get('message')}")
                return False
        except Exception as e:
            print(f"❌ Angel One error: {str(e)}")
            return False

    def get_ltp(self, symbol):
        if not self.access_token:
            return None
        try:
            # Example: fetch LTP for NSE
            data = self.client.ltpData(exchange="NSE", tradingsymbol=symbol, symboltoken="26009")
            if data.get("status"):
                d = data["data"]
                return {
                    "ltp": d["ltp"],
                    "change": d.get("change", 0),
                    "changePercent": d.get("pChange", 0)
                }
        except Exception:
            pass
        return None

angel_api = AngelOneAPI()

# Data Models
class WatchlistCreate(BaseModel):
    name: str
    symbols: List[str] = []

class StockAdd(BaseModel):
    symbol: str

def init_db():
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS watchlist_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER,
            symbol TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (watchlist_id) REFERENCES watchlists(id)
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (1, 'My Portfolio')")
    conn.commit()
    conn.close()

@app.on_event("startup")
async def startup_event():
    init_db()
    success = angel_api.generate_session()
    if not success:
        print("⚠️ Angel One session failed — running in mock mode")

@app.get("/api/watchlists")
async def get_watchlists():
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT w.id, w.name, COUNT(ws.symbol) as stock_count
        FROM watchlists w
        LEFT JOIN watchlist_stocks ws ON w.id = ws.watchlist_id
        GROUP BY w.id, w.name
        ORDER BY w.id
    ''')
    result = [{"id": r[0], "name": r[1], "stock_count": r[2]} for r in cursor.fetchall()]
    conn.close()
    return {"watchlists": result}

@app.post("/api/watchlists")
async def create_watchlist(watchlist: WatchlistCreate):
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO watchlists (name) VALUES (?)", (watchlist.name,))
    wid = cursor.lastrowid
    for sym in watchlist.symbols:
        cursor.execute("INSERT INTO watchlist_stocks (watchlist_id, symbol) VALUES (?, ?)",
                       (wid, sym.upper()))
    conn.commit()
    conn.close()
    return {"id": wid, "message": f"Watchlist '{watchlist.name}' created"}

@app.post("/api/watchlists/{watchlist_id}/add-stock")
async def add_stock(watchlist_id: int, stock: StockAdd):
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM watchlist_stocks WHERE watchlist_id=? AND symbol=?",
                   (watchlist_id, stock.symbol.upper()))
    if cursor.fetchone()[0] > 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Stock already exists")
    cursor.execute("INSERT INTO watchlist_stocks (watchlist_id, symbol) VALUES (?, ?)",
                   (watchlist_id, stock.symbol.upper()))
    conn.commit()
    conn.close()
    return {"message": f"Stock {stock.symbol} added"}

@app.get("/api/watchlists/{watchlist_id}/stocks")
async def get_watchlist_stocks(watchlist_id: int):
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM watchlist_stocks WHERE watchlist_id=? ORDER BY added_at",
                   (watchlist_id,))
    stocks = []
    for (sym,) in cursor.fetchall():
        real = angel_api.get_ltp(sym)
        if real:
            stocks.append({ "symbol": sym, **real, "volume": 0, "sector": "" })
        else:
            # mock fallback
            base = 1000 + hash(sym) % 2000
            chg = (hash(sym) % 200) - 100
            stocks.append({
                "symbol": sym,
                "ltp": base + chg,
                "change": chg,
                "changePercent": round(chg/base*100, 2),
                "volume": 100000 + hash(sym)%1000000,
                "sector": "Technology"
            })
    conn.close()
    return {"stocks": stocks}

@app.get("/api/market/indices")
async def get_market_indices():
    real = None
    if angel_api.access_token:
        try:
            d = angel_api.client.ltpData("NSE", "NIFTY", "99926000")["data"]
            real = [
                {"name": "NIFTY", "value": d["ltp"], "change": d.get("change",0), "changePercent": d.get("pChange",0)}
            ]
        except:
            real = None
    if not real:
        real = [
            {"name": "NIFTY 50", "value": 19674.25, "change": 156.8, "changePercent": 0.80},
            {"name": "SENSEX", "value": 66023.69, "change": 525.42, "changePercent": 0.80},
            {"name": "BANK NIFTY", "value": 44258.75, "change": -125.3, "changePercent": -0.28}
        ]
    return {"indices": real}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "angel_one_status": "Connected" if angel_api.access_token else "Mock Data"
    }

app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000, ))
    uvicorn.run(app, host="0.0.0.0", port=port)
