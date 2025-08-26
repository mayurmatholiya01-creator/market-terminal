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

load_dotenv()

app = FastAPI(title="Market Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (1, 'My Portfolio')")
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (2, 'Growth Stocks')")
    cursor.execute("INSERT OR IGNORE INTO watchlists (id, name) VALUES (3, 'Value Picks')")
    
    conn.commit()
    conn.close()

@app.on_event("startup")
async def startup_event():
    init_db()

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

@app.get("/api/watchlists/{watchlist_id}/stocks")
async def get_watchlist_stocks(watchlist_id: int):
    conn = sqlite3.connect('market_terminal.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT symbol FROM watchlist_stocks WHERE watchlist_id = ? ORDER BY added_at",
