# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools
import numpy
import pandas
import scipy
from time import sleep


from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS

delta = 0.1 #Amount by which we increase/decrease our bid/ask (min possible price difference)
# Minimum amount we wish our B bid/ask to be above/below our A bid/ask
# Therefore, our B spread should always be greater or equal to (A_spread + 2 * pillow) 
pillow = 0.1 
k = 2

sleep_time = 0.1


class AutoTrader(BaseAutoTrader):
    """Example Auto-trader.

    When it starts this auto-trader places ten-lot bid and ask orders at the
    current best-bid and best-ask prices respectively. Thereafter, if it has
    a long position (it has bought more lots than it has sold) it reduces its
    bid and ask prices. Conversely, if it has a short position (it has sold
    more lots than it has bought) then it increases its bid and ask prices.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1) 
        # future is more liquid than ETF

        # arrays are of the format [bid, ask]
        self.bestPriceETF = [0, 0]
        self.bestVolumeETF = [0, 0]
        self.bestPriceF = [0, 0]
        self.bestVolumeF = [0, 0]
        self.bids = set()
        self.asks = set()
        self.FutureOrderBookBid = []
        self.FutureOrderBookAsk = []
        self.ETFOrderBookBid  = []
        self.ETFOrderBookAsk = []

        self.ask_id = self.askETF = self.bid_id = self.bidETF = self.position_F = self.position_E = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.
        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)        

    def delete_outstanding_ordersETF (self, side):
        """Delete outstanding orders that have not been filled yet"""
        if (side == Side.ASKS):
            for (id, i) in (self.asks):
                    self.send_cancel_order(id)
        else:
            for (id, i) in (self.bids):
                    self.send_cancel_order(id)
        

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        if instrument == Instrument.FUTURE:
            print("Future entered")
            # extract future data and update best prices and volumes
            self.bestPriceF[0] = max(bid_prices)
            self.bestPriceF[1] = max(ask_prices)
            self.bestVolumeF[0] = bid_volumes[bid_prices.index(max(bid_prices))]
            self.bestVolumeF[1] = ask_volumes[ask_prices.index(max(ask_prices))]
            self.FutureOrderBookBid = zip(bid_prices, bid_volumes)
            self.FutureOrderBookAsk = zip (ask_prices, ask_volumes) 
            if (self.bestPriceETF[0] != 0):
                print("Calculate trade entered")
                bid_update = ask_update = False

                # If our bid in ETF is above the current bid price
                # for A on the market, we decrease it to be below future
                # Our spread in ETF will always be larger than spread future
                if (self.bestPriceF[0] - self.bidETF < pillow):
                    self.bidETF = self.bestPriceF[0] - pillow
                    bid_update = True
                
                # upper bound the ask price
                if (self.askETF - self.bestPriceF[1] < pillow):
                    self.askETF = self.bestPriceF[1] + pillow
                    ask_update = True

                # if our ETF bids have updated, attempt to place orders for them
                # use limit orders for illiquid market to be safe
                if (ask_update and self.position_E >= -POSITION_LIMIT):
                    # delete outstanding orders
                    # self.delete_outstanding_ordersETF(Side.ASK)
                    # new order
                    self.ask_id = next(self.order_ids)
                    self.send_insert_order(self.ask_id, Side.SELL, int(self.askETF), LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.asks.add(self.ask_id)
                    print("ask trade")

                if (bid_update and self.position_E <= POSITION_LIMIT):
                    # delete old outstanding orders
                    # self.delete_outstanding_ordersETF(Side.BID)
                    # new order
                    self.bid_id = next(self.order_ids)
                    self.send_insert_order(self.bid_id, Side.BUY, int(self.bidETF), LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.bids.add(self.bid_id)
                    print("bid trade")
                # check for inbalance in positions. If we have an inbalance, hedge in liquid market (future)
                total_position = self.position_E + self.position_F
                desired_volume = abs(total_position)

                # hedge orders are fill and kill. Avoids bidding in liquid market at unfavorable prices

                # if our position is positive, sell in futures (meeting a bid)
                if (total_position > 0):
                    self.ask_id = next(self.order_ids)
                    self.send_hedge_order(self.ask_id, Side.ASK, self.bestPriceF[1], desired_volume)
                
                # if our position is negative, buy in futures (meeting an ask)
                if (total_position < 0):
                    self.bid_id = next(self.order_ids)
                    self.send_hedge_order(self.bid_id, Side.BID, self.bestPriceF[0], desired_volume)

        elif instrument == Instrument.ETF:
            print("ETF entered")
            self.bestPriceETF[0] = max(bid_prices)
            self.bestPriceETF[1] = max(ask_prices)
            self.bestVolumeETF[0] = bid_volumes[bid_prices.index(max(bid_prices))]
            self.bestVolumeETF[1] = ask_volumes[ask_prices.index(max(ask_prices))]
            self.ETFOrderBookBid = zip(bid_prices, bid_volumes)
            self.ETFOrderBookAsk = zip (ask_prices, ask_volumes) 
            if (self.bestPriceF[0] != 0):
                # check outstanding orders
                has_outstanding_bids = (len(self.bids) != 0)
                has_outstanding_asks = (len(self.asks) != 0)

                # if our bid/asks haven't gone through, 
                # increase spread by decreasing/increasing bids and asks respectively

                if (not has_outstanding_bids):
                    self.bidETF -= delta * k

                if (not has_outstanding_asks):
                    self.askETF += delta *k

                if self.position_E > 0 and self.position_F < 0:  
                    # We check all possible ask prices, and sell our B so long as the sale is profitable
                    i = 0
                    while self.bestPriceETF[0] >= self.FutureOrderBookAsk[i][0] and i < len(self.FutureOrderBookAsk):
                        # We try to sell ALL B
                        position_ETF = self.position_E
                        position_F = self.position_F
                        new_volume = min(abs(self.ETFOrderBookAsk[0][1]), abs(self.FutureOrderBookAsk[i][1]), abs(position_ETF), abs(position_F))  # best price
                        if new_volume <= 0: # Prevent impossible orders
                            break
                        self.ask_id = next(self.order_ids)
                        self.bid_id = next(self.order_ids)
                        self.send_insert_order(self.ask_id, Side.ASK, self.bestPriceETF[0], new_volume, Lifespan.FILL_AND_KILL)
                        self.send_hedge_order(self.bid_id, Side.BID, self.FutureOrderBookAsk[i][0], new_volume, Lifespan.FILL_AND_KILL)
                            
                        i +=1
                # If we currently have NEGATIVE B positions, we look to unwind by buying B
                if self.position_E < 0 and self.position_F > 0:
                    
                    # To unwind, we need to BUY B
                    # So we desire the price we buy B (B ASK) to be lower than the price we can sell A (bid A)
                    i = 0 

                    while self.bestPriceETF[1] <= self.FutureOrderBookBid[i][0] and i < len(self.FutureOrderBookBid):
                        position_ETF = self.position_E
                        position_F = self.position_F
                        # We try to buy ALL B
                        new_volume = min(abs(self.ETFOrderBookAsk[0][1]), abs(self.FutureOrderBookAsk[i][1]), abs(position_ETF), abs(position_F))  # best price
                        if new_volume <= 0: # Prevent impossible orders
                            break
                        self.ask_id = next(self.order_ids)
                        self.bid_id = next(self.order_ids)
                        self.send_insert_order(self.bid_id, Side.BID, self.bestPriceETF[1], new_volume, Lifespan.FILL_AND_KILL)
                        self.send_hedge_order(self.ask_id, Side.ASK, self.FutureOrderBookBid[i][0], new_volume, Lifespan.FILL_AND_KILL)
                        
                        i+=1


    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position_E += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position_E -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)
        

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0
            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)
