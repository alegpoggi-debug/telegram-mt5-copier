//+------------------------------------------------------------------+
//| TelegramBridge.mq5                                               |
//| Ponte tra l'app "Telegram MT5 Agent" e MetaTrader 5.             |
//| Legge i comandi da Common\Files\TGBridge\cmd_*.txt, li esegue    |
//| e scrive il risultato in res_<id>.txt. Ogni secondo scrive       |
//| state.txt (conto, posizioni, ordini) e ogni minuto symbols.txt.  |
//+------------------------------------------------------------------+
#property copyright "Telegram MT5 Agent"
#property version   "2.00"

#include <Trade\Trade.mqh>

input int TimerMs = 200;   // Frequenza di controllo comandi (ms)

CTrade trade;
string DIR = "TGBridge\\";
int    g_stateTicks = 0;
int    g_symbolTicks = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   FolderCreate("TGBridge", FILE_COMMON);
   if(!EventSetMillisecondTimer(TimerMs))
     {
      Print("TelegramBridge: impossibile avviare il timer");
      return(INIT_FAILED);
     }
   WriteSymbols();
   WriteState();
   Print("TelegramBridge avviato. Cartella: ", TerminalInfoString(TERMINAL_COMMONDATA_PATH), "\\Files\\TGBridge");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   ProcessCommands();
   g_stateTicks += TimerMs;
   g_symbolTicks += TimerMs;
   if(g_stateTicks >= 1000)
     {
      g_stateTicks = 0;
      WriteState();
     }
   if(g_symbolTicks >= 60000)
     {
      g_symbolTicks = 0;
      WriteSymbols();
     }
  }

//+------------------------------------------------------------------+
//| Utility: lettura file chiave=valore                               |
//+------------------------------------------------------------------+
string g_keys[];
string g_vals[];

bool ReadKV(const string path)
  {
   ArrayResize(g_keys, 0);
   ArrayResize(g_vals, 0);
   int fh = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(fh == INVALID_HANDLE)
      return(false);
   while(!FileIsEnding(fh))
     {
      string line = FileReadString(fh);
      int p = StringFind(line, "=");
      if(p <= 0)
         continue;
      int n = ArraySize(g_keys);
      ArrayResize(g_keys, n + 1);
      ArrayResize(g_vals, n + 1);
      g_keys[n] = StringSubstr(line, 0, p);
      g_vals[n] = StringSubstr(line, p + 1);
     }
   FileClose(fh);
   return(true);
  }

string Get(const string key, const string def = "")
  {
   for(int i = 0; i < ArraySize(g_keys); i++)
      if(g_keys[i] == key)
         return(g_vals[i]);
   return(def);
  }

double GetD(const string key, const double def = 0.0)
  {
   string v = Get(key, "");
   if(v == "")
      return(def);
   return(StringToDouble(v));
  }

long GetL(const string key, const long def = 0)
  {
   string v = Get(key, "");
   if(v == "")
      return(def);
   return(StringToInteger(v));
  }

string Clean(string s)
  {
   StringReplace(s, "|", "/");
   StringReplace(s, "\n", " ");
   StringReplace(s, "\r", " ");
   StringReplace(s, "=", "-");
   return(s);
  }

