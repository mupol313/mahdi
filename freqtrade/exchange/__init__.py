from freqtrade.exchange.common import remove_exchange_credentials, MAP_EXCHANGE_CHILDCLASS
from freqtrade.exchange.exchange import Exchange
# from freqtrade.exchange.binance import Binance
# from freqtrade.exchange.bybit import Bybit
from freqtrade.exchange.exchange_utils import (ROUND_DOWN, ROUND_UP, 
                                                amount_to_contract_precision,
                                                amount_to_contracts, 
                                                amount_to_precision,
                                                available_exchanges, 
                                                ccxt_exchanges,
                                                contracts_to_amount, 
                                                date_minus_candles,
                                                is_exchange_known_ccxt, 
                                                list_available_exchanges,
                                                market_is_active, 
                                                price_to_precision,
                                                timeframe_to_minutes, 
                                                timeframe_to_msecs,
                                                timeframe_to_next_date, 
                                                timeframe_to_prev_date,
                                                timeframe_to_resample_freq, 
                                                timeframe_to_seconds,
                                                validate_exchange)
# from freqtrade.exchange.gate import Gate
# from freqtrade.exchange.kraken import Kraken
from freqtrade.exchange.okx import Okx
from freqtrade.exchange.coinex import Coinex
