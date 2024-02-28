from datetime import datetime, timedelta, timezone
from math import ceil, floor
from typing import Any, Dict, List, Optional, Tuple
import ccxt
from ccxt import (DECIMAL_PLACES, ROUND, ROUND_DOWN, ROUND_UP, SIGNIFICANT_DIGITS, TICK_SIZE,TRUNCATE, decimal_to_precision)

from freqtrade.exchange.common import (BAD_EXCHANGES, EXCHANGE_HAS_OPTIONAL, EXCHANGE_HAS_REQUIRED,SUPPORTED_EXCHANGES)
from freqtrade.types import ValidExchangesType
from freqtrade.util import FtPrecise
from freqtrade.util.datetime_helpers import dt_from_ts, dt_ts

CcxtModuleType = Any

def is_exchange_known_ccxt(exchange_name: str, ccxt_module: Optional[CcxtModuleType] = None) -> bool:
    return exchange_name in ccxt_exchanges(ccxt_module)


def ccxt_exchanges(ccxt_module: Optional[CcxtModuleType] = None) -> List[str]:
    return ccxt_module.exchanges if ccxt_module is not None else ccxt.exchanges


def available_exchanges(ccxt_module: Optional[CcxtModuleType] = None) -> List[str]:
    exchanges = ccxt_exchanges(ccxt_module)
    return [x for x in exchanges if validate_exchange(x)[0]]



def validate_exchange(exchange: str) -> Tuple[bool, str]:
    ex_mod = getattr(ccxt, exchange.lower())()
    if not ex_mod or not ex_mod.has:
        return False, ''
    missing = [k for k in EXCHANGE_HAS_REQUIRED if ex_mod.has.get(k) is not True]
    if missing:
        return False, f"missing: {', '.join(missing)}"

    missing_opt = [k for k in EXCHANGE_HAS_OPTIONAL if not ex_mod.has.get(k)]

    if exchange.lower() in BAD_EXCHANGES:
        return False, BAD_EXCHANGES.get(exchange.lower(), '')
    if missing_opt:
        return True, f"missing opt: {', '.join(missing_opt)}"
    return True, ''



def _build_exchange_list_entry(exchange_name: str, exchangeClasses: Dict[str, Any]) -> ValidExchangesType:
    valid, comment = validate_exchange(exchange_name)
    result: ValidExchangesType = {
        'name': exchange_name,
        'valid': valid,
        'supported': exchange_name.lower() in SUPPORTED_EXCHANGES,
        'comment': comment,
        'trade_modes': [{'trading_mode': 'spot', 'margin_mode': ''}],
    }

    if resolved := exchangeClasses.get(exchange_name.lower()):
        supported_modes = [{'trading_mode': 'spot', 'margin_mode': ''}] + [{'trading_mode': tm.value, 'margin_mode': mm.value} for tm, mm in resolved['class']._supported_trading_mode_margin_pairs]
        result.update({'trade_modes': supported_modes})
    return result



def list_available_exchanges(all_exchanges: bool) -> List[ValidExchangesType]:

    exchanges = ccxt_exchanges() if all_exchanges else available_exchanges()
    from freqtrade.resolvers.exchange_resolver import ExchangeResolver

    subclassed = {e['name'].lower(): e for e in ExchangeResolver.search_all_objects({}, False)}
    exchanges_valid: List[ValidExchangesType] = [_build_exchange_list_entry(e, subclassed) for e in exchanges]
    return exchanges_valid



def timeframe_to_seconds(timeframe: str) -> int:
    return ccxt.Exchange.parse_timeframe(timeframe)


def timeframe_to_minutes(timeframe: str) -> int:
    return ccxt.Exchange.parse_timeframe(timeframe) // 60


def timeframe_to_msecs(timeframe: str) -> int:
    return ccxt.Exchange.parse_timeframe(timeframe) * 1000


def timeframe_to_resample_freq(timeframe: str) -> str:

    if timeframe == '1y':
        return '1YS'
    timeframe_seconds = timeframe_to_seconds(timeframe)
    timeframe_minutes = timeframe_seconds // 60
    resample_interval = f'{timeframe_seconds}s'
    if 10000 < timeframe_minutes < 43200:
        resample_interval = '1W-MON'
    elif timeframe_minutes >= 43200 and timeframe_minutes < 525600:
        resample_interval = f'{timeframe}S'
    elif timeframe_minutes > 43200:
        resample_interval = timeframe
    return resample_interval



def timeframe_to_prev_date(timeframe: str, date: Optional[datetime] = None) -> datetime:

    if not date:
        date = datetime.now(timezone.utc)
    new_timestamp = ccxt.Exchange.round_timeframe(timeframe, dt_ts(date), ROUND_DOWN) // 1000
    return dt_from_ts(new_timestamp)