//+------------------------------------------------------------------+
//| Elaborazione comandi                                              |
//+------------------------------------------------------------------+
void ProcessCommands()
  {
   string name;
   long h = FileFindFirst(DIR + "cmd_*.txt", name, FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   string names[];
   int n = 0;
   do
     {
      ArrayResize(names, n + 1);
      names[n] = name;
      n++;
     }
   while(FileFindNext(h, name));
   FileFindClose(h);

   for(int i = 0; i < n; i++)
      HandleCommand(names[i]);
  }

// risultato del comando corrente
bool   r_ok;
long   r_ticket;
uint   r_retcode;
string r_msg;
double r_price;
double r_volume;

void ResetResult()
  {
   r_ok = false;
   r_ticket = 0;
   r_retcode = 0;
   r_msg = "";
   r_price = 0;
   r_volume = 0;
  }

void HandleCommand(const string fileName)
  {
   string path = DIR + fileName;
   // id = parte tra "cmd_" e ".txt"
   string id = StringSubstr(fileName, 4, StringLen(fileName) - 8);
   ResetResult();

   if(!ReadKV(path))
     {
      // il file potrebbe essere ancora in scrittura: riprova al prossimo giro
      return;
     }
   FileDelete(path, FILE_COMMON);   // cancello subito: ogni comando eseguito una sola volta

   string action = Get("action");
   if(action == "OPEN")
      DoOpen();
   else if(action == "MODIFY")
      DoModify();
   else if(action == "CLOSE")
      DoClose();
   else if(action == "CANCEL")
      DoCancel();
   else if(action == "QUOTE")
      DoQuote();
   else if(action == "PING")
     {
      r_ok = true;
      r_msg = "pong";
     }
   else
      r_msg = "azione sconosciuta: " + action;

   WriteResult(id);
  }

void WriteResult(const string id)
  {
   string tmp = DIR + "rtmp_" + id;
   int fh = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(fh == INVALID_HANDLE)
     {
      Print("TelegramBridge: impossibile scrivere il risultato ", id);
      return;
     }
   FileWriteString(fh, "ok=" + (r_ok ? "1" : "0") + "\n");
   FileWriteString(fh, "ticket=" + IntegerToString(r_ticket) + "\n");
   FileWriteString(fh, "retcode=" + IntegerToString(r_retcode) + "\n");
   FileWriteString(fh, "msg=" + Clean(r_msg) + "\n");
   FileWriteString(fh, "price=" + DoubleToString(r_price, 8) + "\n");
   FileWriteString(fh, "volume=" + DoubleToString(r_volume, 2) + "\n");
   FileClose(fh);
   FileMove(tmp, FILE_COMMON, DIR + "res_" + id + ".txt", FILE_COMMON | FILE_REWRITE);
  }

bool RetcodeOk(const uint rc)
  {
   return(rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_DONE_PARTIAL);
  }

void TakeTradeResult(const bool sent)
  {
   r_retcode = trade.ResultRetcode();
   r_ok = sent && RetcodeOk(r_retcode);
   r_msg = trade.ResultRetcodeDescription();
   r_price = trade.ResultPrice();
   if(r_volume <= 0)
      r_volume = trade.ResultVolume();
  }

//+------------------------------------------------------------------+
//| Volume                                                            |
//+------------------------------------------------------------------+
int VolumeDigits(const double step)
  {
   int d = 0;
   double s = step;
   while(d < 8 && MathAbs(s - MathRound(s)) > 1e-9)
     {
      s *= 10.0;
      d++;
     }
   return(d);
  }

double NormalizeVolume(const string sym, double vol, double maxLot)
  {
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(step <= 0)
      step = 0.01;
   if(maxLot > 0 && vol > maxLot)
      vol = maxLot;
   if(vol > vmax)
      vol = vmax;
   vol = MathFloor(vol / step + 1e-9) * step;
   vol = NormalizeDouble(vol, VolumeDigits(step));
   if(vol < vmin - 1e-9)
      return(0.0);
   return(vol);
  }

double LotFromRisk(const string sym, const bool isBuy, const double price, const double sl, const double riskMoney)
  {
   double loss = 0;
   ENUM_ORDER_TYPE t = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcProfit(t, sym, 1.0, price, sl, loss))
      return(0.0);
   loss = MathAbs(loss);
   if(loss <= 0)
      return(0.0);
   return(riskMoney / loss);
  }

