//+------------------------------------------------------------------+
//| CTradeExecutor.mqh — OrderSend with retry and fill validation   |
//+------------------------------------------------------------------+
#ifndef CTRADE_EXECUTOR_MQH
#define CTRADE_EXECUTOR_MQH

#include <Trade\Trade.mqh>
#include "CSignalReceiver.mqh"

class CTradeExecutor
{
private:
   CTrade   m_trade;
   string   m_ipc_dir;
   int      m_max_retries;
   int      m_retry_delay_ms;
   double   m_max_slippage_pips;

public:
   CTradeExecutor(const string ipc_dir,
                  const int    max_retries      = 3,
                  const int    retry_delay_ms   = 500,
                  const double max_slippage_pips = 1.0)
      : m_ipc_dir(ipc_dir),
        m_max_retries(max_retries),
        m_retry_delay_ms(retry_delay_ms),
        m_max_slippage_pips(max_slippage_pips)
   {
      m_trade.SetExpertMagicNumber(20240101);
      m_trade.SetDeviationInPoints((ulong)(max_slippage_pips * 10));
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
      m_trade.SetAsyncMode(false);
   }

   // Returns ticket on success, 0 on failure.
   ulong Execute(const SSignal &sig)
   {
      ENUM_ORDER_TYPE order_type = sig.direction == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

      double entry = NormalizeDouble(sig.entry_price, _Digits);
      double sl    = NormalizeDouble(sig.stop_price, _Digits);
      double tp1   = NormalizeDouble(sig.tp1_price, _Digits);

      for(int attempt = 1; attempt <= m_max_retries; attempt++)
      {
         bool sent;
         if(order_type == ORDER_TYPE_BUY)
            sent = m_trade.Buy(sig.lots, _Symbol, entry, sl, tp1);
         else
            sent = m_trade.Sell(sig.lots, _Symbol, entry, sl, tp1);

         if(sent)
         {
            ulong ticket = m_trade.ResultOrder();
            if(ticket > 0)
            {
               Print("CTradeExecutor: order filled ticket=", ticket,
                     " lots=", sig.lots, " entry=", entry,
                     " sl=", sl, " tp1=", tp1);
               _WriteExecEvent("ORDER_SENT", ticket, sig);
               return ticket;
            }
         }

         int err = GetLastError();
         Print("CTradeExecutor: attempt ", attempt, " failed code=", err,
               " retcode=", m_trade.ResultRetcode());

         if(attempt < m_max_retries)
            Sleep(m_retry_delay_ms * attempt);
      }

      Print("CTradeExecutor: all retries exhausted — order not placed");
      return 0;
   }

private:
   void _WriteExecEvent(const string event_type, const ulong ticket, const SSignal &sig)
   {
      string ts = IntegerToString((long)TimeCurrent());
      string filename = m_ipc_dir + "\\exec_events\\" + ts + "_" +
                        IntegerToString((long)ticket) + ".json";

      string json = "{\"event_type\":\"" + event_type + "\","
                  + "\"ticket\":" + IntegerToString((long)ticket) + ","
                  + "\"direction\":\"" + sig.direction + "\","
                  + "\"lots\":" + DoubleToString(sig.lots, 2) + ","
                  + "\"entry_price\":" + DoubleToString(sig.entry_price, _Digits) + ","
                  + "\"stop_price\":" + DoubleToString(sig.stop_price, _Digits) + ","
                  + "\"tp1_price\":" + DoubleToString(sig.tp1_price, _Digits) + ","
                  + "\"tp2_price\":" + DoubleToString(sig.tp2_price, _Digits) + ","
                  + "\"ts\":" + ts + "}";

      int fh = FileOpen(filename, FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(fh != INVALID_HANDLE)
      {
         FileWriteString(fh, json);
         FileClose(fh);
      }
   }
};

#endif // CTRADE_EXECUTOR_MQH