def timeframe_to_next_date(timeframe: str, date: Optional[datetime] = None) -> datetime:
    if not date:
        date = datetime.now(timezone.utc)
    new_timestamp = ccxt.Exchange.round_timeframe(timeframe, dt_ts(date), ROUND_UP) // 1000
    return dt_from_ts(new_timestamp)



def date_minus_candles(timeframe: str, candle_count: int, date: Optional[datetime] = None) -> datetime:

    if not date:
        date = datetime.now(timezone.utc)

    tf_min = timeframe_to_minutes(timeframe)
    new_date = timeframe_to_prev_date(timeframe, date) - timedelta(minutes=tf_min * candle_count)
    return new_date


def market_is_active(market: Dict) -> bool:
    return market.get('active', True) is not False


def amount_to_contracts(amount: float, contract_size: Optional[float]) -> float:

    if contract_size and contract_size != 1:
        return float(FtPrecise(amount) / FtPrecise(contract_size))
    else:
        return amount


def contracts_to_amount(num_contracts: float, contract_size: Optional[float]) -> float:

    if contract_size and contract_size != 1:
        return float(FtPrecise(num_contracts) * FtPrecise(contract_size))
    else:
        return num_contracts


def amount_to_precision(amount: float, amount_precision: Optional[float],precisionMode: Optional[int]) -> float:

    if amount_precision is not None and precisionMode is not None:
        precision = int(amount_precision) if precisionMode != TICK_SIZE else amount_precision
        # precision must be an int for non-ticksize inputs.
        amount = float(decimal_to_precision(amount, rounding_mode=TRUNCATE,precision=precision,counting_mode=precisionMode))
    return amount


def amount_to_contract_precision(amount, amount_precision: Optional[float], precisionMode: Optional[int],contract_size: Optional[float]) -> float:

    if amount_precision is not None and precisionMode is not None:
        contracts = amount_to_contracts(amount, contract_size)
        amount_p = amount_to_precision(contracts, amount_precision, precisionMode)
        return contracts_to_amount(amount_p, contract_size)
    return amount



def __price_to_precision_significant_digits(price: float,price_precision: float,*,rounding_mode: int = ROUND) -> float:

    from decimal import ROUND_DOWN as dec_ROUND_DOWN
    from decimal import ROUND_UP as dec_ROUND_UP
    from decimal import Decimal
    dec = Decimal(str(price))
    string = f'{dec:f}'
    precision = round(price_precision)

    q = precision - dec.adjusted() - 1
    sigfig = Decimal('10') ** -q
    if q < 0:
        string_to_precision = string[:precision]
        # string_to_precision is '' when we have zero precision
        below = sigfig * Decimal(string_to_precision if string_to_precision else '0')
        above = below + sigfig
        res = above if rounding_mode == ROUND_UP else below
        precise = f'{res:f}'
    else:
        precise = '{:f}'.format(dec.quantize(sigfig,rounding=dec_ROUND_DOWN if rounding_mode == ROUND_DOWN else dec_ROUND_UP))
    return float(precise)


def price_to_precision(price: float,price_precision: Optional[float],precisionMode: Optional[int],*,rounding_mode: int = ROUND) -> float:

    if price_precision is not None and precisionMode is not None:
        if rounding_mode not in (ROUND_UP, ROUND_DOWN):
            return float(decimal_to_precision(price, rounding_mode=rounding_mode,precision=price_precision,counting_mode=precisionMode))

        if precisionMode == TICK_SIZE:
            precision = FtPrecise(price_precision)
            price_str = FtPrecise(price)
            missing = price_str % precision
            if not missing == FtPrecise("0"):
                if rounding_mode == ROUND_UP:
                    res = price_str - missing + precision
                elif rounding_mode == ROUND_DOWN:
                    res = price_str - missing
                return round(float(str(res)), 14)
            return price
        elif precisionMode == DECIMAL_PLACES:

            ndigits = round(price_precision)
            ticks = price * (10**ndigits)
            if rounding_mode == ROUND_UP:
                return ceil(ticks) / (10**ndigits)
            if rounding_mode == ROUND_DOWN:
                return floor(ticks) / (10**ndigits)

            raise ValueError(f"Unknown rounding_mode {rounding_mode}")
        elif precisionMode == SIGNIFICANT_DIGITS:
            if rounding_mode in (ROUND_UP, ROUND_DOWN):
                return __price_to_precision_significant_digits(price, price_precision, rounding_mode=rounding_mode)

        raise ValueError(f"Unknown precisionMode {precisionMode}")
    return price
