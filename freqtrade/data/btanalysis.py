import logging
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
import numpy as np
import pandas as pd

from freqtrade.constants import LAST_BT_RESULT_FN, IntOrInf
from freqtrade.exceptions import OperationalException
from freqtrade.misc import file_dump_json, json_load
from freqtrade.optimize.backtest_caching import get_backtest_metadata_filename
from freqtrade.persistence import LocalTrade, Trade, init_db
from freqtrade.types import BacktestHistoryEntryType, BacktestResultType

logger = logging.getLogger(__name__)
# Newest format
BT_DATA_COLUMNS = ['pair', 'stake_amount', 'max_stake_amount', 'amount','open_date', 'close_date', 'open_rate', 'close_rate','fee_open', 'fee_close', 'trade_duration','profit_ratio', 'profit_abs', 'exit_reason','initial_stop_loss_abs', 'initial_stop_loss_ratio', 'stop_loss_abs','stop_loss_ratio', 'min_rate', 'max_rate', 'is_open', 'enter_tag','leverage', 'is_short', 'open_timestamp', 'close_timestamp', 'orders']

def get_latest_optimize_filename(directory: Union[Path, str], variant: str) -> str:

    if isinstance(directory, str):
        directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"Directory '{directory}' does not exist.")
    filename = directory / LAST_BT_RESULT_FN

    if not filename.is_file():
        raise ValueError(f"Directory '{directory}' does not seem to contain backtest statistics yet.")

    with filename.open() as file:
        data = json_load(file)

    if f'latest_{variant}' not in data:
        raise ValueError(f"Invalid '{LAST_BT_RESULT_FN}' format.")
    return data[f'latest_{variant}']



def get_latest_backtest_filename(directory: Union[Path, str]) -> str:
    return get_latest_optimize_filename(directory, 'backtest')


def get_latest_hyperopt_filename(directory: Union[Path, str]) -> str:
    try:
        return get_latest_optimize_filename(directory, 'hyperopt')
    except ValueError:
        return 'hyperopt_results.pickle'



def get_latest_hyperopt_file(directory: Union[Path, str], predef_filename: Optional[str] = None) -> Path:

    if isinstance(directory, str):
        directory = Path(directory)
    if predef_filename:
        if Path(predef_filename).is_absolute():
            raise OperationalException("--hyperopt-filename expects only the filename, not an absolute path.")
        return directory / predef_filename
    return directory / get_latest_hyperopt_filename(directory)



def load_backtest_metadata(filename: Union[Path, str]) -> Dict[str, Any]:

    filename = get_backtest_metadata_filename(filename)
    try:
        with filename.open() as fp:
            return json_load(fp)
    except FileNotFoundError:
        return {}
    except Exception as e:
        raise OperationalException('Unexpected error while loading backtest metadata.') from e


def load_backtest_stats(filename: Union[Path, str]) -> BacktestResultType:

    if isinstance(filename, str):
        filename = Path(filename)
    if filename.is_dir():
        filename = filename / get_latest_backtest_filename(filename)
    if not filename.is_file():
        raise ValueError(f"File {filename} does not exist.")
    logger.info(f"Loading backtest result from {filename}")
    with filename.open() as file:
        data = json_load(file)

    if isinstance(data, dict):
        data['metadata'] = load_backtest_metadata(filename)
    return data



def load_and_merge_backtest_result(strategy_name: str, filename: Path, results: Dict[str, Any]):

    bt_data = load_backtest_stats(filename)
    k: Literal['metadata', 'strategy']
    for k in ('metadata', 'strategy'):  # type: ignore
        results[k][strategy_name] = bt_data[k][strategy_name]
    results['metadata'][strategy_name]['filename'] = filename.stem
    comparison = bt_data['strategy_comparison']
    for i in range(len(comparison)):
        if comparison[i]['key'] == strategy_name:
            results['strategy_comparison'].append(comparison[i])
            break


def _get_backtest_files(dirname: Path) -> List[Path]:
    return list(reversed(sorted(dirname.glob('backtest-result-*-[0-9][0-9].json'))))



def _extract_backtest_result(filename: Path) -> List[BacktestHistoryEntryType]:
    metadata = load_backtest_metadata(filename)
    return [
        {'filename': filename.stem,
            'strategy': s,
            'run_id': v['run_id'],
            'notes': v.get('notes', ''),
            'backtest_start_time': v['backtest_start_time'],
            'backtest_start_ts': v.get('backtest_start_ts', None),
            'backtest_end_ts': v.get('backtest_end_ts', None),
            'timeframe': v.get('timeframe', None),
            'timeframe_detail': v.get('timeframe_detail', None),
        } for s, v in metadata.items()]


def get_backtest_result(filename: Path) -> List[BacktestHistoryEntryType]:
    return _extract_backtest_result(filename)



def get_backtest_resultlist(dirname: Path) -> List[BacktestHistoryEntryType]:
    return [result for filename in _get_backtest_files(dirname) for result in _extract_backtest_result(filename)]



def delete_backtest_result(file_abs: Path):

    logger.info(f"Deleting backtest result file: {file_abs.name}")
    file_abs_meta = file_abs.with_suffix('.meta.json')
    file_abs.unlink()
    file_abs_meta.unlink()


def update_backtest_metadata(filename: Path, strategy: str, content: Dict[str, Any]):

    metadata = load_backtest_metadata(filename)
    if not metadata:
        raise ValueError("File does not exist.")
    if strategy not in metadata:
        raise ValueError("Strategy not in metadata.")
    metadata[strategy].update(content)
    file_dump_json(get_backtest_metadata_filename(filename), metadata)



