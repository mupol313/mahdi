import logging
from typing import Optional
import numpy as np
from pandas import DataFrame, read_json, to_datetime

from freqtrade import misc
from freqtrade.configuration import TimeRange
from freqtrade.constants import DEFAULT_DATAFRAME_COLUMNS, DEFAULT_TRADES_COLUMNS
from freqtrade.data.converter import trades_dict_to_list, trades_list_to_df
from freqtrade.enums import CandleType
from .idatahandler import IDataHandler

logger = logging.getLogger(__name__)

class JsonDataHandler(IDataHandler):

    _use_zip = False
    _columns = DEFAULT_DATAFRAME_COLUMNS

    def ohlcv_store(self, pair: str, timeframe: str, data: DataFrame, candle_type: CandleType) -> None:

        filename = self._pair_data_filename(self._datadir, pair, timeframe, candle_type)
        self.create_dir_if_needed(filename)
        _data = data.copy()
        _data['date'] = _data['date'].view(np.int64) // 1000 // 1000

        _data.reset_index(drop=True).loc[:, self._columns].to_json(
            filename, orient="values",
            compression='gzip' if self._use_zip else None)


    def _ohlcv_load(self, pair: str, timeframe: str,timerange: Optional[TimeRange], candle_type: CandleType) -> DataFrame:

        filename = self._pair_data_filename(self._datadir, pair, timeframe, candle_type=candle_type)
        if not filename.exists():
            # Fallback mode for 1M files
            filename = self._pair_data_filename(self._datadir, pair, timeframe, candle_type=candle_type, no_timeframe_modify=True)
            if not filename.exists():
                return DataFrame(columns=self._columns)
        try:
            pairdata = read_json(filename, orient='values')
            pairdata.columns = self._columns
        except ValueError:
            logger.error(f"Could not load data for {pair}.")
            return DataFrame(columns=self._columns)
        pairdata = pairdata.astype(dtype={'open': 'float', 'high': 'float','low': 'float', 'close': 'float', 'volume': 'float'})
        pairdata['date'] = to_datetime(pairdata['date'], unit='ms', utc=True)
        return pairdata


    def ohlcv_append(self,pair: str,timeframe: str,data: DataFrame,candle_type: CandleType) -> None:
        raise NotImplementedError()


    def _trades_store(self, pair: str, data: DataFrame) -> None:

        filename = self._pair_trades_filename(self._datadir, pair)
        trades = data.values.tolist()
        misc.file_dump_json(filename, trades, is_zip=self._use_zip)



    def trades_append(self, pair: str, data: DataFrame):
        raise NotImplementedError()

    def _trades_load(self, pair: str, timerange: Optional[TimeRange] = None) -> DataFrame:

        filename = self._pair_trades_filename(self._datadir, pair)
        tradesdata = misc.file_load_json(filename)

        if not tradesdata:
            return DataFrame(columns=DEFAULT_TRADES_COLUMNS)

        if isinstance(tradesdata[0], dict):
            logger.info("Old trades format detected - converting")
            tradesdata = trades_dict_to_list(tradesdata)
            pass
        return trades_list_to_df(tradesdata, convert=False)


    @classmethod
    def _get_file_extension(cls):
        return "json.gz" if cls._use_zip else "json"


class JsonGzDataHandler(JsonDataHandler):
    _use_zip = True
