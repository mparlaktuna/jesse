from jesse.strategies import Strategy
from jesse.services import logger


# test_end
class Test41(Strategy):
    def __init__(self, exchange, symbol, timeframe):
        super().__init__('Test41', '0.0.1', exchange, symbol, timeframe)

    def should_long(self) -> bool:
        return self.index == 0

    def should_short(self) -> bool:
        return False

    def go_long(self):
        qty = 1
        self.buy = qty, 2

    def go_short(self):
        pass

    def should_cancel(self):
        return False

    def terminate(self):
        # log, so we can check this block was executed in the first place
        logger.info('executed terminate successfully')

        # assert open position
        assert self.position.is_open
        assert self.position.pnl == 97

        # close it manually
        self.liquidate()

