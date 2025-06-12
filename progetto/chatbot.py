# Importazione dei moduli standard
import os                           # Modulo per operazioni sul file system
import re                           # Modulo per espressioni regolari
from datetime import datetime       # Oggetto per gestire date e orari
import pandas as pd                 # Libreria per la gestione dei dati (DataFrame)

# Importazione delle librerie di Telegram
from telegram import Update         # Oggetto Update per gestire messaggi in arrivo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
# Componenti necessari per creare il bot e gestire comandi/messaggi

from regressione_ogg import ParkingModel  # Importa il modello di previsione personalizzato

# Costanti e cartelle
DATA_FOLDER = "INPUT/"             # Cartella contenente i dati
DF_PREPARED = pd.read_csv("dati_preprocessati.csv")  # Carica dataset preprocessato
DF_PREPARED['LastUpdated'] = pd.to_datetime(DF_PREPARED['LastUpdated'])  # Converte colonna in datetime

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Risponde all'utente con le istruzioni di utilizzo
    await update.message.reply_text(
        "Ciao! 👋 \nInviami un messaggio con il formato:\n"
        "Città ParcheggioID YYYY-MM-DD HH:MM\n"
        "Esempio:\nBirmingham Shopping 2017-04-28 08:48\n"
        "Ti risponderò con la previsione dei posti liberi. 🙂"
    )

# Handler principale per i messaggi con richiesta di previsione
async def forecast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()  # Rimuove spazi bianchi iniziali/finali dal messaggio

    try:
        # Estrazione della data/ora usando una regex
        match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})$', text)
        if not match:
            raise ValueError("Formato data/ora non valido. Usa: YYYY-MM-DD HH:MM")

        dt_str = match.group(1)  # Stringa della data trovata
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")  # Converte in oggetto datetime

        # Estrae città e parcheggio dal messaggio (prima della data)
        info_part = text.replace(dt_str, "").strip()  # Rimuove la parte della data
        info_parts = info_part.split(maxsplit=1)      # Divide in [città, parcheggio]
        if len(info_parts) != 2:
            raise ValueError("Specificare città e nome parcheggio prima della data.")

        city = info_parts[0]      # Nome della città
        park_id = info_parts[1]   # Nome/id del parcheggio

        # Filtra il dataframe per la città e il parcheggio selezionati
        df_group = DF_PREPARED[
            (DF_PREPARED['city'].str.lower() == city.lower()) &  # Confronto case-insensitive
            (DF_PREPARED['SystemCodeNumber'].astype(str) == str(park_id))  # Parcheggio come stringa
        ].sort_values('LastUpdated')  # Ordina i dati in base alla data

        # Se non ci sono dati per quel parcheggio
        if df_group.empty:
            await update.message.reply_text(f"⚠️ Nessun dato per il parcheggio '{park_id}' a {city.capitalize()}.")
            return

        last_real_row = df_group.iloc[-1]  # Ultima riga disponibile nel tempo
        last_real_time = last_real_row['LastUpdated']  # Timestamp dell'ultima osservazione reale

        # Carica il modello per città e parcheggio
        model = ParkingModel(city, park_id)
        if not model.load_model():  # Se il modello non esiste
            await update.message.reply_text(f"⚠️ Modello non trovato per il parcheggio '{park_id}' a {city.capitalize()}.")
            return

        # Se la data richiesta è già presente nei dati storici
        if dt <= last_real_time:
            prev_rows = df_group[df_group['LastUpdated'] <= dt].tail(1)  # Trova la riga corrispondente o precedente
            if prev_rows.empty:
                await update.message.reply_text("⚠️ Non ci sono dati storici sufficienti per questa data.")
                return

            last_row = prev_rows.iloc[0]  # Ottiene l'ultima osservazione storica
            lag_1 = last_row['lag_1']     # Valore del primo lag
            lag_2 = last_row['lag_2']     # Valore del secondo lag
            lag_3 = last_row['lag_3']     # Valore del terzo lag
            hour = dt.hour                # Ora del giorno
            weekday = dt.weekday()        # Giorno della settimana (0=Lunedì)

            # Prepara il dataset per la previsione
            X = pd.DataFrame([{
                'hour': hour,
                'weekday': weekday,
                'lag_1': lag_1,
                'lag_2': lag_2,
                'lag_3': lag_3
            }])
            predicted = model.model.predict(X)[0]  # Esegue la previsione
        else:
            # Caso di previsione futura: propagazione dei lag
            lag_1 = last_real_row['lag_1']
            lag_2 = last_real_row['lag_2']
            lag_3 = last_real_row['lag_3']
            current_time = last_real_time
            alpha = 0.3  # Fattore di smorzamento (exponential smoothing)
            capacity = df_group['Capacity'].max()  # Capacità massima del parcheggio
            lag_values = [lag_1, lag_2, lag_3]     # Lista iniziale dei lag

            # Esegue la propagazione oraria fino alla data richiesta
            while current_time < dt:
                current_time += pd.Timedelta(hours=1)  # Aumenta di un'ora
                x_input = pd.DataFrame([{
                    'hour': current_time.hour,
                    'weekday': current_time.weekday(),
                    'lag_1': lag_values[-3],
                    'lag_2': lag_values[-2],
                    'lag_3': lag_values[-1]
                }])
                y_pred = model.model.predict(x_input)[0]  # Previsione per quell'ora
                y_pred = max(0, min(y_pred, capacity))    # Limita ai valori realistici

                # Smorzamento esponenziale
                smoothed = alpha * y_pred + (1 - alpha) * (sum(lag_values[-3:]) / 3)
                lag_values.append(smoothed)  # Aggiunge il nuovo valore ai lag

            predicted = lag_values[-1]  # Ultima previsione dopo la propagazione

        # Risposta all’utente con i risultati
        await update.message.reply_text(
            f"🔮 Previsione per il parcheggio '{park_id}' a {city.capitalize()}\n"
            f"🕒 Orario: {dt_str}\n"
            f"🚗 Posti liberi previsti: {round(predicted, 0)}"
        )

    except Exception as e:
        # Gestione degli errori imprevisti
        await update.message.reply_text(f"❌ Errore: {e}")

# Punto di ingresso principale
if __name__ == "__main__":
    import logging                      # Modulo logging per messaggi di debug/info
    logging.basicConfig(level=logging.INFO)  # Imposta livello di logging

    TOKEN = "7268780527:AAG-ZEo5j3sgTBrU5Gvyi9Ln90dVPrx3ddo"  # Token del bot

    app = ApplicationBuilder().token(TOKEN).build()  # Crea l'applicazione Telegram

    app.add_handler(CommandHandler("start", start))  # Aggiunge handler per /start
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forecast_handler))  # Handler per messaggi testuali

    print("Bot avviato...")            # Messaggio di avvio console
    app.run_polling()                  # Avvia polling per ascoltare i messaggi