//+------------------------------------------------------------------+
//| OPEN                                                              |
//+------------------------------------------------------------------+
void DoOpen()
  {
   string sym    = Get("symbol");
   string side   = Get("side");      // BUY / SELL
   string otype  = Get("otype");     // MARKET / LIMIT / STOP
   double price  = GetD("price");
   double sl     = GetD("sl");
   double tp     = GetD("tp");
   double vol    = GetD("volume");
   double risk   = GetD("risk_money");
   double maxLot = GetD("max_lot");
   long   magic  = GetL("magic", 770077);
   long   dev    = GetL("deviation", 30);
   string cmt    = Get("comment");

   if(!SymbolSelect(sym, true))
     {
      r_msg = "simbolo non disponibile: " + sym;
      return;
     }
   bool isBuy = (side == "BUY");
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(sl > 0)
      sl = NormalizeDouble(sl, digits);
   if(tp > 0)
      tp = NormalizeDouble(tp, digits);
   if(price > 0)
      price = NormalizeDouble(price, digits);

   MqlTick tick;
   if(!SymbolInfoTick(sym, tick) || tick.bid <= 0)
     {
      r_msg = "nessuna quotazione per " + sym + " (mercato chiuso?)";
      return;
     }
   double refPrice = (otype == "MARKET") ? (isBuy ? tick.ask : tick.bid) : price;
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);

   // distanza massima dal prezzo di entrata del segnale (solo ordini a mercato)
   double maxDist  = GetD("max_dist");
   double entryRef = GetD("entry_ref");
   if(otype == "MARKET" && maxDist > 0 && entryRef > 0 && MathAbs(refPrice - entryRef) > maxDist * point)
     {
      r_msg = "prezzo attuale " + DoubleToString(refPrice, digits) + " troppo lontano dall'entrata " +
              DoubleToString(entryRef, digits);
      return;
     }
   // SL/TP predefiniti in punti
   double slPts = GetD("sl_points");
   double tpPts = GetD("tp_points");
   if(sl <= 0 && slPts > 0)
      sl = isBuy ? refPrice - slPts * point : refPrice + slPts * point;
   if(tp <= 0 && tpPts > 0)
      tp = isBuy ? refPrice + tpPts * point : refPrice - tpPts * point;
   // aggiustamenti: SL allargato, TP anticipato
   double slAdj = GetD("sl_adj");
   double tpAdj = GetD("tp_adj");
   if(sl > 0 && slAdj != 0)
      sl = isBuy ? sl - slAdj * point : sl + slAdj * point;
   if(tp > 0 && tpAdj != 0)
      tp = isBuy ? tp - tpAdj * point : tp + tpAdj * point;
   if(sl > 0)
      sl = NormalizeDouble(sl, digits);
   if(tp > 0)
      tp = NormalizeDouble(tp, digits);

   long expMin = GetL("expiry_min", 0);
   ENUM_ORDER_TYPE_TIME tt = (expMin > 0) ? ORDER_TIME_SPECIFIED : ORDER_TIME_GTC;
   datetime expiry = (expMin > 0) ? (datetime)(TimeCurrent() + expMin * 60) : (datetime)0;

   if(vol <= 0 && risk > 0)
     {
      if(sl <= 0)
        {
         r_msg = "calcolo lotto a rischio impossibile senza stop loss";
         return;
        }
      vol = LotFromRisk(sym, isBuy, refPrice, sl, risk);
     }
   vol = NormalizeVolume(sym, vol, maxLot);
   if(vol <= 0)
     {
      r_msg = "lotto calcolato inferiore al minimo del broker";
      return;
     }
   r_volume = vol;

   trade.SetExpertMagicNumber((ulong)magic);
   trade.SetDeviationInPoints((ulong)dev);
   trade.SetTypeFillingBySymbol(sym);

   bool sent = false;
   if(otype == "MARKET")
      sent = isBuy ? trade.Buy(vol, sym, 0.0, sl, tp, cmt) : trade.Sell(vol, sym, 0.0, sl, tp, cmt);
   else if(otype == "LIMIT")
      sent = isBuy ? trade.BuyLimit(vol, price, sym, sl, tp, tt, expiry, cmt)
                   : trade.SellLimit(vol, price, sym, sl, tp, tt, expiry, cmt);
   else if(otype == "STOP")
      sent = isBuy ? trade.BuyStop(vol, price, sym, sl, tp, tt, expiry, cmt)
                   : trade.SellStop(vol, price, sym, sl, tp, tt, expiry, cmt);
   else
     {
      r_msg = "tipo ordine sconosciuto: " + otype;
      return;
     }
   TakeTradeResult(sent);
   r_ticket = (long)trade.ResultOrder();
  }

