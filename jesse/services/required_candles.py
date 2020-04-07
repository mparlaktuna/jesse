import arrow
import numpy as np

import jesse.helpers as jh
from jesse.config import config
from jesse.models import Candle
from jesse.services.cache import cache
from jesse.store import store
from jesse.services.candle import generate_candle_from_one_minutes
from jesse.exceptions import Breaker, CandleNotFoundInDatabase


def load_required_candles(exchange: str, symbol: str, start_date_str: str, finish_date_str: str):
    """
    loads initial candles that required before executing strategies.
    210 for the biggest timeframe and more for the rest
    """
    start_date = jh.arrow_to_timestamp(arrow.get(start_date_str, 'YYYY-MM-DD'))
    finish_date = jh.arrow_to_timestamp(arrow.get(finish_date_str, 'YYYY-MM-DD')) - 60000

    # validate
    if start_date == finish_date:
        raise ValueError('start_date and finish_date cannot be the same.')
    if start_date > finish_date:
        raise ValueError('start_date cannot be bigger than finish_date.')
    if finish_date > arrow.utcnow().timestamp * 1000:
        raise ValueError('Can\'t backtest the future!')

    max_timeframe = jh.max_timeframe(config['app']['considering_timeframes'])
    short_candles_count = 210 * jh.timeframe_to_one_minutes(max_timeframe)
    pre_finish_date = start_date - 60_000
    pre_start_date = pre_finish_date - short_candles_count * 60_000
    # make sure starting from the beginning of the day instead
    pre_start_date = jh.get_arrow(pre_start_date).floor('day').timestamp * 1000
    # update candles_count to count from the beginning of the day instead
    short_candles_count = int((pre_finish_date - pre_start_date) / 60_000)

    key = jh.key(exchange, symbol)
    cache_key = '{}-{}-{}'.format(str(pre_start_date), str(pre_finish_date), key)
    cached_value = cache.get_cache(cache_key)

    # if redis cache exists
    if cached_value:
        candles_tuple = cached_value
    # not cached, get and cache for later calls in the next 5 minutes
    else:
        # fetch from database
        candles_tuple = tuple(
            Candle.select(
                Candle.timestamp, Candle.open, Candle.close, Candle.high, Candle.low,
                Candle.volume
            ).where(
                Candle.timestamp.between(pre_start_date, pre_finish_date),
                Candle.exchange == exchange,
                Candle.symbol == symbol
            ).order_by(Candle.timestamp.asc()).tuples()
        )

        # cache it for near future calls
        cache.set_cache(cache_key, candles_tuple)

    candles = np.array(candles_tuple)

    if len(candles) < short_candles_count + 1:
        first_existing_candle = tuple(
            Candle.select(Candle.timestamp).where(
                Candle.exchange == exchange,
                Candle.symbol == symbol
            ).order_by(Candle.timestamp.asc()).limit(1).tuples()
        )

        if not len(first_existing_candle):
            raise CandleNotFoundInDatabase(
                'No candle for {} {} is present in the database. Try importing candles.'.format(
                    exchange, symbol
                )
            )

        first_existing_candle = first_existing_candle[0][0]

        last_existing_candle = tuple(
            Candle.select(Candle.timestamp).where(
                Candle.exchange == exchange,
                Candle.symbol == symbol
            ).order_by(Candle.timestamp.desc()).limit(1).tuples()
        )[0][0]

        first_backtestable_timestamp = first_existing_candle + (pre_finish_date - pre_start_date) + (60_000 * 1440)

        # if first backtestable timestamp is in the future, that means we have some but not enough candles
        if first_backtestable_timestamp > jh.today():
            raise CandleNotFoundInDatabase(
                'Not enough candle for {} {} is present in the database. Try importing candles.'.format(
                    exchange, symbol
                )
            )

        raise CandleNotFoundInDatabase(
            'Not enough candles for {} {} exists to run backtest from {} => {}. \n'
            'First available date is {}\n'
            'Last available date is {}'.format(
                exchange, symbol, start_date_str, finish_date_str,
                jh.timestamp_to_date(first_backtestable_timestamp),
                jh.timestamp_to_date(last_existing_candle),
            )
        )

    return candles


def inject_required_candles_to_store(candles: np.ndarray, exchange: str, symbol: str):
    """
    generate and add required candles to the candle store
    """
    # batch add 1m candles:
    store.candles.batch_add_candle(candles, exchange, symbol, '1m', with_generation=False)

    # loop to generate, and add candles (without execution)
    for i in range(len(candles)):
        for timeframe in config['app']['considering_timeframes']:
            # skip 1m. already added
            if timeframe == '1m':
                continue

            num = jh.timeframe_to_one_minutes(timeframe)

            if (i + 1) % num == 0:
                generated_candle = generate_candle_from_one_minutes(
                    timeframe,
                    candles[(i - (num - 1)):(i + 1)],
                    True
                )

                store.candles.add_candle(
                    generated_candle,
                    exchange,
                    symbol,
                    timeframe,
                    with_execution=False,
                    with_generation=False
                )
