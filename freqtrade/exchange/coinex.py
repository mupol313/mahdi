import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import math
import ccxt
import ccxt.async_support as ccxt_async
from datetime import datetime, timedelta, timezone
from typing import Any, Coroutine, Dict, List, Literal, Optional, Tuple, Union
from copy import deepcopy

from freqtrade.constants import BuySell
from freqtrade.enums import CandleType, MarginMode, PriceType, TradingMode
from freqtrade.exceptions import DDosProtection, ExchangeError, OperationalException, TemporaryError
from freqtrade.exchange import Exchange
from freqtrade.exchange.common import retrier
from freqtrade.util.datetime_helpers import dt_now, dt_ts
from freqtrade.exceptions import (DDosProtection, ExchangeError, InsufficientFundsError,InvalidOrderException, OperationalException, PricingError,RetryableOrderError, TemporaryError)
from freqtrade.exchange.exchange_utils import (ROUND, ROUND_DOWN, ROUND_UP, CcxtModuleType,amount_to_contract_precision, amount_to_contracts,amount_to_precision, contracts_to_amount,date_minus_candles, is_exchange_known_ccxt,market_is_active, price_to_precision,timeframe_to_minutes, timeframe_to_msecs,timeframe_to_next_date, timeframe_to_prev_date,timeframe_to_seconds)
from freqtrade.exchange.common import (API_FETCH_ORDER_RETRY_COUNT, remove_exchange_credentials,retrier, retrier_async)
from freqtrade.exchange.types import OHLCVResponse, OrderBook, Ticker, Tickers
from freqtrade.constants import (DEFAULT_AMOUNT_RESERVE_PERCENT, NON_OPEN_EXCHANGE_STATES, BidAsk,BuySell, Config, EntryExit, ExchangeConfig,ListPairsWithTimeframes, MakerTaker, OBLiteral, PairWithTimeframe)
from freqtrade.misc import (chunks, deep_merge_dicts, file_dump_json, file_load_json,safe_value_fallback2)
from freqtrade.plugins.pairlist.pairlist_helpers import expand_pairlist
from freqtrade.util import dt_from_ts, dt_now
from freqtrade.util.datetime_helpers import dt_humanize, dt_ts
from freqtrade.data.converter import clean_ohlcv_dataframe, ohlcv_to_dataframe, trades_dict_to_list

logger = logging.getLogger(__name__)

