//+------------------------------------------------------------------+
//| CConfigReader.mqh — HMAC-verified JSON config loader            |
//+------------------------------------------------------------------+
#ifndef CCONFIG_READER_MQH
#define CCONFIG_READER_MQH

// Compile-time HMAC key — must match Python .env HMAC_SECRET.
// Replace this placeholder before deploying; never commit the real key.
#ifndef HMAC_KEY
#define HMAC_KEY "CHANGE_ME_BEFORE_DEPLOY"
#endif

struct SConfig
{
   double   risk_pct;
   double   daily_loss_limit_pct;
   double   weekly_loss_limit_pct;
   double   monthly_loss_limit_pct;
   double   max_drawdown_pct;
   int      max_trades_per_day;
   int      max_consecutive_losses;
   double   pip_value_per_lot;
   bool     circuit_breaker_locked;
   string   disabled_sessions;   // CSV e.g. "ASIAN,LONDON_OPEN"
   string   disabled_grades;     // CSV e.g. "STANDARD"
};

class CConfigReader
{
private:
   string    m_path;
   SConfig   m_cfg;
   datetime  m_loaded_at;
   bool      m_valid;

public:
   CConfigReader(const string ipc_dir) : m_loaded_at(0), m_valid(false)
   {
      m_path = ipc_dir + "\\config.json";
   }

   bool Load()
   {
      int fh = FileOpen(m_path, FILE_READ | FILE_TXT | FILE_ANSI);
      if(fh == INVALID_HANDLE)
         return false;

      string raw = "";
      while(!FileIsEnding(fh))
         raw += FileReadString(fh);
      FileClose(fh);

      if(!_VerifyHMAC(raw))
      {
         Print("CConfigReader: HMAC verification failed — config rejected");
         return false;
      }

      m_valid = _Parse(raw);
      if(m_valid)
         m_loaded_at = TimeCurrent();
      return m_valid;
   }

   bool IsValid() const { return m_valid; }
   datetime LoadedAt() const { return m_loaded_at; }
   const SConfig& Get() const { return m_cfg; }

private:
   // Minimal HMAC-SHA256 verification using MT5's built-in CryptEncode.
   // Payload is JSON body without the "sig" field value.
   bool _VerifyHMAC(const string &raw)
   {
      // Extract sig field
      string sig = _ExtractField(raw, "\"sig\"");
      if(StringLen(sig) == 0)
         return false;

      // Rebuild payload: everything before ,"sig": ... }
      int sig_pos = StringFind(raw, "\"sig\"");
      if(sig_pos < 0)
         return false;

      // Find the comma before "sig" to strip the field
      string payload = "";
      int comma_pos = sig_pos - 1;
      while(comma_pos > 0 && StringGetCharacter(raw, comma_pos) != ',')
         comma_pos--;
      payload = StringSubstr(raw, 0, comma_pos) + "}";

      uchar key_arr[];
      uchar msg_arr[];
      uchar result_arr[];

      StringToCharArray(HMAC_KEY, key_arr, 0, WHOLE_ARRAY, CP_UTF8);
      StringToCharArray(payload, msg_arr, 0, WHOLE_ARRAY, CP_UTF8);

      // MQL5's CryptEncode with CRYPT_HASH_SHA256_HMAC
      if(!CryptEncode(CRYPT_HASH_SHA256_HMAC, msg_arr, key_arr, result_arr))
         return false;

      string computed = "";
      for(int i = 0; i < ArraySize(result_arr); i++)
      {
         string byte_hex = IntegerToString(result_arr[i], 2, '0');
         StringToLower(byte_hex);
         computed += byte_hex;
      }

      return computed == sig;
   }

   bool _Parse(const string &raw)
   {
      m_cfg.risk_pct               = _ExtractDouble(raw, "\"risk_pct\"");
      m_cfg.daily_loss_limit_pct   = _ExtractDouble(raw, "\"daily_loss_limit_pct\"");
      m_cfg.weekly_loss_limit_pct  = _ExtractDouble(raw, "\"weekly_loss_limit_pct\"");
      m_cfg.monthly_loss_limit_pct = _ExtractDouble(raw, "\"monthly_loss_limit_pct\"");
      m_cfg.max_drawdown_pct       = _ExtractDouble(raw, "\"max_drawdown_pct\"");
      m_cfg.pip_value_per_lot      = _ExtractDouble(raw, "\"pip_value_per_lot\"");
      m_cfg.max_trades_per_day     = (int)_ExtractDouble(raw, "\"max_trades_per_day\"");
      m_cfg.max_consecutive_losses = (int)_ExtractDouble(raw, "\"max_consecutive_losses\"");
      m_cfg.circuit_breaker_locked = _ExtractField(raw, "\"circuit_breaker_locked\"") == "true";
      m_cfg.disabled_sessions      = _ExtractField(raw, "\"disabled_sessions\"");
      m_cfg.disabled_grades        = _ExtractField(raw, "\"disabled_grades\"");
      return m_cfg.risk_pct > 0.0;
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
      // Boolean / number
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

#endif // CCONFIG_READER_MQH
