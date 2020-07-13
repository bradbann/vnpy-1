# encoding: UTF-8

import json
import tushare as ts
import numpy as np
import pandas as pd
from enum import Enum
from collections import deque
import traceback

from datetime import datetime
from datetime import timedelta
from time import time, sleep
from pymongo import MongoClient, ASCENDING, DESCENDING

from vnpy.app.cta_strategy import (
    BarData,
    BarGenerator,
    ArrayManager
)
from vnpy.trader.constant import Exchange

from convert_utils import string_to_datetime, time_to_str
from logger import Logger

# 加载配置
config = open('C:/vnstudio/Lib/site-packages/vnpy/trade_stock_digu/config.json')
setting = json.load(config)

MONGO_HOST = setting['MONGO_HOST']
MONGO_PORT = setting['MONGO_PORT']
STOCK_DB_NAME = setting['DB_NAME']
STOCK_DB_NAME_VNPY = setting['DB_NAME_VNPY']
CL_STOCK_K_DATA_VNPY = setting['CL_STOCK_K_DATA_VNPY']
CL_STOCK_DATE = setting['CL_STOCK_DATE']
CL_STOCK_BASIC = setting['CL_STOCK_BASIC']
CL_TRADE_CAL = setting['CL_TRADE_CAL']
DATA_BEGIN_DATE = setting['DATA_BEGIN_DATE']
CL_INDEXS = setting['CL_INDEXS']
CL_TOP10_HOLDER = setting['CL_TOP10_HOLDER']
CL_TOP10_FLOADHOLDER = setting['CL_TOP10_FLOADHOLDER']
CL_PLEDGE_STAT = setting['CL_PLEDGE_STAT']
CL_REPURCHASE = setting['CL_REPURCHASE']
CL_STK_HOLDERNUMBER = setting['CL_STK_HOLDERNUMBER']
CL_STK_HOLDERTRADE = setting['CL_STK_HOLDERTRADE']
CL_STK_TOP_LIST = setting['CL_STK_TOP_LIST']

class TsCodeType(Enum):
    """
    交易代码类型 1: 股票  2：指数
    """
    STOCK = 1
    INDEX = 2 

