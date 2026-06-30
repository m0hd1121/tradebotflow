//+------------------------------------------------------------------+
//| CHeartbeatLogger.mqh — Writes periodic heartbeat to IPC dir     |
//+------------------------------------------------------------------+
#ifndef CHEARTBEAT_LOGGER_MQH
#define CHEARTBEAT_LOGGER_MQH

class CHeartbeatLogger
{
private:
   string   m_ipc_dir;
   int      m_interval_s;
   datetime m_last_beat;

public:
   CHeartbeatLogger(const string ipc_dir, const int interval_s = 15)
      : m_ipc_dir(ipc_dir), m_interval_s(interval_s), m_last_beat(0) {}

   void Tick()
   {
      datetime now = TimeCurrent();
      if((int)(now - m_last_beat) < m_interval_s)
         return;
      m_last_beat = now;
      _Write(now);
   }

   void ForceWrite()
   {
      _Write(TimeCurrent());
   }

private:
   void _Write(const datetime ts)
   {
      string path = m_ipc_dir + "\\heartbeat.txt";
      int fh = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI);
      if(fh == INVALID_HANDLE)
         return;
      FileWriteString(fh, IntegerToString((long)ts));
      FileClose(fh);
   }
};

#endif // CHEARTBEAT_LOGGER_MQH
