from typing import Dict, List, Optional, Tuple, TypedDict

from freqtrade.enums import CandleType


class Ticker(TypedDict):
    symbol: str
    ask: Optional[float]
    askVolume: Optional[float]
    bid: Optional[float]
    bidVolume: Optional[float]
    last: Optional[float]
    quoteVolume: Optional[float]
    baseVolume: Optional[float]

class OrderBook(TypedDict):
    symbol: str
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]
    timestamp: Optional[int]
    datetime: Optional[str]
    nonce: Optional[int]

Tickers = Dict[str, Ticker]
OHLCVResponse = Tuple[str, str, CandleType, List, bool]