class DataServiceTushare(object):
    """封装tushare获取原始数据模块"""

    mc = MongoClient(MONGO_HOST, MONGO_PORT)  # Mongo连接
    db = mc[STOCK_DB_NAME]  # 数据库
    db_vnpy = mc[STOCK_DB_NAME_VNPY]  # 数据库
    log = Logger().getlog()
    count_max_retry = 10
    second_sleep = 60
    index_lst = ['000001.SH', '399001.SZ', '399005.SZ', '399006.SZ']       

    def __init__(self):
        """Constructor"""        
        cl_stock_db_date = self.db[CL_STOCK_DATE]
        db_date = cl_stock_db_date.find_one({}, {"_id": 0, "db_date": 1})
        self.db_date = DATA_BEGIN_DATE if db_date is None else db_date['db_date']
        self.date_now = time_to_str(datetime.now(), '%Y%m%d')
        ts.set_token('4c1d16a895e4c954adc8d2a436f2b21dd4ccc514f0c5a192edaa953b')
        self.pro = ts.pro_api()
        cl_stock_basic = self.db[CL_STOCK_BASIC]
        self.lst_stock_ = list()
        self.list_stock_vnpy_ = list()

    def _get_trade_date(self, trade_date):
        # 获取当前日期最邻近的一个交易日
        # 1、如果当前日期就是交易日，则返回当前日期
        # 2、如果当前日期不是交易日，则返回当前日期之前的一个交易日
        cl_cal = self.db[CL_TRADE_CAL]
        trade_cal = cl_cal.find(
            {'cal_date': {"$lte": trade_date}, 'is_open': 1},
            {'_id': 0}).sort("cal_date")
        return list(trade_cal)[-1]['cal_date']

    def _is_in_vnpy_db(self, ts_code, update=True):
        if ts_code in self.index_lst:
            return True
        else:
            return False
        # if update is True:
        #     if self.list_stock_vnpy_ == []:
        #         cl_stock_code_vnpy = self.db_vnpy[CL_STOCK_K_DATA_VNPY]
        #         res_symbol = cl_stock_code_vnpy.find({}).distinct('symbol')
        #         for item in res_symbol:
        #             self.list_stock_vnpy_.append(item.replace('_', '.'))
        #     return ts_code in self.list_stock_vnpy_
        # else:
        #     if self.list_stock_vnpy_ == []:
        #         cl_daily_basic = self.db[CL_DAILY_BASIC]
        #         # 流通市值大于100亿的公司入vnpy数据库            
        #         daily_basic = cl_daily_basic.find({'circ_mv': {"$gte": 1000000}},
        #             {'ts_code': 1, 'total_mv': 1, '_id': 0}).sort("ts_code")
        #         for item in daily_basic:
        #             self.list_stock_vnpy_.append(item['ts_code'])
        #     return ts_code in self.list_stock_vnpy_

    def _build_db_vnpy(self, d):
        # 更新vnpy数据库数据
        if d['trade_date'] > self.db_date:            
            d_db_vnpy = dict()
            if d['ts_code'][-2:] == 'SH':
                # exchange = Exchange.SSE
                exchange = 'SSE'
            else:
                # exchange = Exchange.SZSE
                exchange = 'SZSE'
            d_db_vnpy['symbol'] = d['ts_code'].replace('.', '_')
            d_db_vnpy['exchange'] = exchange
            d_db_vnpy['datetime'] = string_to_datetime(d['trade_date'])
            d_db_vnpy['interval'] = 'd'
            d_db_vnpy['volume'] = d['vol']
            d_db_vnpy['open_interest'] = d['pre_close']
            d_db_vnpy['open_price'] = d['open']
            d_db_vnpy['high_price'] = d['high']
            d_db_vnpy['low_price'] = d['low']
            d_db_vnpy['close_price'] = d['close']
            flt_vnpy = {'symbol': d['ts_code'], 'datetime': d['trade_date'], 'exchange:': exchange, 'interval:': 'd',}        
            cl_stock_code_vnpy = self.db_vnpy[CL_STOCK_K_DATA_VNPY]
            cl_stock_code_vnpy.ensure_index([('symbol', ASCENDING), ('exchange', ASCENDING), ('interval', ASCENDING), ('datetime', ASCENDING)], unique=True)
            cl_stock_code_vnpy.replace_one(flt_vnpy, d_db_vnpy, upsert=True)

    def _initKData(self, code, k_data):
        # 注意：所有的数据库数据和列表数据都按照日期的正序排序(从小到大)
        """
        初始化股票数据库数据
        @:param code  股票(指数)代码                
        """     
        if len(k_data) != 0:
            last_5_vol = deque([0.0] * 5)
            last_5_amount = deque([0.0] * 5)
            k_data = k_data.sort_values(by='trade_date')
            code = code.replace('.', '_')
            cl_stock_code = self.db[code]
            cl_stock_code.ensure_index([('trade_date', ASCENDING)], unique=True)
            am = ArrayManager(size=600)
            for ix, row in k_data.iterrows():
                d = row.to_dict()              
                if 0.0 not in last_5_vol:
                    vol_rate = d['vol'] / (sum(last_5_vol) / 5.0)                
                    amount_rate = d['amount'] / (sum(last_5_amount) / 5.0)   
                    d['vol_rate'] = vol_rate
                    d['amount_rate'] = amount_rate 
                else:
                    d['vol_rate'] = 0.0
                    d['amount_rate'] = 0.0
                last_5_vol.popleft()
                last_5_amount.popleft()
                last_5_vol.append(d['vol'])
                last_5_amount.append(d['amount'])                   
                if self._is_in_vnpy_db(d['ts_code'], update=False):
                    # 构建vnpy股票数据库数据
                    self._build_db_vnpy(d)               
                d['ts_code'] = d['ts_code'].replace('.', '_')
                if d['ts_code'][-3:] == '_SH':
                    exchange = Exchange.SSE
                    d['exchange'] = 'SSE'
                else:
                    exchange = Exchange.SZSE                
                    d['exchange'] = 'SZSE'
                bar = BarData(
                    gateway_name='ctp', symbol=d['ts_code'],
                    exchange=exchange,
                    datetime=string_to_datetime(d['trade_date']))
                bar.symbol = d['ts_code']
                bar.volume = d['vol']
                bar.open_price = d['open']
                bar.high_price = d['high']
                bar.low_price = d['low']
                bar.close_price = d['close']
                am.update_bar(bar)
                try:
                    d['ma_5'] = am.sma(5)
                except:
                    traceback.print_exc()                    
                    self.log.error('************************')
                    self.log.error(d['ts_code'])
                    self.log.error(d['trade_date'])
                    self.log.error(bar)
                d['ma_10'] = am.sma(10)
                d['ma_20'] = am.sma(20)
                d['ma_30'] = am.sma(30)
                d['ma_60'] = am.sma(60)
                d['ma_120'] = am.sma(120)
                d['ma_250'] = am.sma(250)
                d['ma_500'] = am.sma(500)
                d['high_5'] = np.max(am.high[-5:])
                d['high_10'] = np.max(am.high[-10:])
                d['high_20'] = np.max(am.high[-20:])
                d['high_30'] = np.max(am.high[-30:])
                d['high_60'] = np.max(am.high[-60:])
                d['high_120'] = np.max(am.high[-120:])
                d['high_250'] = np.max(am.high[-250:])
                d['high_500'] = np.max(am.high[-500:])
                d['low_5'] = np.min(am.low[-5:])
                d['low_10'] = np.min(am.low[-10:])
                d['low_20'] = np.min(am.low[-20:])
                d['low_30'] = np.min(am.low[-30:])
                d['low_60'] = np.min(am.low[-60:])
                d['low_120'] = np.min(am.low[-120:])
                d['low_250'] = np.min(am.low[-250:])
                d['low_500'] = np.min(am.low[-500:])
                flt = {'trade_date': d['trade_date']}
                cl_stock_code.replace_one(flt, d, upsert=True)

    def _updateKData(self, code, k_data):
        # 注意：所有的数据库数据和列表数据都按照日期的正序排序(从小到大)
        """
        更新股票，股指每日数据（行情，K线，市值等0）
        @:param code  股票(指数)代码                
        @:param k_data  ts中获取的最新df数据
        """        
        if len(k_data) != 0:
            k_data = k_data.sort_values(by='trade_date')
            code = code.replace('.', '_')
            cl_stock_code = self.db[code]
            cl_stock_code.ensure_index([('trade_date', ASCENDING)], unique=True)
            # 更新k线数据
            # 1、新增日K线入库
            # 2、遍历数据库找出最近的500+22(必须保证更新数据操作在22天以内进行)条数据并更新最后的22条的ma和最高 最低价
            for ix, row in k_data.iterrows():
                d = row.to_dict()
                if self._is_in_vnpy_db(d['ts_code'], update=True):
                    # 更新vnpy数据库数据                    
                    self._build_db_vnpy(d)
                flt = {'trade_date': d['trade_date']}
                cl_stock_code.replace_one(flt, d, upsert=True)
            lst_close = list()
            lst_high = list()
            lst_low = list()
            lst_trade_date = list()
            rec = cl_stock_code.find({}).sort("trade_date", DESCENDING).limit(522)
            rec = list(rec)
            rec.reverse()
            am = ArrayManager(size=600)
            last_5_vol = deque([0.0] * 5)
            last_5_amount = deque([0.0] * 5)
            for d in rec:
                if 0.0 not in last_5_vol:
                    vol_rate = d['vol'] / (sum(last_5_vol) / 5.0)               
                    amount_rate = d['amount'] / (sum(last_5_amount) / 5.0)   
                    d['vol_rate'] = vol_rate
                    d['amount_rate'] = amount_rate 
                else:
                    d['vol_rate'] = 0.0
                    d['amount_rate'] = 0.0
                last_5_vol.popleft()
                last_5_amount.popleft()
                last_5_vol.append(d['vol'])
                last_5_amount.append(d['amount'])                       
                d['ts_code'] = d['ts_code'].replace('.', '_')
                if d['ts_code'][-3:] == '_SH':
                    exchange = Exchange.SSE
                    d['exchange'] = 'SSE'
                else:
                    exchange = Exchange.SZSE
                    d['exchange'] = 'SZSE'                
                bar = BarData(
                    gateway_name='ctp', symbol=d['ts_code'],
                    exchange=exchange,
                    datetime=string_to_datetime(d['trade_date']))
                bar.symbol = d['ts_code']
                bar.volume = d['vol']
                bar.open_price = d['open']
                bar.high_price = d['high']
                bar.low_price = d['low']
                bar.close_price = d['close']
                am.update_bar(bar)
                if d['trade_date'] >= self.db_date:
                    d['ma_5'] = am.sma(5)
                    d['ma_10'] = am.sma(10)
                    d['ma_20'] = am.sma(20)
                    d['ma_30'] = am.sma(30)
                    d['ma_60'] = am.sma(60)
                    d['ma_120'] = am.sma(120)
                    d['ma_250'] = am.sma(250)
                    d['ma_500'] = am.sma(500)
                    d['high_5'] = np.max(am.high[-5:])
                    d['high_10'] = np.max(am.high[-10:])
                    d['high_20'] = np.max(am.high[-20:])
                    d['high_30'] = np.max(am.high[-30:])
                    d['high_60'] = np.max(am.high[-60:])
                    d['high_120'] = np.max(am.high[-120:])
                    d['high_250'] = np.max(am.high[-250:])
                    d['high_500'] = np.max(am.high[-500:])
                    d['low_5'] = np.min(am.low[-5:])
                    d['low_10'] = np.min(am.low[-10:])
                    d['low_20'] = np.min(am.low[-20:])
                    d['low_30'] = np.min(am.low[-30:])
                    d['low_60'] = np.min(am.low[-60:])
                    d['low_120'] = np.min(am.low[-120:])
                    d['low_250'] = np.min(am.low[-250:])
                    d['low_500'] = np.min(am.low[-500:])
                    flt = {'trade_date': d['trade_date']}
                    cl_stock_code.replace_one(flt, d, upsert=True)

    def _buildTradeCal(self):
        self.log.info('构建交易日日历数据')
        df_trade_cal = self.pro.trade_cal(
            exchange='', start_date=DATA_BEGIN_DATE, end_date=self.date_now)
        cl_trade_cal = self.db[CL_TRADE_CAL]
        cl_trade_cal.ensure_index([('cal_date', ASCENDING)], unique=True)
        for ix, row in df_trade_cal.iterrows():
            d = row.to_dict()
            flt = {'cal_date': d['cal_date']}
            cl_trade_cal.replace_one(flt, d, upsert=True)
        self.log.info('构建交易日日历数据完成')

    def buildStockData(self, update=True):
        self._buildTradeCal()
        self._buildBasic()        
        self._buildIndex(update)
        self._build_top_list()        
        self.log.info('构建股票日K线数据')
        start = time()
        cl_stock_basic = self.db[CL_STOCK_BASIC]
        stock_basic_lst = cl_stock_basic.find(
            {}, {'_id': 0}).sort("ts_code", ASCENDING)
        for d in stock_basic_lst:    
            df_stock_k_data = self._getDailyKDataFromTs(d['ts_code'], update)       
            df_stock_daily_basic = self._getDailyBasicFromTs(d['ts_code'], update)   
            if df_stock_k_data.empty is False and df_stock_daily_basic.empty is False:
                df_stock_info = pd.merge(df_stock_k_data, df_stock_daily_basic)
            if d['list_date'] < self.date_now:
                if update is True:
                    self._updateKData(d['ts_code'], df_stock_info)
                else:
                    self._initKData(d['ts_code'], df_stock_info)
        # 数据更新时间
        cl_stock_db_date = self.db[CL_STOCK_DATE]
        db_date = {'db_date': self.date_now}
        flt_date = {'db_date': self.db_date}
        cl_stock_db_date.replace_one(flt_date, db_date, upsert=True)
        end = time()
        cost = (end - start)/3600
        self.log.info('构建股票日K线数据完成，耗时%s小时' % cost)

    def _buildIndex(self, update=True):
        self.log.info('构建指数K线数据')        
        for code in self.index_lst:
            df_index = self._getIndexDailyKDataFromTs(code, update)        
            if df_index.empty is False:
                if update is True:            
                    self._updateKData(code, df_index)
                else:
                    self._initKData(code, df_index)
        self.log.info('构建指数K线数据完成')

    def _build_top_list(self):
        # 构建龙虎榜数据                
        self.log.info('构建龙虎榜数据')     
        begin_date = '20050101' if self.db_date < '20050101' else self.db_date  # 龙虎榜数据只有2005年之后的数据
        trade_lst = self.getTradeCal(begin_date)
        for item_date in trade_lst:
            df_top_list = self.pro.top_list(trade_date=item_date)
            sleep(1)
            if df_top_list.size != 0:
                for ix_top_list, row_top_list in df_top_list.iterrows():
                    d_top_list = row_top_list.to_dict()
                    cl_stk_top_list = self.db[CL_STK_TOP_LIST]
                    flt_top_list = {'trade_date': item_date, 'ts_code': d_top_list['ts_code'].replace('.', '_')}
                    cl_stk_top_list.replace_one(flt_top_list, d_top_list, upsert=True)  
        self.log.info('构建龙虎榜数据完成')

    def _buildBasic(self):
        self.log.info('构建股票基础信息')
        data = self.pro.stock_basic(
            exchange='', list_status='L',
            fields='ts_code,symbol,name,area,industry,market,list_date')
        cl_stock_basic = self.db[CL_STOCK_BASIC]
        cl_stock_basic.ensure_index([('ts_code', ASCENDING)], unique=True)
        for ix, row in data.iterrows():
            d = row.to_dict()
            flt = {'ts_code': d['ts_code']}
            cl_stock_basic.replace_one(flt, d, upsert=True)
        self.log.info('构建股票基础信息完成')

    def _getDailyBasicFromTs(self, code, update=True):        
        start_date = DATA_BEGIN_DATE
        if update is True:
            start_date = self.db_date
        count = 0        
        while(True):
            try:
                df_daily_basic = self.pro.daily_basic(
                    ts_code=code, start_date=start_date, end_date=self.date_now)     
                if df_daily_basic is not None:
                    break
                else:                    
                    self.log.info('(%s)调用tushare pro.daily_basic失败，空数据' % (code))
                    break
            except:
                count += 1
                self.log.info('(%s)调用tushare pro.daily_basic失败，重试次数：%s' % (code, count))
                if count > self.count_max_retry:
                    break
                sleep(self.second_sleep)  
        if df_daily_basic is None:
            df_daily_basic = pd.DataFrame()                
            df_daily_basic.fillna(0.0, inplace=True)
        return df_daily_basic  

    def _getDailyKDataFromTs(self, code, update=True):        
        start_date = DATA_BEGIN_DATE
        if update is True:
            start_date = self.db_date
        count = 0        
        while(True):
            try:
                df_k_data = ts.pro_bar(
                    ts_code=code, adj='qfq', start_date=start_date,
                    end_date=self.date_now)  
                if df_k_data is not None:
                    break
                else:                    
                    self.log.info('(%s)调用tushare ts.pro_bar失败，空数据' % (code))
                    break
            except:
                count += 1
                self.log.info('(%s)调用tushare ts.pro_bar失败，重试次数：%s' % (code, count))
                if count > self.count_max_retry:
                    break
                sleep(self.second_sleep)
        if df_k_data is None:
            df_k_data = pd.DataFrame()
        df_k_data.fillna(0.0, inplace=True)
        return df_k_data

    def _getIndexDailyKDataFromTs(self, code, update=True):                
        start_date = DATA_BEGIN_DATE
        if update is True:
            start_date = self.db_date
        count = 0        
        while(True):
            try:
                df_index_k_data = self.pro.index_daily(
                    ts_code=code, start_date=self.db_date,
                    end_date=self.date_now)   
                if df_index_k_data is not None:
                    break
                else:                    
                    self.log.info('(%s)调用tushare pro.index_daily失败，空数据' %(code))
                    break
            except:
                count += 1
                self.log.info('(%s)调用tushare pro.index_daily失败，重试次数：%s' % (code, count))
                if count > self.count_max_retry:
                    break
                sleep(self.second_sleep)    
        if df_index_k_data is None:
            df_index_k_data = pd.DataFrame()            
        df_index_k_data.fillna(0.0, inplace=True)
        return df_index_k_data

    def getStockPriceInfo(self, code, date):
        cl_stock_code = self.db[code]
        stock_price_info = cl_stock_code.find_one(
            {'trade_date': date}, {'_id': 0})
        return stock_price_info

    def getStockPriceLst(self, code, date):
        cl_stock_code = self.db[code]
        stock_price_lst = cl_stock_code.find(
            {'trade_date': {"$gte": date}}, {'_id': 0}).sort("trade_date")
        return stock_price_lst

    def getStockBasicInfo(self, code):
        cl_stock_basic = self.db[CL_STOCK_BASIC]
        stock_basic_info = cl_stock_basic.find_one(
            {'ts_code': code}, {'_id': 0})
        return stock_basic_info

    def getTradeCal(self, begin_date, end_date=None):
        cl_cal = self.db[CL_TRADE_CAL]
        if end_date is None:
            trade_cal = cl_cal.find(
                {'cal_date': {"$gte": begin_date}, 'is_open': 1},
                {'_id': 0}).sort("cal_date")
        else:
            trade_cal = cl_cal.find(
                {'cal_date': {"$gte": begin_date, '$lte': end_date},
                    'is_open': 1}, {'_id': 0}).sort("cal_date")
        trade_cal_lst = list()
        for item in trade_cal:
            trade_cal_lst.append(item['cal_date'])
        return trade_cal_lst

    def get_next_trade_date(self, trade_date):
        # 获取下一个交易日
        cl_cal = self.db[CL_TRADE_CAL]
        trade_cal = cl_cal.find(
            {'cal_date': {"$gt": trade_date}, 'is_open': 1},
            {'_id': 0}).sort("cal_date")
        return list(trade_cal)[0]['cal_date']

    def getStockTopList(self, date):
        cl_stock_top_list = self.db[CL_STK_TOP_LIST]
        top_list = cl_stock_top_list.find(
            {'trade_date': date}, {'_id': 0})
        stock_top_list = list()
        for item in top_list:
            stock_top_list.append(item)       
        return stock_top_list

if __name__ == "__main__":
    ds_tushare = DataServiceTushare()
    ds_tushare.buildStockData(update=False)
