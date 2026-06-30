//+------------------------------------------------------------------+
//| CTradeMonitor.mqh — TP1 partial, BE stop, TP2, time-stop mgmt  |
//+------------------------------------------------------------------+
#ifndef CTRADE_MONITOR_MQH
#define CTRADE_MONITOR_MQH

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include "CSignalReceiver.mqh"

class CTradeMonitor
{
private:
   CTrade         m_trade;
   CPositionInfo  m_pos;
   string         m_ipc_dir;
   ulong          m_magic;
   double         m_pip;           // 0.0001 for 4-digit pairs
   bool           m_tp1_hit;
   double         m_tp2_price;
   ulong          m_ticket;

public:
   CTradeMonitor(const string ipc_dir, const ulong magic = 20240101)
      : m_ipc_dir(ipc_dir), m_magic(magic), m_tp1_hit(false),
        m_tp2_price(0.0), m_ticket(0)
   {
      m_pip = 0.0001;
      m_trade.SetExpertMagicNumber(magic);
      m_trade.SetAsyncMode(false);
   }

   void SetActiveTrade(const ulong ticket, const double tp2_price)
   {
      m_ticket    = ticket;
      m_tp2_price = tp2_price;
      m_tp1_hit   = false;
   }

   void ClearActiveTrade()
   {
      m_ticket    = 0;
      m_tp2_price = 0.0;
      m_tp1_hit   = false;
   }

   bool HasActiveTrade() const { return m_ticket > 0; }

   // Called on every tick. Returns true when the trade has fully closed.
   bool Manage()
   {
      if(m_ticket == 0)
         return false;

      if(!m_pos.SelectByTicket(m_ticket))
      {
         // Position gone — closed externally (SL/TP hit server-side)
         _WriteExecEvent("TRADE_CLOSED", m_ticket, 0.0, "CLOSED_EXTERNAL");
         ClearActiveTrade();
         return true;
      }

      string direction = m_pos.PositionType() == POSITION_TYPE_BUY ? "BUY" : "SELL";
      double sl        = m_pos.StopLoss();
      double tp1       = m_pos.TakeProfit();
      double entry     = m_pos.PriceOpen();
      double bid       = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask       = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double price     = direction == "BUY" ? bid : ask;

      // Time stop: close fully at 20:00 UTC
      MqlDateTime dt;
      TimeToStruct(TimeGMT(), dt);
      if(dt.hour >= 20)
      {
         _CloseAll("TIME_STOP");
         return true;
      }

      if(!m_tp1_hit)
      {
         bool tp1_reached = (direction == "BUY"  && bid >= tp1) ||
                            (direction == "SELL" && ask <= tp1);
         if(tp1_reached)
            _HandleTP1(direction, entry, tp1);
      }
      else
      {
         // Runner management: TP2 or trailing stop breach
         bool tp2_reached = (direction == "BUY"  && bid >= m_tp2_price) ||
                            (direction == "SELL" && ask <= m_tp2_price);
         if(tp2_reached)
         {
            _CloseAll("WIN_TP2");
            return true;
         }

         // Trailing stop breach (SL already moved to BE)
         bool ts_breach = (direction == "BUY"  && bid <= sl) ||
                          (direction == "SELL" && ask >= sl);
         if(ts_breach)
         {
            _CloseAll("WIN_TP1_RUNNER_TS");
            return true;
         }
      }
      return false;
   }

private:
   void _HandleTP1(const string direction, const double entry, const double tp1_price)
   {
      // Close half the position at TP1
      double lots = m_pos.Volume();
      double half = NormalizeDouble(lots / 2.0,
                       (int)SymbolInfoInteger(_Symbol, SYMBOL_VOLUME_STEP));
      if(half <= 0) half = lots;

      if(direction == "BUY")
         m_trade.Sell(half, _Symbol, 0, 0, 0, "TP1_PARTIAL");
      else
         m_trade.Buy(half, _Symbol, 0, 0, 0, "TP1_PARTIAL");

      // Move SL to breakeven + 1 pip
      double be_sl = direction == "BUY"
                     ? entry + m_pip
                     : entry - m_pip;
      be_sl = NormalizeDouble(be_sl, _Digits);

      m_trade.PositionModify(m_ticket, be_sl, 0);
      m_tp1_hit = true;

      _WriteExecEvent("TRADE_PARTIAL", m_ticket, half, "TP1_HIT");
      Print("CTradeMonitor: TP1 hit — partial closed, SL moved to BE+1pip");
   }

   void _CloseAll(const string reason)
   {
      m_trade.PositionClose(m_ticket);
      _WriteExecEvent("TRADE_CLOSED", m_ticket, 0.0, reason);
      Print("CTradeMonitor: position closed reason=", reason);
      ClearActiveTrade();
   }

   void _WriteExecEvent(const string event_type, const ulong ticket,
                         const double lots, const string reason)
   {
      string ts = IntegerToString((long)TimeCurrent());
      string filename = m_ipc_dir + "\\exec_events\\" + ts + "_" +
                        IntegerToString((long)ticket) + "_" + reason + ".json";

      string json = "{\"event_type\":\"" + event_type + "\","
                  + "\"ticket\":" + IntegerToString((long)ticket) + ","
                  + "\"lots_closed\":" + DoubleToString(lots, 2) + ","
                  + "\"reason\":\"" + reason + "\","
                  + "\"ts\":" + ts + "}";

      int fh = FileOpen(filename, FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(fh != INVALID_HANDLE)
      {
         FileWriteString(fh, json);
         FileClose(fh);
      }
   }
};

#endif // CTRADE_MONITOR_MQH
