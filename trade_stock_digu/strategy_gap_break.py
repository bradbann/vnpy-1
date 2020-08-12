import numpy as np
from datetime import datetime
from datetime import timedelta
from collections import Counter
from strategy_base import StrategyBase
from data_service import DataServiceTushare
from convert_utils import string_to_datetime, time_to_str

class StrategyGapBreak(StrategyBase):
    """
    缺口突破策略：
    1、中长期均线多头排列(ma60>ma120>ma250<ma500,)
    2、股价最近一年未暴涨(high_250/low_250<3)
    3、最近5（10）天有缺口图片 days_gap
    4、缺口价格在250日新高附近
    5、股价回调到缺口位置，缺口下端价格+2%以内
    6、缺口上方最高价涨幅0.2

    待补充：
    """
    n_years = 2
    days_gap = 5
    pct_near_gap = 0.02
    pct_back = 0.3
    max_times_in_year = 3
    days_break = 250
    pct_max = 0.2
    # def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
    #     """"""
    #     super().__init__()

    def __init__(self):
        """"""
        super().__init__()

    def pick_stock(self, date_picked):            
        ds_tushare = DataServiceTushare()        
        lst_code_picked = list()
        for ts_code in ds_tushare.get_stock_list():
            stock_basic = ds_tushare.get_stock_basic_info(ts_code)
            if stock_basic is None:
                self.logger.info('None stock basic info %s' %ts_code)
                continue
            dt_date = string_to_datetime(date_picked)
            d = timedelta(days=-365 * self.n_years)
            if stock_basic['list_date'] > time_to_str(dt_date+d, '%Y%m%d') or 'ST' in stock_basic['name']:
                # 排除上市时间小于2年和st股票
                continue
            dic_stock_price = ds_tushare.get_stock_price_info(ts_code, date_picked)       
            if dic_stock_price is None:
                # 排除选股日停牌的股票
                continue   
            high_gap = 'high_' + str(self.days_gap)
            low_gap = 'low_' + str(self.days_gap)
            days_break_gap = 'high_' + str(self.days_break)
            try:
                if dic_stock_price['high_250'] / dic_stock_price['low_250'] < self.max_times_in_year \
                    and dic_stock_price['ma_60'] > dic_stock_price['ma_120'] and dic_stock_price['ma_120'] > dic_stock_price['ma_250']:
                    date_pre = ds_tushare.get_pre_trade_date(date_picked, self.days_gap)
                    price_pre = ds_tushare.get_stock_price_info(ts_code, date_pre)
                    if price_pre['high'] < dic_stock_price[low_gap] and price_pre['close'] > price_pre[days_break_gap] \
                        and dic_stock_price['close'] < price_pre['high']*(1.0+self.pct_near_gap) \
                            and price_pre[high_gap]/price_pre['close'] < (1.0+self.pct_max):
                        lst_code_picked.append(dic_stock_price['ts_code'])                    
            except:
                self.logger.info('XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX')
                self.logger.info(dic_stock_price)                    
        return lst_code_picked


if __name__ == "__main__":
    ds_tushare = DataServiceTushare()
    strategy = StrategyGapBreak()
    print(strategy.pick_stock('20200811'))
    # lst_trade_date = ds_tushare.get_trade_cal('20200101', '20200701')
    # cnt_loop = 0
    # for item_date in lst_trade_date:
    #     cnt_loop += 1
    #     if cnt_loop % 5 == 0:
    #         # 换股日
    #         strategy.pick_stock(item_date)

"""
to do:
计算股票池的每日涨跌幅（叠加大盘指数绘图）
"""                    