class Coinex(Exchange):

    _ft_has: Dict = {
        "ohlcv_candle_limit": 1000,
        "ohlcv_has_history": True
    }

    _ft_has_futures: Dict= {
        "ohlcv_has_history": True,
        "order_time_in_force": ['GTC', 'IOC', 'FOK'],
        "mark_ohlcv_timeframe": "4h",
        "funding_fee_timeframe": "8h",
        "stoploss_on_exchange": True,
        "stop_price_param": "stopLossPrice",
        "stop_price_prop": "stopLossPrice",

        "stoploss_order_types": {"limit": "limit", "market": "market"},

        "stop_price_type_field": "triggerPrice",

        "stop_price_type_value_mapping": {
                                    PriceType.LAST: "LastPrice",
                                    PriceType.MARK: "MarkPrice",
                                    PriceType.INDEX: "IndexPrice",
        }}

    _supported_trading_mode_margin_pairs: List[Tuple[TradingMode, MarginMode]] = [(TradingMode.FUTURES, MarginMode.ISOLATED)]



    @property
    def _ccxt_config(self) -> Dict:

        config = {}
        config.update({"defaultType": self._ft_has["ccxt_futures_name"]})
        config.update(super()._ccxt_config)
        return config



    def ohlcv_candle_limit(self, timeframe: str, candle_type: CandleType, since_ms: Optional[int] = None) -> int:

        candle_limit = 1000
        return candle_limit


    def _set_leverage(self,leverage: float,pair: Optional[str] = None,accept_fail: bool = False):

        if self._config['dry_run']:
            return

        ISOLATED = 1
        params = {'position_type': ISOLATED}
        if not self.markets:
            self._load_markets()
        
        info = self.markets[pair]
        if 'info' in info and 'leverage' in info['info']:
            lev_list = info['info']['leverage']
            leverage = math.floor(leverage)
            if leverage in lev_list:
                try:
                    res = self._api.set_leverage(leverage= leverage, symbol= pair , params= params )
                    self._log_exchange_response('set_leverage', res)
                    logger.info(f'Set leverage to {leverage} on {pair}')

                except ccxt.DDoSProtection as e:
                    raise DDosProtection(e) from e
                except (ccxt.BadRequest, ccxt.InsufficientFunds) as e:
                    if not accept_fail:
                        raise TemporaryError(f'Could not set leverage due to {e.__class__.__name__}. Message: {e}') from e
                except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                    raise TemporaryError(f'Could not set leverage due to {e.__class__.__name__}. Message: {e}') from e
                except ccxt.BaseError as e:
                    raise OperationalException(e) from e
            else:
                leverage = 1
                res = self._api.set_leverage(leverage= leverage, symbol= pair , params= params )
                self._log_exchange_response('set_leverage', res)
                logger.info(f'there is no leverage {leverage} on {pair}. Set leverage to {leverage} on {pair}')


    @retrier
    def get_tickers(self, symbols: Optional[List[str]] = None, cached: bool = False) -> Tickers:

        tickers: Tickers
        if cached:
            with self._cache_lock:
                tickers = self._fetch_tickers_cache.get('fetch_tickers') 

            if tickers:   #^ if tickers there is in cach
                for pair, data in tickers.items():
                    if data['quoteVolume'] is None:
                        quote_volume = data['baseVolume'] * data['last']
                        data['quoteVolume'] = quote_volume
                return tickers

        try:
            tickers = self._api.fetch_tickers(symbols)
            for pair, data in tickers.items():
                    if data['quoteVolume'] is None:
                        quote_volume = data['baseVolume'] * data['last']
                        data['quoteVolume'] = quote_volume

            with self._cache_lock:
                self._fetch_tickers_cache['fetch_tickers'] = tickers
            return tickers

        except ccxt.NotSupported as e:
            raise OperationalException(f'Exchange {self._api.name} does not support fetching tickers in batch. '
                                        f'Message: {e}') from e
        except ccxt.BadSymbol as e:
            logger.warning(f"Could not load tickers due to {e.__class__.__name__}. Message: {e} .Reloading markets.")
            self.reload_markets(True)
            raise TemporaryError from e
        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            raise TemporaryError(f'Could not load tickers due to {e.__class__.__name__}. Message: {e}') from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e



    @retrier
    def fetch_ticker(self, pair: str) -> Ticker:
        try:
            if (pair not in self.markets or self.markets[pair].get('active', False) is False):
                raise ExchangeError(f"Pair {pair} not available")

            data: Ticker = self._api.fetch_ticker(pair)
            quote_volume = data['baseVolume'] * data['last']
            data['quoteVolume'] = quote_volume
            return data

        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            raise TemporaryError(f'Could not load ticker due to {e.__class__.__name__}. Message: {e}') from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e





    # def create_order(self,*,pair: str,ordertype: str,side: BuySell,amount: float,rate: float,leverage: float,reduceOnly: bool = False,time_in_force: str = 'GTC') -> Dict:

    #     if self._config['dry_run']:
    #         dry_order = self.create_dry_run_order(pair, ordertype, side, amount, self.price_to_precision(pair, rate), leverage)
    #         return dry_order

    #     params = self._get_params(side, ordertype, leverage, reduceOnly, time_in_force)

    #     try:
    #         # Set the precision for amount and price(rate) as accepted by the exchange
    #         amount = self.amount_to_precision(pair, self._amount_to_contracts(pair, amount))
    #         needs_price = self._order_needs_price(ordertype)
    #         rate_for_order = self.price_to_precision(pair, rate) if needs_price else None

    #         if not reduceOnly:
    #             self._lev_prep(pair, leverage, side)

    #         order = self._api.create_order(symbol= pair,type= ordertype,side= side,amount= amount,price= rate_for_order,params= params)

    #         #% dont create order
    #         if order.get('status') is None:
    #             order['status'] = 'open'

    #         if order.get('type') is None:
    #             order['type'] = ordertype

    #         self._log_exchange_response('create_order', order)

    #         order = self._order_contracts_to_amount(order)

    #         return order

    #     except ccxt.InsufficientFunds as e:
    #         raise InsufficientFundsError(f'Insufficient funds to create {ordertype} {side} order on market {pair}. '
    #                                         f'Tried to {side} amount {amount} at rate {rate}.'
    #                                         f'Message: {e}') from e
    #     except ccxt.InvalidOrder as e:
    #         raise InvalidOrderException(f'Could not create {ordertype} {side} order on market {pair}. '
    #                                     f'Tried to {side} amount {amount} at rate {rate}. '
    #                                     f'Message: {e}') from e
    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not place {side} order due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e


    # def _get_stop_params(self, side: BuySell, ordertype: str, stop_price: float) -> Dict:
    #     '''   this function must be test'''
    #     params = self._params.copy()
    #     # Verify if stopPrice works for your exchange, else configure stop_price_param
    #     params.update({self._ft_has['stop_price_param']: stop_price})
    #     return params


    # @retrier(retries=0)
    # def create_stoploss(self, pair: str, amount: float, stop_price: float, order_types: Dict, side: BuySell, leverage: float) -> Dict:

    #     if not self._ft_has['stoploss_on_exchange']:
    #         raise OperationalException(f"stoploss is not implemented for {self.name}.")

    #     user_order_type = order_types.get('stoploss', 'market')
    #     ordertype, user_order_type = self._get_stop_order_type(user_order_type)
    #     round_mode = ROUND_DOWN if side == 'buy' else ROUND_UP
    #     stop_price_norm = self.price_to_precision(pair, stop_price, rounding_mode=round_mode)
    #     limit_rate = None

    #     if user_order_type == 'limit':
    #         limit_rate = self._get_stop_limit_rate(stop_price, order_types, side)
    #         limit_rate = self.price_to_precision(pair, limit_rate, rounding_mode=round_mode)

    #     if self._config['dry_run']:
    #         dry_order = self.create_dry_run_order(pair,ordertype,side,amount,stop_price_norm,stop_loss=True,leverage=leverage)
    #         return dry_order

    #     try:
    #         params = self._get_stop_params(side=side, ordertype=ordertype,stop_price=stop_price_norm)

    #         if self.trading_mode == TradingMode.FUTURES:
    #             params['reduceOnly'] = True

    #             if 'stoploss_price_type' in order_types and 'stop_price_type_field' in self._ft_has:
    #                 price_type = self._ft_has['stop_price_type_value_mapping'][order_types.get('stoploss_price_type', PriceType.LAST)]
    #                 params[self._ft_has['stop_price_type_field']] = price_type

    #         amount = self.amount_to_precision(pair, self._amount_to_contracts(pair, amount))

    #         # Prepare leverage
    #         self._lev_prep(pair, leverage, side, accept_fail=True)


    #         # Create stoploss order
    #         order = self._api.create_order(symbol=pair, type=ordertype, side=side, amount=amount, price=limit_rate, params=params)

    #         self._log_exchange_response('create_stoploss_order', order)

    #         order = self._order_contracts_to_amount(order)

    #         logger.info(f"stoploss {user_order_type} order added for {pair}. "
    #                     f"stop price: {stop_price}. limit: {limit_rate}")

    #         return order

    #     except ccxt.InsufficientFunds as e:
    #         raise InsufficientFundsError(f'Insufficient funds to create {ordertype} {side} order on market {pair}. '
    #                                     f'Tried to {side} amount {amount} at rate {limit_rate} with '
    #                                     f'stop-price {stop_price_norm}. Message: {e}') from e

    #     except (ccxt.InvalidOrder, ccxt.BadRequest) as e:
    #         raise InvalidOrderException(f'Could not create {ordertype} {side} order on market {pair}. '
    #                                     f'Tried to {side} amount {amount} at rate {limit_rate} with '
    #                                     f'stop-price {stop_price_norm}. Message: {e}') from e
    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f"Could not place stoploss order due to {e.__class__.__name__}. "
    #                             f"Message: {e}") from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e



    # @retrier(retries=API_FETCH_ORDER_RETRY_COUNT)
    # def fetch_order(self, order_id: str, pair: str, params: Dict = {}) -> Dict:

    #     if self._config['dry_run']:
    #         return self.fetch_dry_run_order(order_id)

    #     try:
    #         order = self._api.fetch_order(order_id, pair, params=params)
    #         self._log_exchange_response('fetch_order', order)
    #         order = self._order_contracts_to_amount(order)

    #         return order

    #     except ccxt.OrderNotFound as e:
    #         raise RetryableOrderError(f'Order not found (pair: {pair} id: {order_id}). Message: {e}') from e
    #     except ccxt.InvalidOrder as e:
    #         raise InvalidOrderException(f'Tried to get an invalid order (pair: {pair} id: {order_id}). Message: {e}') from e
    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not get order due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e


    # @retrier
    # def cancel_order(self, order_id: str, pair: str, params: Dict = {}) -> Dict:

    #     if self._config['dry_run']:
    #         try:
    #             order = self.fetch_dry_run_order(order_id)

    #             order.update({'status': 'canceled', 'filled': 0.0, 'remaining': order['amount']})
    #             return order

    #         except InvalidOrderException:
    #             return {}

    #     try:
    #         order = self._api.cancel_order(order_id, pair, params=params)
    #         self._log_exchange_response('cancel_order', order)

    #         order = self._order_contracts_to_amount(order)

    #         return order

    #     except ccxt.InvalidOrder as e:
    #         raise InvalidOrderException(f'Could not cancel order. Message: {e}') from e
    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not cancel order due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e


    # @retrier
    # def fetch_positions(self, pair: Optional[str] = None) -> List[Dict]:

    #     if self._config['dry_run'] or self.trading_mode != TradingMode.FUTURES:
    #         return []

    #     try:
    #         symbols = []
    #         if pair:
    #             symbols.append(pair)
    #         positions: List[Dict] = self._api.fetch_positions(symbols)
    #         self._log_exchange_response('fetch_positions', positions)

    #         return positions

    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not get positions due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e


    def _fetch_orders_emulate(self, pair: str, since_ms: int) -> List[Dict]:
        orders = []
        if self.exchange_has('fetchClosedOrders'):
            orders = self._api.fetch_closed_orders(pair, since=since_ms)
            if self.exchange_has('fetchOpenOrders'):
                orders_open = self._api.fetch_open_orders(pair, since=since_ms)
                orders.extend(orders_open)
        return orders


    # @retrier(retries=0)
    # def fetch_orders(self, pair: str, since: datetime, params: Optional[Dict] = None) -> List[Dict]:

    #     if self._config['dry_run']:
    #         return []

    #     try:
    #         since_ms = int((since.timestamp() - 10) * 1000)

    #         if self.exchange_has('fetchOrders'):
    #             if not params:
    #                 params = {}
    #             try:
    #                 orders: List[Dict] = self._api.fetch_orders(pair, since=since_ms, params=params)
    #             except ccxt.NotSupported:
    #                 orders = self._fetch_orders_emulate(pair, since_ms)

    #         else:
    #             orders = self._fetch_orders_emulate(pair, since_ms)
    #         self._log_exchange_response('fetch_orders', orders)
    #         orders = [self._order_contracts_to_amount(o) for o in orders]
    #         return orders

    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not fetch positions due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e


    # @retrier
    # def fetch_trading_fees(self) -> Dict[str, Any]:

    #     if (self._config['dry_run'] or self.trading_mode != TradingMode.FUTURES  or not self.exchange_has('fetchTradingFees')):
    #         return {}

    #     try:
    #         trading_fees: Dict[str, Any] = self._api.fetch_trading_fees()
    #         self._log_exchange_response('fetch_trading_fees', trading_fees)
    #         return trading_fees

    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not fetch trading fees due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e


    # @retrier
    # def fetch_l2_order_book(self, pair: str, limit: int = 100) -> OrderBook:

    #     limit1 = self.get_next_limit_in_list(limit, self._ft_has['l2_limit_range'],self._ft_has['l2_limit_range_required'])

    #     try:
    #         return self._api.fetch_l2_order_book(pair, limit1)

    #     except ccxt.NotSupported as e:
    #         raise OperationalException(f'Exchange {self._api.name} does not support fetching order book.'
    #                                     f'Message: {e}') from e
    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not get order book due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e



    # @retrier
    # def get_trades_for_order(self, order_id: str, pair: str, since: datetime,params: Optional[Dict] = None) -> List:
    #     """
    #     Fetch Orders using the "fetch_my_trades" endpoint and filter them by order-id.
    #     The "since" argument passed in is coming from the database and is in UTC,
    #     as timezone-native datetime object.
    #     From the python documentation:
    #         > Naive datetime instances are assumed to represent local time
    #     Therefore, calling "since.timestamp()" will get the UTC timestamp, after applying the
    #     transformation from local timezone to UTC.
    #     This works for timezones UTC+ since then the result will contain trades from a few hours
    #     instead of from the last 5 seconds, however fails for UTC- timezones,
    #     since we're then asking for trades with a "since" argument in the future.
    #     """
    #     if self._config['dry_run']:
    #         return []
    #     if not self.exchange_has('fetchMyTrades'):
    #         return []

    #     try:
    #         # Allow 5s offset to catch slight time offsets (discovered in #1185)
    #         # since needs to be int in milliseconds
    #         _params = params if params else {}

    #         my_trades = self._api.fetch_my_trades(pair, int((since.replace(tzinfo=timezone.utc).timestamp() - 5) * 1000),params=_params)

    #         matched_trades = [trade for trade in my_trades if trade['order'] == order_id]

    #         self._log_exchange_response('get_trades_for_order', matched_trades)

    #         matched_trades = self._trades_contracts_to_amount(matched_trades)

    #         return matched_trades

    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not get trades due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e


    # @retrier
    # def get_fee(self, symbol: str, type: str = '', side: str = '', amount: float = 1,price: float = 1, taker_or_maker: MakerTaker = 'maker') -> float:
    #     """
    #     Retrieve fee from exchange
    #     """
    #     if type and type == 'market':
    #         taker_or_maker = 'taker'
    #     try:
    #         if self._config['dry_run'] and self._config.get('fee', None) is not None:
    #             return self._config['fee']
    #         # validate that markets are loaded before trying to get fee

    #         if self._api.markets is None or len(self._api.markets) == 0:
    #             self._api.load_markets(params={})

    #         return self._api.calculate_fee(symbol=symbol, type=type, side=side, amount=amount,price=price, takerOrMaker=taker_or_maker)['rate']

    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(
    #             f'Could not get fee info due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e




    # @retrier_async
    # async def _async_fetch_trades(self, pair: str,since: Optional[int] = None,params: Optional[dict] = None) -> Tuple[List[List], Any]:
    #     """
    #     Asyncronously gets trade history using fetch_trades.
    #     Handles exchange errors, does one call to the exchange.
    #     """
    #     try:
    #         # fetch trades asynchronously
    #         if params:
    #             logger.debug("Fetching trades for pair %s, params: %s ", pair, params)
    #             trades = await self._api_async.fetch_trades(pair, params=params, limit=1000)
    #         else:
    #             logger.debug(
    #                 "Fetching trades for pair %s, since %s %s...",
    #                 pair, since,
    #                 '(' + dt_from_ts(since).isoformat() + ') ' if since is not None else '')

    #             trades = await self._api_async.fetch_trades(pair, since=since, limit=1000)

    #         trades = self._trades_contracts_to_amount(trades)
    #         pagination_value = self._get_trade_pagination_next_value(trades)
    #         return trades_dict_to_list(trades), pagination_value

    #     except ccxt.NotSupported as e:
    #         raise OperationalException(f'Exchange {self._api.name} does not support fetching historical trade data.'
    #             f'Message: {e}') from e
    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not load trade history due to {e.__class__.__name__}. '
    #                             f'Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(f'Could not fetch trade data. Msg: {e}') from e



    # async def _fetch_funding_rate_history(self,pair: str,timeframe: str,limit: int,since_ms: Optional[int] = None) -> List[List]:

    #     data = await self._api_async.fetch_funding_rate_history(pair, since=since_ms,limit=limit)
    #     # Convert funding rate to candle pattern
    #     data = [[x['timestamp'], x['fundingRate'], 0, 0, 0, 0] for x in data]
    #     return data


    # @retrier
    # def _get_funding_fees_from_exchange(self, pair: str, since: Union[datetime, int]) -> float:
    #     """
    #     #!  frist function for get funding rate
    #     Returns the sum of all funding fees that were exchanged for a pair within a timeframe
    #     Dry-run handling happens as part of _calculate_funding_fees.
    #     """
    #     if not self.exchange_has("fetchFundingHistory"):
    #         raise OperationalException(f"fetch_funding_history() is not available using {self.name}")

    #     if type(since) is datetime:
    #         since = int(since.timestamp()) * 1000

    #     try:
    #         funding_history = self._api.fetch_funding_history(symbol=pair,since=since)
    #         self._log_exchange_response('funding_history', funding_history,add_info=f"pair: {pair}, since: {since}")
    #         return sum(fee['amount'] for fee in funding_history)

    #     except ccxt.DDoSProtection as e:
    #         raise DDosProtection(e) from e
    #     except (ccxt.NetworkError, ccxt.ExchangeError) as e:
    #         raise TemporaryError(f'Could not get funding fees due to {e.__class__.__name__}. Message: {e}') from e
    #     except ccxt.BaseError as e:
    #         raise OperationalException(e) from e




