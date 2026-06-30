//+------------------------------------------------------------------+
//| TradeBotFlow.mq5 — Main EA: IPC-only execution, zero strategy   |
//|                                                                  |
//| All signal analysis lives in the Python engine. This EA:        |
//|  1. Reads heartbeat IPC to prove liveness                       |
//|  2. Loads HMAC-signed config.json on startup & every 60s        |
//|  3. Polls signal.json on each tick                              |
//|  4. Passes pre-trade safety gate before placing any order       |
//|  5. Manages open position (TP1 partial, BE, TP2, time-stop)    |
//|  6. Writes exec_events/*.json for Python consumption            |
//+------------------------------------------------------------------+
#property copyright "TradeBotFlow"
#property version   "1.00"
#property strict

#include "..\\..\\Include\\TradeBotFlow\\CHeartbeatLogger.mqh"
#include "..\\..\\Include\\TradeBotFlow\\CSafetyGate.mqh"
#include "..\\..\\Include\\TradeBotFlow\\CConfigReader.mqh"
#include "..\\..\\Include\\TradeBotFlow\\CSignalReceiver.mqh"
#include "..\\..\\Include\\TradeBotFlow\\CTradeExecutor.mqh"
#include "..\\..\\Include\\TradeBotFlow\\CTradeMonitor.mqh"

// ---- Input parameters -----------------------------------------------
input string IpcDir            = "C:\\TradeBotFlow\\ipc";   // IPC directory path
input double MaxSpreadPips     = 2.0;                        // Max allowed spread (pips)
input double MinFreeMargin     = 500.0;                      // Min free margin (account currency)
input double MaxSlippagePips   = 1.0;                        // Max slippage on entry (pips)
input int    HeartbeatIntervalS = 15;                        // Heartbeat write interval (seconds)
input int    ConfigReloadS     = 60;                         // Config reload interval (seconds)
// ---------------------------------------------------------------------

CHeartbeatLogger* g_hb      = NULL;
CSafetyGate*      g_gate    = NULL;
CConfigReader*    g_cfg_rdr = NULL;
CSignalReceiver*  g_sig_rcv = NULL;
CTradeExecutor*   g_exec    = NULL;
CTradeMonitor*    g_monitor = NULL;

ulong    g_active_ticket    = 0;
double   g_tp2_price        = 0.0;
datetime g_last_cfg_load    = 0;

int OnInit()
{
   Print("TradeBotFlow EA v1.00 starting on ", _Symbol, " ", EnumToString(Period()));

   g_hb      = new CHeartbeatLogger(IpcDir, HeartbeatIntervalS);
   g_gate    = new CSafetyGate(IpcDir, MaxSpreadPips, MinFreeMargin);
   g_cfg_rdr = new CConfigReader(IpcDir);
   g_sig_rcv = new CSignalReceiver(IpcDir);
   g_exec    = new CTradeExecutor(IpcDir, 3, 500, MaxSlippagePips);
   g_monitor = new CTradeMonitor(IpcDir, 20240101);

   // Create exec_events subdirectory (silently fails if already exists)
   FolderCreate(IpcDir + "\\exec_events");

   if(!g_cfg_rdr.Load())
      Print("WARNING: Could not load config.json — using defaults");
   else
   {
      g_last_cfg_load = TimeCurrent();
      Print("Config loaded: risk_pct=", g_cfg_rdr.Get().risk_pct);
   }

   g_hb.ForceWrite();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_hb)      { delete g_hb;      g_hb      = NULL; }
   if(g_gate)    { delete g_gate;    g_gate    = NULL; }
   if(g_cfg_rdr) { delete g_cfg_rdr; g_cfg_rdr = NULL; }
   if(g_sig_rcv) { delete g_sig_rcv; g_sig_rcv = NULL; }
   if(g_exec)    { delete g_exec;    g_exec    = NULL; }
   if(g_monitor) { delete g_monitor; g_monitor = NULL; }
}

void OnTick()
{
   g_hb.Tick();

   // Reload config periodically
   if((int)(TimeCurrent() - g_last_cfg_load) >= ConfigReloadS)
   {
      if(g_cfg_rdr.Load())
         g_last_cfg_load = TimeCurrent();
   }

   // Circuit breaker: refuse all action if config says locked
   if(g_cfg_rdr.IsValid() && g_cfg_rdr.Get().circuit_breaker_locked)
      return;

   // Manage existing position
   if(g_monitor.HasActiveTrade())
   {
      bool closed = g_monitor.Manage();
      if(closed)
      {
         g_active_ticket = 0;
         g_tp2_price     = 0.0;
      }
      return;   // Never open a second trade while one is live
   }

   // Safety gate
   string gate_reason;
   if(!g_gate.IsSafe(gate_reason))
   {
      // Only log periodically to avoid log spam
      static datetime last_gate_log = 0;
      if((int)(TimeCurrent() - last_gate_log) >= 60)
      {
         Print("Safety gate blocked: ", gate_reason);
         last_gate_log = TimeCurrent();
      }
      return;
   }

   // Check for new signal
   SSignal sig;
   if(!g_sig_rcv.Poll(sig))
      return;

   // Place order
   ulong ticket = g_exec.Execute(sig);
   if(ticket == 0)
   {
      Print("Order execution failed — signal discarded");
      g_sig_rcv.MarkConsumed(sig.signal_ts);
      return;
   }

   g_active_ticket = ticket;
   g_tp2_price     = sig.tp2_price;
   g_monitor.SetActiveTrade(ticket, sig.tp2_price);
   g_sig_rcv.MarkConsumed(sig.signal_ts);

   Print("Trade active: ticket=", ticket, " tp2=", sig.tp2_price);
}

void OnTimer() { g_hb.Tick(); }
