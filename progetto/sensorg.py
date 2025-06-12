import csv                                           # Importa il modulo per leggere file CSV
import time                                          # Importa il modulo per gestire le pause temporali
import paho.mqtt.client as mqtt                      # Importa il client MQTT per la pubblicazione su broker

city = 'glasgow'                                  # Nome della città (usato nel topic MQTT)
topic = f'/parking2025/{city}'                       # Topic base MQTT (sovrascritto successivamente)
csv_path = "../INPUT/Glasgow.csv"                 # Percorso del file CSV da leggere

client = mqtt.Client()                               # Crea un'istanza del client MQTT
client.connect('broker.emqx.io', 1883, 60)           # Connessione al broker MQTT (porta 1883, keepalive 60 sec)
client.loop_start()                                  # Avvia il ciclo di rete del client MQTT (non bloccante)

# Apertura del file CSV in modalità lettura, con encoding UTF-8
with open(csv_path, 'r', encoding='utf-8') as file:
    reader = csv.DictReader(file)                    # Crea un oggetto DictReader per leggere righe come dizionari

    for row in reader:                               # Itera su ogni riga del file CSV
        try:
            park_id = row['SystemCodeNumber']        # Estrae l’ID del parcheggio
            capacity = int(row['Capacity'])          # Converte la capacità in intero
            occupancy = int(row['Occupancy'])        # Converte l’occupazione in intero
            timestamp = row['LastUpdated']           # Timestamp dell’aggiornamento
            weekday = row['weekday']                 # Giorno della settimana

            free_spaces = capacity - occupancy       # Calcola i posti liberi

            # Crea il payload del messaggio MQTT: "timestamp,posti liberi,occupati,capacità,giorno settimana"
            payload = f"{timestamp},{free_spaces},{occupancy},{capacity},{weekday}"  # Es. 2016-10-04 09:32:46,120,40,160,1

            # Costruisce il topic finale per ogni parcheggio
            topic = f"/parking2025/{city}/{park_id}"

            # Pubblica il messaggio sul broker MQTT
            client.publish(topic, payload)

            # Stampa di conferma su console
            print(f"Sent MQTT: {payload} on topic {topic}")

            # Pausa di 0.1 secondi per evitare invii troppo rapidi
            time.sleep(0.1)

        except Exception as e:                        # Gestione errori: mostra la riga problematica e l’eccezione
            print(f"Errore con riga {row}: {e}")

client.loop_stop()                                    # Ferma il ciclo di rete del client MQTT