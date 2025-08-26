from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sqlite3
import json
import os
from datetime import datetime
from typing import List
import uvicorn
from dotenv import load_dotenv
import pyotp
from smartapi import SmartConnect

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
        self.api_secret = os.getenv('ANGEL_API_SECRET') 
        self.client_code = os.getenv('ANGEL_CLIENT_CODE')
        self.password = os.getenv('ANGEL_PASSWORD')
        self.totp_secret = os.getenv('ANGEL_TOTP_SECRET')
        
        if self.api_key:
            self.smart_api = SmartConnect(api_key=self.api_key)
            self.access_token = None
        else:
            self.smart_api = None
        
    def generate_session(self):
        if not self.smart_api:
            return False
            
        try:
            totp = pyotp.TOTP(self.totp_secret)
            totp_code = totp.now()
            
            data = self.smart_api.generateSession(
                clientCode=self.client_code,
                password=self.password,
                totp=totp_code
            )
            
            if data['status']:
                self.access_token = data['data']['jwtToken']
                print("✅ Angel One session created successfully")
                return True
            else:
                print(f"❌ Session failed: {data['message']}")
                return False
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            return False
    
    def get_ltp(self, symbol):
        if not self.smart_api or not self.access_token:
            return None
            
        try:
            # Common symbol tokens (you can expand this)
            symbol_tokens = {
                "RELIANCE": "2885",
                "TCS": "11536", 
                "INFY": "1594",
                "HDFCBANK": "1333",
                "ITC": "424",
                "SBIN": "3045",
                "BHARTIARTL": "10604",
                "ASIANPAINT": "236",
                "MARUTI": "10999",
                "KOTAKBANK": "492"
            }
            
            token = symbol_tokens.get(symbol, "26009")  # Default token
            
            data = self.smart_api.ltpData("NSE", symbol, token)
            
            if data['status']:
                return {
                    'symbol': symbol,
                    'ltp': data['data']['ltp'],
                    'change': data['data'].get('change', 0),
                    'changePercent': data['data'].get('pChange', 0)
                }
            return None
        except Exception as e:
            print(f"❌ Price fetch error for {symbol}: {str(e)}")
            return None

# Initialize Angel One API
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
            FOREIGN KEY (watchlist_id) REFERENCES watchlists (id)
        )
    ''')
    
    # Default watchlists
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (1, 'My Portfolio')")
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (2, 'Growth Stocks')")
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (3, 'Value Picks')")
    
    # Add some default stocks
    default_stocks = ['RELIANCE', 'TCS', 'INFY', 'EMS', 'BIKAJI']
    for stock in default_stocks:
        cursor.execute(
            "INSERT OR IGNORE INTO watchlist_stocks (watchlist_id, symbol) VALUES (1, ?)",
            (stock,)
        )
    
    conn.commit()
    conn.close()

@app.on_event("startup")
async def startup_event():
    init_db()
    if angel_api.api_key:
        angel_api.generate_session()

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
    
    watchlists = []
    for row in cursor.fetchall():
        watchlists.append({
            'id': row[0],
            'name': row[1],
            'stock_count': row[2]
        })
    
    conn.close()
    return {"watchlists": watchlists}

@app.post("/api/watchlists")
async def create_watchlist(watchlist: WatchlistCreate):
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    
    cursor.execute("INSERT INTO watchlists (name) VALUES (?)", (watchlist.name,))
    watchlist_id = cursor.lastrowid
    
    for symbol in watchlist.symbols:
        cursor.execute(
            "INSERT INTO watchlist_stocks (watchlist_id, symbol) VALUES (?, ?)",
            (watchlist_id, symbol.upper())
        )
    
    conn.commit()
    conn.close()
    
    return {"id": watchlist_id, "message": f"Watchlist '{watchlist.name}' created"}

@app.post("/api/watchlists/{watchlist_id}/add-stock")
async def add_stock(watchlist_id: int, stock: StockAdd):
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT COUNT(*) FROM watchlist_stocks WHERE watchlist_id = ? AND symbol = ?",
        (watchlist_id, stock.symbol.upper())
    )
    
    if cursor.fetchone()[0] > 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Stock already exists in watchlist")
    
    cursor.execute(
        "INSERT INTO watchlist_stocks (watchlist_id, symbol) VALUES (?, ?)",
        (watchlist_id, stock.symbol.upper())
    )
    
    conn.commit()
    conn.close()
    
    return {"message": f"Stock {stock.symbol} added to watchlist"}

@app.delete("/api/watchlists/{watchlist_id}/stocks/{symbol}")
async def remove_stock(watchlist_id: int, symbol: str):
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "DELETE FROM watchlist_stocks WHERE watchlist_id = ? AND symbol = ?",
        (watchlist_id, symbol.upper())
    )
    
    conn.commit()
    conn.close()
    
    return {"message": f"Stock {symbol} removed from watchlist"}

@app.get("/api/watchlists/{watchlist_id}/stocks")
async def get_watchlist_stocks(watchlist_id: int):
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT symbol FROM watchlist_stocks WHERE watchlist_id = ? ORDER BY added_at",
        (watchlist_id,)
    )
    
    stocks = []
    for row in cursor.fetchall():
        symbol = row[0]
        
        # Try to get real data from Angel One
        real_data = angel_api.get_ltp(symbol) if angel_api else None
        
        if real_data:
            stocks.append({
                'symbol': symbol,
                'ltp': real_data['ltp'],
                'change': real_data['change'],
                'changePercent': real_data['changePercent'],
                'volume': 0,
                'sector': 'Live Data'
            })
        else:
            # Fallback to mock data
            stocks.append({
                'symbol': symbol,
                'ltp': 1000 + hash(symbol) % 2000,
                'change': (hash(symbol) % 200) - 100,
                'changePercent': ((hash(symbol) % 200) - 100) / 10,
                'volume': (hash(symbol) % 1000000) + 100000,
                'sector': 'Technology'
            })
    
    conn.close()
    return {"stocks": stocks}

@app.get("/api/market/indices")
async def get_market_indices():
    # Try to get real indices data
    indices_data = []
    
    if angel_api and angel_api.access_token:
        try:
            # Get NIFTY 50 data
            nifty_data = angel_api.smart_api.ltpData("NSE", "NIFTY", "99926000")
            if nifty_data['status']:
                indices_data.append({
                    "name": "NIFTY 50",
                    "value": nifty_data['data']['ltp'],
                    "change": nifty_data['data'].get('change', 0),
                    "changePercent": nifty_data['data'].get('pChange', 0)
                })
        except:
            pass
    
    # Fallback to mock data if real data not available
    if not indices_data:
        indices_data = [
            {"name": "NIFTY 50", "value": 19674.25, "change": 156.80, "changePercent": 0.80},
            {"name": "SENSEX", "value": 66023.69, "change": 525.42, "changePercent": 0.80},
            {"name": "BANK NIFTY", "value": 44258.75, "change": -125.30, "changePercent": -0.28}
        ]
    
    return {"indices": indices_data}

@app.get("/health")
async def health_check():
    angel_status = "Connected" if (angel_api and angel_api.access_token) else "Mock Data"
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "angel_one_status": angel_status
    }

# Serve static files (frontend)
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