def find_existing_backtest_stats(dirname: Union[Path, str], run_ids: Dict[str, str],min_backtest_date: Optional[datetime] = None) -> Dict[str, Any]:

    run_ids = copy(run_ids)
    dirname = Path(dirname)
    results: Dict[str, Any] = {
                            'metadata': {},
                            'strategy': {},
                            'strategy_comparison': [],
    }

    for filename in _get_backtest_files(dirname):
        metadata = load_backtest_metadata(filename)
        if not metadata:
            break

        for strategy_name, run_id in list(run_ids.items()):
            strategy_metadata = metadata.get(strategy_name, None)
            if not strategy_metadata:
                continue

            if min_backtest_date is not None:
                backtest_date = strategy_metadata['backtest_start_time']
                backtest_date = datetime.fromtimestamp(backtest_date, tz=timezone.utc)
                if backtest_date < min_backtest_date:
                    del run_ids[strategy_name]
                    continue

            if strategy_metadata['run_id'] == run_id:
                del run_ids[strategy_name]
                load_and_merge_backtest_result(strategy_name, filename, results)

        if len(run_ids) == 0:
            break
    return results




def _load_backtest_data_df_compatibility(df: pd.DataFrame) -> pd.DataFrame:

    df['open_date'] = pd.to_datetime(df['open_date'], utc=True)
    df['close_date'] = pd.to_datetime(df['close_date'], utc=True)
    if 'is_short' not in df.columns:
        df['is_short'] = False
    if 'leverage' not in df.columns:
        df['leverage'] = 1.0
    if 'enter_tag' not in df.columns:
        df['enter_tag'] = df['buy_tag']
        df = df.drop(['buy_tag'], axis=1)
    if 'max_stake_amount' not in df.columns:
        df['max_stake_amount'] = df['stake_amount']
    if 'orders' not in df.columns:
        df['orders'] = None
    return df


def load_backtest_data(filename: Union[Path, str], strategy: Optional[str] = None) -> pd.DataFrame:

    data = load_backtest_stats(filename)
    if not isinstance(data, list):
        if 'strategy' not in data:
            raise ValueError("Unknown dataformat.")

        if not strategy:
            if len(data['strategy']) == 1:
                strategy = list(data['strategy'].keys())[0]
            else:
                raise ValueError("Detected backtest result with more than one strategy. "
                                "Please specify a strategy.")

        if strategy not in data['strategy']:
            raise ValueError(f"Strategy {strategy} not available in the backtest result. "
                            f"Available strategies are '{','.join(data['strategy'].keys())}'")

        data = data['strategy'][strategy]['trades']
        df = pd.DataFrame(data)
        if not df.empty:
            df = _load_backtest_data_df_compatibility(df)

    else:
        raise OperationalException("Backtest-results with only trades data are no longer supported.")
    if not df.empty:
        df = df.sort_values("open_date").reset_index(drop=True)
    return df




def analyze_trade_parallelism(results: pd.DataFrame, timeframe: str) -> pd.DataFrame:

    from freqtrade.exchange import timeframe_to_resample_freq
    timeframe_freq = timeframe_to_resample_freq(timeframe)
    dates = [pd.Series(pd.date_range(row[1]['open_date'], row[1]['close_date'],freq=timeframe_freq)) for row in results[['open_date', 'close_date']].iterrows()]
    deltas = [len(x) for x in dates]
    dates = pd.Series(pd.concat(dates).values, name='date')
    df2 = pd.DataFrame(np.repeat(results.values, deltas, axis=0), columns=results.columns)
    df2 = pd.concat([dates, df2], axis=1)
    df2 = df2.set_index('date')
    df_final = df2.resample(timeframe_freq)[['pair']].count()
    df_final = df_final.rename({'pair': 'open_trades'}, axis=1)
    return df_final



def evaluate_result_multi(results: pd.DataFrame, timeframe: str,max_open_trades: IntOrInf) -> pd.DataFrame:
    df_final = analyze_trade_parallelism(results, timeframe)
    return df_final[df_final['open_trades'] > max_open_trades]



def trade_list_to_dataframe(trades: Union[List[Trade], List[LocalTrade]]) -> pd.DataFrame:

    df = pd.DataFrame.from_records([t.to_json(True) for t in trades], columns=BT_DATA_COLUMNS)
    if len(df) > 0:
        df['close_date'] = pd.to_datetime(df['close_date'], utc=True)
        df['open_date'] = pd.to_datetime(df['open_date'], utc=True)
        df['close_rate'] = df['close_rate'].astype('float64')
    return df


def load_trades_from_db(db_url: str, strategy: Optional[str] = None) -> pd.DataFrame:

    init_db(db_url)

    filters = []
    if strategy:
        filters.append(Trade.strategy == strategy)
    trades = trade_list_to_dataframe(list(Trade.get_trades(filters).all()))
    return trades



def load_trades(source: str, db_url: str, exportfilename: Path,no_trades: bool = False, strategy: Optional[str] = None) -> pd.DataFrame:

    if no_trades:
        df = pd.DataFrame(columns=BT_DATA_COLUMNS)
        return df

    if source == "DB":
        return load_trades_from_db(db_url)
    elif source == "file":
        return load_backtest_data(exportfilename, strategy)


def extract_trades_of_period(dataframe: pd.DataFrame, trades: pd.DataFrame,date_index=False) -> pd.DataFrame:

    if date_index:
        trades_start = dataframe.index[0]
        trades_stop = dataframe.index[-1]
    else:
        trades_start = dataframe.iloc[0]['date']
        trades_stop = dataframe.iloc[-1]['date']
    trades = trades.loc[(trades['open_date'] >= trades_start) & (trades['close_date'] <= trades_stop)]
    return trades