//+------------------------------------------------------------------+
//| MODIFY (sl/tp = -1 -> lascia invariato, 0 -> rimuovi)             |
//+------------------------------------------------------------------+
void DoModify()
  {
   ulong  ticket = (ulong)GetL("ticket");
   double sl     = GetD("sl", -1);
   double tp     = GetD("tp", -1);
   r_ticket = (long)ticket;

   if(PositionSelectByTicket(ticket))
     {
      string sym = PositionGetString(POSITION_SYMBOL);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double newSl = (sl < 0) ? PositionGetDouble(POSITION_SL) : NormalizeDouble(sl, digits);
      double newTp = (tp < 0) ? PositionGetDouble(POSITION_TP) : NormalizeDouble(tp, digits);
      trade.SetExpertMagicNumber((ulong)PositionGetInteger(POSITION_MAGIC));
      bool sent = trade.PositionModify(ticket, newSl, newTp);
      TakeTradeResult(sent);
      if(r_retcode == TRADE_RETCODE_NO_CHANGES)
        {
         r_ok = true;
         r_msg = "nessuna modifica necessaria";
        }
      return;
     }
   if(OrderSelect(ticket))
     {
      string sym = OrderGetString(ORDER_SYMBOL);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double price = OrderGetDouble(ORDER_PRICE_OPEN);
      double np = GetD("price", -1);
      if(np > 0)
         price = NormalizeDouble(np, digits);
      double newSl = (sl < 0) ? OrderGetDouble(ORDER_SL) : NormalizeDouble(sl, digits);
      double newTp = (tp < 0) ? OrderGetDouble(ORDER_TP) : NormalizeDouble(tp, digits);
      bool sent = trade.OrderModify(ticket, price, newSl, newTp, ORDER_TIME_GTC, 0);
      TakeTradeResult(sent);
      return;
     }
   r_msg = "ticket non trovato: " + IntegerToString((long)ticket);
  }

//+------------------------------------------------------------------+
//| CLOSE (volume <= 0 -> chiusura totale)                            |
//+------------------------------------------------------------------+
void DoClose()
  {
   ulong  ticket = (ulong)GetL("ticket");
   double vol    = GetD("volume", 0);
   long   dev    = GetL("deviation", 30);
   r_ticket = (long)ticket;
   if(!PositionSelectByTicket(ticket))
     {
      r_msg = "posizione non trovata: " + IntegerToString((long)ticket);
      return;
     }
   string sym = PositionGetString(POSITION_SYMBOL);
   double posVol = PositionGetDouble(POSITION_VOLUME);
   trade.SetDeviationInPoints((ulong)dev);
   trade.SetTypeFillingBySymbol(sym);
   bool sent;
   if(vol <= 0 || vol >= posVol - 1e-9)
     {
      r_volume = posVol;
      sent = trade.PositionClose(ticket);
     }
   else
     {
      double v = NormalizeVolume(sym, vol, 0);
      if(v <= 0)
        {
         r_msg = "volume di chiusura parziale inferiore al minimo";
         return;
        }
      r_volume = v;
      sent = trade.PositionClosePartial(ticket, v);
     }
   TakeTradeResult(sent);
  }

//+------------------------------------------------------------------+
void DoCancel()
  {
   ulong ticket = (ulong)GetL("ticket");
   r_ticket = (long)ticket;
   bool sent = trade.OrderDelete(ticket);
   TakeTradeResult(sent);
  }

//+------------------------------------------------------------------+
void DoQuote()
  {
   string sym = Get("symbol");
   if(!SymbolSelect(sym, true))
     {
      r_msg = "simbolo non disponibile: " + sym;
      return;
     }
   MqlTick tick;
   if(!SymbolInfoTick(sym, tick))
     {
      r_msg = "nessuna quotazione per " + sym;
      return;
     }
   r_ok = true;
   r_price = tick.bid;
   r_msg = "ask:" + DoubleToString(tick.ask, (int)SymbolInfoInteger(sym, SYMBOL_DIGITS)) +
           " point:" + DoubleToString(SymbolInfoDouble(sym, SYMBOL_POINT), 8);
  }

