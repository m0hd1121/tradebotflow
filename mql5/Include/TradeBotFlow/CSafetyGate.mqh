//+------------------------------------------------------------------+
//| CSafetyGate.mqh — Pre-trade safety checks before order send     |
//+------------------------------------------------------------------+
#ifndef CSAFETY_GATE_MQH
#define CSAFETY_GATE_MQH

class CSafetyGate
{
private:
   string   m_ipc_dir;
   double   m_max_spread_pips;
   double   m_min_free_margin;

public:
   CSafetyGate(const string ipc_dir,
               const double max_spread_pips = 2.0,
               const double min_free_margin = 500.0)
      : m_ipc_dir(ipc_dir),
        m_max_spread_pips(max_spread_pips),
        m_min_free_margin(min_free_margin) {}

   // Returns true if safe to trade; populates reason on failure.
   bool IsSafe(string &reason)
   {
      if(_KillFlagSet())
      {
         reason = "KILL_FLAG";
         return false;
      }
      if(!_MarginOk())
      {
         reason = "LOW_MARGIN";
         return false;
      }
      if(!_SpreadOk())
      {
         reason = "SPREAD_TOO_WIDE";
         return false;
      }
      if(!_MarketOpen())
      {
         reason = "MARKET_CLOSED";
         return false;
      }
      reason = "";
      return true;
   }

private:
   bool _KillFlagSet()
   {
      string path = m_ipc_dir + "\\kill_flag.txt";
      int fh = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI);
      if(fh == INVALID_HANDLE)
         return false;
      string content = FileReadString(fh);
      FileClose(fh);
      return StringLen(StringTrimLeft(StringTrimRight(content))) > 0;
   }

   bool _MarginOk()
   {
      return AccountInfoDouble(ACCOUNT_MARGIN_FREE) >= m_min_free_margin;
   }

   bool _SpreadOk()
   {
      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      long spread_pts = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      double spread_pips = (double)spread_pts * point / 0.0001;
      return spread_pips <= m_max_spread_pips;
   }

   bool _MarketOpen()
   {
      datetime session_open, session_close;
      if(!SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)DayOfWeek(),
                                  0, session_open, session_close))
         return true; // Can't determine — allow
      datetime now = TimeCurrent() % 86400;
      return now >= session_open && now < session_close;
   }
};

#endif // CSAFETY_GATE_MQH
