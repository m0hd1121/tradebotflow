//+------------------------------------------------------------------+
//| CSignalReceiver.mqh — Reads and validates the IPC signal file   |
//+------------------------------------------------------------------+
#ifndef CSIGNAL_RECEIVER_MQH
#define CSIGNAL_RECEIVER_MQH

#include "CConfigReader.mqh"   // Reuse _ExtractField/_ExtractDouble/_VerifyHMAC pattern

struct SSignal
{
   string   direction;       // "BUY" or "SELL"
   string   grade;           // "A+" or "STANDARD"
   string   session;
   double   entry_price;
   double   stop_price;
   double   tp1_price;
   double   tp2_price;
   double   stop_pips;
   double   planned_rr;
   double   lots;
   double   risk_pct_used;
   long     signal_ts;       // Unix epoch seconds
   bool     valid;
};

class CSignalReceiver
{
private:
   string   m_path;
   string   m_consumed_path;
   long     m_last_signal_ts;

public:
   CSignalReceiver(const string ipc_dir) : m_last_signal_ts(0)
   {
      m_path          = ipc_dir + "\\signal.json";
      m_consumed_path = ipc_dir + "\\signal.consumed";
   }

   // Returns true when a fresh, unread signal is available.
   bool Poll(SSignal &sig)
   {
      if(!_FileExists(m_path))
         return false;
      if(_FileExists(m_consumed_path))
         return false;   // Already consumed

      int fh = FileOpen(m_path, FILE_READ | FILE_TXT | FILE_ANSI);
      if(fh == INVALID_HANDLE)
         return false;

      string raw = "";
      while(!FileIsEnding(fh))
         raw += FileReadString(fh);
      FileClose(fh);

      if(!_VerifyHMAC(raw))
      {
         Print("CSignalReceiver: HMAC failure — signal rejected");
         return false;
      }

      sig = _Parse(raw);
      if(!sig.valid)
         return false;

      // Guard against replaying old signals (within same session)
      if(sig.signal_ts <= m_last_signal_ts)
         return false;

      return true;
   }

   // Call after successfully acting on the signal to prevent re-entry.
   void MarkConsumed(const long signal_ts)
   {
      m_last_signal_ts = signal_ts;
      int fh = FileOpen(m_consumed_path, FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(fh != INVALID_HANDLE)
      {
         FileWriteString(fh, IntegerToString(signal_ts));
         FileClose(fh);
      }
   }

   // Call to clear consumed state (e.g. after a new signal replaces the old one).
   void ClearConsumed()
   {
      if(_FileExists(m_consumed_path))
         FileDelete(m_consumed_path);
   }

private:
   bool _FileExists(const string &path)
   {
      int fh = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI);
      if(fh == INVALID_HANDLE) return false;
      FileClose(fh);
      return true;
   }

   bool _VerifyHMAC(const string &raw)
   {
      string sig = _ExtractField(raw, "\"sig\"");
      if(StringLen(sig) == 0) return false;

      int sig_pos   = StringFind(raw, "\"sig\"");
      int comma_pos = sig_pos - 1;
      while(comma_pos > 0 && StringGetCharacter(raw, comma_pos) != ',')
         comma_pos--;
      string payload = StringSubstr(raw, 0, comma_pos) + "}";

      uchar key_arr[], msg_arr[], result_arr[];
      StringToCharArray(HMAC_KEY, key_arr, 0, WHOLE_ARRAY, CP_UTF8);
      StringToCharArray(payload, msg_arr, 0, WHOLE_ARRAY, CP_UTF8);
      if(!CryptEncode(CRYPT_HASH_SHA256_HMAC, msg_arr, key_arr, result_arr))
         return false;

      string computed = "";
      for(int i = 0; i < ArraySize(result_arr); i++)
      {
         string h = IntegerToString(result_arr[i], 2, '0');
         StringToLower(h);
         computed += h;
      }
      return computed == sig;
   }

   SSignal _Parse(const string &raw)
   {
      SSignal s;
      s.direction    = _ExtractField(raw, "\"direction\"");
      s.grade        = _ExtractField(raw, "\"grade\"");
      s.session      = _ExtractField(raw, "\"session\"");
      s.entry_price  = _ExtractDouble(raw, "\"entry_price\"");
      s.stop_price   = _ExtractDouble(raw, "\"stop_price\"");
      s.tp1_price    = _ExtractDouble(raw, "\"tp1_price\"");
      s.tp2_price    = _ExtractDouble(raw, "\"tp2_price\"");
      s.stop_pips    = _ExtractDouble(raw, "\"stop_pips\"");
      s.planned_rr   = _ExtractDouble(raw, "\"planned_rr\"");
      s.lots         = _ExtractDouble(raw, "\"lots\"");
      s.risk_pct_used = _ExtractDouble(raw, "\"risk_pct_used\"");
      s.signal_ts    = (long)_ExtractDouble(raw, "\"signal_ts\"");
      s.valid = StringLen(s.direction) > 0 && s.lots > 0.0 && s.entry_price > 0.0;
      return s;
   }

   string _ExtractField(const string &raw, const string &key)
   {
      int pos = StringFind(raw, key);
      if(pos < 0) return "";
      int colon = StringFind(raw, ":", pos);
      if(colon < 0) return "";
      int start = colon + 1;
      while(start < StringLen(raw) && StringGetCharacter(raw, start) == ' ')
         start++;
      if(StringGetCharacter(raw, start) == '"')
      {
         start++;
         int end = StringFind(raw, "\"", start);
         if(end < 0) return "";
         return StringSubstr(raw, start, end - start);
      }
      int end = start;
      while(end < StringLen(raw))
      {
         ushort ch = StringGetCharacter(raw, end);
         if(ch == ',' || ch == '}' || ch == '\n') break;
         end++;
      }
      return StringTrimRight(StringTrimLeft(StringSubstr(raw, start, end - start)));
   }

   double _ExtractDouble(const string &raw, const string &key)
   {
      string val = _ExtractField(raw, key);
      return StringLen(val) > 0 ? StringToDouble(val) : 0.0;
   }
};

#endif // CSIGNAL_RECEIVER_MQH