//+------------------------------------------------------------------+
//| Stato conto/posizioni/ordini                                      |
//+------------------------------------------------------------------+
void WriteState()
  {
   string tmp = DIR + "state.tmp";
   int fh = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(fh == INVALID_HANDLE)
      return;

   FileWriteString(fh, "account|" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "|" +
                   IntegerToString(AccountInfoInteger(ACCOUNT_TRADE_MODE)) + "|" +
                   DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + "|" +
                   DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + "|" +
                   AccountInfoString(ACCOUNT_CURRENCY) + "|" +
                   DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + "|" +
                   Clean(AccountInfoString(ACCOUNT_SERVER)) + "\n");
   FileWriteString(fh, "time|" + TimeToString(TimeLocal(), TIME_DATE | TIME_SECONDS) + "\n");

   string syms[];
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0)
         continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      AddUnique(syms, sym);
      int d = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      FileWriteString(fh, "pos|" + IntegerToString((long)t) + "|" + sym + "|" +
                      IntegerToString(PositionGetInteger(POSITION_TYPE)) + "|" +
                      DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + "|" +
                      DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), d) + "|" +
                      DoubleToString(PositionGetDouble(POSITION_SL), d) + "|" +
                      DoubleToString(PositionGetDouble(POSITION_TP), d) + "|" +
                      DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + "|" +
                      IntegerToString(PositionGetInteger(POSITION_MAGIC)) + "|" +
                      Clean(PositionGetString(POSITION_COMMENT)) + "|" +
                      DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), d) + "\n");
     }

   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong t = OrderGetTicket(i);
      if(t == 0)
         continue;
      string sym = OrderGetString(ORDER_SYMBOL);
      AddUnique(syms, sym);
      int d = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      FileWriteString(fh, "ord|" + IntegerToString((long)t) + "|" + sym + "|" +
                      EnumToString((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE)) + "|" +
                      DoubleToString(OrderGetDouble(ORDER_VOLUME_CURRENT), 2) + "|" +
                      DoubleToString(OrderGetDouble(ORDER_PRICE_OPEN), d) + "|" +
                      DoubleToString(OrderGetDouble(ORDER_SL), d) + "|" +
                      DoubleToString(OrderGetDouble(ORDER_TP), d) + "|" +
                      IntegerToString(OrderGetInteger(ORDER_MAGIC)) + "|" +
                      Clean(OrderGetString(ORDER_COMMENT)) + "\n");
     }
   for(int i = 0; i < ArraySize(syms); i++)
     {
      int d = (int)SymbolInfoInteger(syms[i], SYMBOL_DIGITS);
      FileWriteString(fh, "sym|" + syms[i] + "|" + DoubleToString(SymbolInfoDouble(syms[i], SYMBOL_POINT), 10) + "|" +
                      IntegerToString(d) + "|" + DoubleToString(SymbolInfoDouble(syms[i], SYMBOL_BID), d) + "|" +
                      DoubleToString(SymbolInfoDouble(syms[i], SYMBOL_ASK), d) + "\n");
     }
   FileClose(fh);
   FileMove(tmp, FILE_COMMON, DIR + "state.txt", FILE_COMMON | FILE_REWRITE);
  }

void AddUnique(string &arr[], const string v)
  {
   for(int i = 0; i < ArraySize(arr); i++)
      if(arr[i] == v)
         return;
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1);
   arr[n] = v;
  }

void WriteSymbols()
  {
   string tmp = DIR + "symbols.tmp";
   int fh = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(fh == INVALID_HANDLE)
      return;
   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
      FileWriteString(fh, SymbolName(i, false) + "\n");
   FileClose(fh);
   FileMove(tmp, FILE_COMMON, DIR + "symbols.txt", FILE_COMMON | FILE_REWRITE);
  }
//+------------------------------------------------------------------+
