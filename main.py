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

# SmartConnect import करें (यह Angel One का Python SDK है)
from SmartApi import SmartConnect

load_dotenv()

app = FastAPI(title="Market Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AngelOneAPI:
    def __init__(self):
        self.api_key    = os.getenv("ANGEL_API_KEY")
        self.api_secret = os.getenv("ANGEL_API_SECRET")
        self.client_code = os.getenv("ANGEL_CLIENT_CODE")
        self.password    = os.getenv("ANGEL_PASSWORD")
        self.totp_secret = os.getenv("ANGEL_TOTP_SECRET")
        # SmartConnect में API key और secret दोनों दें
        if self.api_key and self.api_secret:
            self.smart = SmartConnect(api_key=self.api_key, api_secret=self.api_secret)
        else:
            self.smart = None
        self.jwt_token = None
        self.feed_token = None
        # कुछ महत्वपूर्ण symbol tokens (जरूरत के अनुसार बढ़ा सकते हैं)
        self.symbol_tokens = {
            "RELIANCE": "2885",
            "TCS": "11536",
            "INFY": "1594",
            "HDFCBANK": "1333",
            "ITC": "424",
            "SBIN": "3045",
        }

    def generate_session(self) -> bool:
        if not self.smart or not all([self.client_code, self.password, self.totp_secret]):
            return False
        try:
            # TOTP कोड generate करें (Google Authenticator secret)
            totp_code = pyotp.TOTP(self.totp_secret).now()
            # लॉगिन करें
            data = self.smart.generateSession(self.client_code, self.password, totp_code)
            if not data or not data.get("status"):
                return False
            self.jwt_token = data["data"]["jwtToken"]
            self.feed_token = self.smart.getfeedToken()
            return True
        except Exception:
            return False

    def get_ltp(self, symbol: str):
        if not self.smart or not self.jwt_token:
            return None
        sym = symbol.upper().strip()
        token = self.symbol_tokens.get(sym)
        if not token:
            return None
        try:
            tradingsymbol = f"{sym}-EQ"
            resp = self.smart.ltpData("NSE", tradingsymbol, token)
            if resp and resp.get("status") and resp.get("data"):
                d = resp["data"]
                return {
                    "ltp": d.get("ltp"),
                    "change": d.get("change", 0),
                    "changePercent": d.get("pChange", 0),
                }
        except Exception:
            return None
        return None


angel_api = AngelOneAPI()

class WatchlistCreate(BaseModel):
    name: str
    symbols: List[str] = []

class StockAdd(BaseModel):
    symbol: str

def init_db():
    conn = sqlite3.connect("market_terminal.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER,
            symbol TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (watchlist_id) REFERENCES watchlists (id)
        )
    """)

    # डिफ़ॉल्ट watchlists बनाएं
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (1, 'My Portfolio')")
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (2, 'Growth Stocks')")
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (3, 'Value Picks')")

    default_stocks = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC"]
    for stock in default_stocks:
        cursor.execute(
            "INSERT OR IGNORE INTO watchlist_stocks (watchlist_id, symbol) VALUES (1, ?)",
            (stock,),
        )

    conn.commit()
    conn.close()

@app.on_event("startup")
async def startup_event():
    init_db()
    ok = angel_api.generate_session()
    print("Angel One session:", "Connected" if ok else "Mock")

@app.get("/api/watchlists")
async def get_watchlists():
    conn = sqlite3.connect("market_terminal.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT w.id, w.name, COUNT(ws.symbol) as stock_count
        FROM watchlists w
        LEFT JOIN watchlist_stocks ws ON w.id = ws.watchlist_id
        GROUP BY w.id, w.name
        ORDER BY w.id
    """)
    rows = cursor.fetchall()
    conn.close()
    return {
        "watchlists": [{"id": r[0], "name": r[1], "stock_count": r[2]} for r in rows]
    }

@app.post("/api/watchlists")
async def create_watchlist(watchlist: WatchlistCreate):
    conn = sqlite3.connect("market_terminal.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO watchlists (name) VALUES (?)", (watchlist.name,))
    watchlist_id = cursor.lastrowid
    for symbol in watchlist.symbols:
        cursor.execute(
            "INSERT INTO watchlist_stocks (watchlist_id, symbol) VALUES (?, ?)",
            (watchlist_id, symbol.upper()),
        )
    conn.commit()
    conn.close()
    return {"id": watchlist_id, "message": f"Watchlist '{watchlist.name}' created"}

@app.post("/api/watchlists/{watchlist_id}/add-stock")
async def add_stock(watchlist_id: int, stock: StockAdd):
    conn = sqlite3.connect("market_terminal.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM watchlist_stocks WHERE watchlist_id = ? AND symbol = ?",
        (watchlist_id, stock.symbol.upper()),
    )
    if cursor.fetchone()[0] > 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Stock already exists in watchlist")
    cursor.execute(
        "INSERT INTO watchlist_stocks (watchlist_id, symbol) VALUES (?, ?)",
        (watchlist_id, stock.symbol.upper()),
    )
    conn.commit()
    conn.close()
    return {"message": f"Stock {stock.symbol} added to watchlist"}

@app.delete("/api/watchlists/{watchlist_id}/stocks/{symbol}")
async def remove_stock(watchlist_id: int, symbol: str):
    conn = sqlite3.connect("market_terminal.db")
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM watchlist_stocks WHERE watchlist_id = ? AND symbol = ?",
        (watchlist_id, symbol.upper()),
    )
    conn.commit()
    conn.close()
    return {"message": f"Stock {symbol} removed from watchlist"}

@app.get("/api/watchlists/{watchlist_id}/stocks")
async def get_watchlist_stocks(watchlist_id: int):
    conn = sqlite3.connect("market_terminal.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT symbol FROM watchlist_stocks WHERE watchlist_id = ? ORDER BY added_at",
        (watchlist_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    stocks = []
    for (symbol,) in rows:
        live_data = angel_api.get_ltp(symbol)
        if live_data:
            stocks.append({
                "symbol": symbol,
                "ltp": live_data["ltp"],
                "change": live_data["change"],
                "changePercent": live_data["changePercent"],
                "volume": 0,
                "sector": "Live Data",
            })
        else:
            # mock data fallback
            base = 1000 + hash(symbol) % 3000
            delta = (hash(symbol) % 200) - 100
            pct = delta / max(base, 1) * 100 
            stocks.append({
                "symbol": symbol,
                "ltp": base + delta,
                "change": delta,
                "changePercent": round(pct, 2),
                "volume": 0,
                "sector": "Technology",
            })

    return {"stocks": stocks}

@app.get("/api/market/indices")
async def get_market_indices():
    return {
        "indices": [
            {"name": "NIFTY 50", "value": 25674.25, "change": 156.80, "changePercent": 0.61},
            {"name": "SENSEX", "value": 84023.69, "change": 525.42, "changePercent": 0.63},
            {"name": "BANK NIFTY", "value": 54258.75, "change": -125.30, "changePercent": -0.23},
        ]
    }

@app.get("/health")
async def health_check():
    status = "Connected" if angel_api.jwt_token else "Mock"
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "angel_one_status": status,
    }

app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
