from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_mqtt import Mqtt
from datetime import datetime
import traceback
import pandas as pd
from datetime import timedelta

from statistics import mean
from google.cloud import firestore

from slugify import slugify
import numpy as np
import os
from joblib import load
import sys

from regressione_ogg import ParkingModel
import os

print("Cartella di lavoro:", os.getcwd())
print("File esiste?", os.path.exists("dati_preprocessati.csv"))
input_folder = "../INPUT/"
app = Flask(__name__)

# Configurazione MQTT
app.config['MQTT_BROKER_URL'] = 'broker.emqx.io'
app.config['MQTT_BROKER_PORT'] = 1883
app.config['MQTT_KEEPALIVE'] = 60
app.config['MQTT_TOPIC'] = '/parking2025/#'

mqtt = Mqtt(app)

# Inizializza Firestore
db = firestore.Client.from_service_account_json(
    #r"firestore1\\credentials.json",
    'credentials.json',
    database='smartpark'
)




# --------------------- ROUTE HTML ---------------------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/reqpar', methods=['POST'])
def reqpar():
    username = request.form['username']
    password = request.form['password']
    user_ref = db.collection('users').document(username).get()
    if user_ref.exists and user_ref.to_dict()['password'] == password:
        return redirect(url_for('map'))
    return render_template('index.html',error=True)

@app.route('/register', methods=['POST'])
def register():
    username = request.form['username']
    email = request.form['email']
    password = request.form['password']
    db.collection('users').document(username).set({
        'email': email,
        'password': password
    })
    return redirect(url_for('index'))

@app.route('/recover', methods=['POST'])
def recover():
    email = request.form['email']
    users = db.collection('users').where('email', '==', email).stream()
    for user in users:
        return f"La tua password è: {user.to_dict()['password']}"
    return "Email non trovata"

@app.route('/map')
def map():
    return render_template('map.html')

# Mappa per città
@app.route('/mapbir')
def map_bir():
    return render_template('mapbir.html')

@app.route('/mapnork')
def map_nork():
    return render_template('mapnork.html')

@app.route('/mapnott')
def map_nott():
    return render_template('mapnott.html')

@app.route('/mapglas')
def map_glas():
    return render_template('mapglas.html')

# Grafici per città 
@app.route('/graphb')
def graph_bir():
    park_id = request.args.get('parkId', None)
    return render_template('graphb.html', park_id=park_id)

@app.route('/graphg')
def graph_glas():
    park_id = request.args.get('parkId', None)
    return render_template('graphg.html', park_id=park_id)

@app.route('/graphnk')
def graph_nork():
    park_id = request.args.get('parkId', None)
    return render_template('graphnk.html', park_id=park_id)

@app.route('/graphnm')
def graph_nott():
    park_id = request.args.get('parkId', None)
    return render_template('graphnm.html', park_id=park_id)

# --------------------- MQTT ---------------------

@mqtt.on_connect()
def on_connect(client, userdata, flags, rc):
    print("Connected to MQTT broker with code", rc)
    mqtt.subscribe(app.config['MQTT_TOPIC'])

@mqtt.on_message()
def handle_mqtt_message(client, userdata, message):
    
    try:
        payload = message.payload.decode()
        print(f"MQTT received: {payload}")
        topic_parts = message.topic.split('/')
        city = topic_parts[-2].lower()
        park_id = topic_parts[-1]
        parts = payload.split(',')
        ts = datetime.fromisoformat(parts[0])
        availability = float(parts[1])
        occupancy = float(parts[2])
        capacity = float(parts[3])
        weekday = parts[4]

        doc = {
            'city': city,
            'parcheggio_id': park_id,
            'timestamp': ts.isoformat(),
            'availability': availability,
            'occupancy': occupancy,
            'capacity': capacity,
            'weekday': weekday 
        }
         # ID univoco: park_id + timestamp
        doc_id = f"{park_id}_{ts.isoformat()}"
        db.collection('occupazione_parcheggio').document(doc_id).set(doc)
        print(f"Saved to Firestore with ID '{doc_id}': {doc}")
        
    except Exception as e:
        print("Error processing message:", e) 
    
@mqtt.on_disconnect()
def handle_disconnect():
    print("CLIENT DISCONNECTED")

# --------------------- API ---------------------

@app.route('/average_availability/<city>', methods=['GET'])
def average_availability(city):
    time_str = request.args.get('time')
    day = request.args.get('day')
    park_id_filter = request.args.get('parkId') 
    if not time_str:
        return jsonify({'error': 'Manca il parametro time'}), 400

    try:
        target_time = datetime.strptime(time_str, '%H:%M').time()
    except ValueError:
        return jsonify({'error': 'Formato time non valido, usa HH:MM'}), 400

    try:
        query = db.collection('occupazione_parcheggio') \
            .where('city', '==', city.lower())
        
        if park_id_filter:
            query = query.where('parcheggio_id', '==', park_id_filter)

        if day:
            query = query.where('weekday', '==', day.lower())

        docs = query.stream()

        park_data = {}
        for d in docs:
            data = d.to_dict()
            ts = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
            park_id = data['parcheggio_id']

            # Filtro sull'orario

            #if ts.time().hour == target_time.hour and ts.time().minute == target_time.minute:
            if ts.time() <= target_time:
                park_data.setdefault(park_id, []).append(data.get('availability'))

        result = {}
        for park_id, values in park_data.items():
            result[park_id] = {
                'average': round(mean(values), 1) if values else None,
                'samples': len(values)
            }

        return jsonify(result), 200

    except Exception as e:
        app.logger.error(f"Errore in average_availability: {e}")
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb.splitlines()}), 500

@app.route('/sensors/<city>/<park_id>', methods=['GET'])
def get_park_data(city, park_id):
    try:
        docs = db.collection('occupazione_parcheggio') \
            .where('city', '==', city.lower()) \
            .where('parcheggio_id', '==', park_id) \
            .order_by('timestamp') \
            .stream()

        result = []
        for d in docs:
            data = d.to_dict()
            result.append({
                'data': data['timestamp'],
                'availability': data.get('availability'),
                'occupancy': data.get('occupancy'),
                'capacity': data.get('capacity'),
            })
        return jsonify(result), 200

    except Exception as e:
        app.logger.error(f"Errore in get_park_data: {e}")
        tb = traceback.format_exc()
        return jsonify({'error': str(e), 'trace': tb.splitlines()}), 500

@app.route('/sensors/<city>/list', methods=['GET'])
def list_parks(city):
    docs = db.collection('occupazione_parcheggio') \
        .where('city', '==', city.lower()) \
        .stream()
    parks = sorted({d.to_dict()['parcheggio_id'] for d in docs})
    return jsonify(parks), 200

#(route per i grafici)
@app.route('/forecast_extended/<city>/<park_id>', methods=['GET'])
def forecast_extended(city, park_id):
    date = request.args.get('date')  # formato: YYYY-MM-DD
    time = request.args.get('time')  # formato: HH:MM

    if not date or not time:
        return jsonify({'error': 'Parametri date e time mancanti'}), 400
    try:
        df_prepared = pd.read_csv("dati_preprocessati.csv")
        # Filtra solo per la città e il parcheggio richiesto
        df_group = df_prepared[
            (df_prepared['city'].str.lower() == city.lower()) &
            (df_prepared['SystemCodeNumber'].astype(str) == str(park_id))
        ]
        if df_group.empty:
            return jsonify({'error': 'Nessun dato per questo parcheggio'}), 404

        model = ParkingModel(city=city, parkId=park_id)
        if not model.load_model():
            return jsonify({'error': 'Modello non trovato'}), 404

        dt_req = pd.to_datetime(f"{date} {time}")
        last_data_time = pd.to_datetime(df_group['LastUpdated']).max()
       
        model.prepare_data(df_group, train_frac=0.8)

        if dt_req <= last_data_time:
            # Previsione "normale" (dentro i dati reali)
            weekday = dt_req.dayofweek
            hour = dt_req.hour
            last_lags = model.get_last_lags()
            x_input = pd.DataFrame([{
                'hour': hour,
                'weekday': weekday,
                'lag_1': last_lags['lag_1'],
                'lag_2': last_lags['lag_2'],
                'lag_3': last_lags['lag_3']
            }])
            pred = model.model.predict(x_input)[0]
            capacity = df_group['Capacity'].max()
            pred = max(0, min(pred, capacity))
            return jsonify({
                "forecast": [
                    {
                        "data": dt_req.strftime("%Y-%m-%dT%H:%M:%S"),
                        "forecast": int(round(pred))
                    }
                ]
            })
        else:
            # Previsione futura (oltre i dati reali)
            n_steps = int((dt_req - last_data_time) / pd.Timedelta(hours=1))
            if n_steps <= 0:
                return jsonify({'error': 'Data richiesta non valida'}), 400
            
            capacity = df_group['Capacity'].max()
            forecast_df = model.forecast_future(n_steps=n_steps, capacity=capacity)
            # Trova la previsione per la data richiesta
            row = forecast_df[forecast_df['LastUpdated'] == dt_req]
            if row.empty:
                return jsonify({'error': 'Previsione non disponibile per questa data/ora'}), 404
            pred = row['Availability'].values[0]
            return jsonify({
                "forecast": [
                    {
                        "data": dt_req.strftime("%Y-%m-%dT%H:%M:%S"),
                        "forecast": int(round(pred))
                    }
                ]
            })
    except Exception as e:
        import traceback
        print("Errore forecast_map:", e)
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
        
#route per la mappa delle città
@app.route('/forecast_map/<city>', methods=['GET'])
def forecast_map(city):
    date = request.args.get('date')
    time = request.args.get('time')
    if not date or not time:
        return jsonify({'error': 'Parametri date e time mancanti'}), 400
    try:
        df_prepared = pd.read_csv("dati_preprocessati.csv")
        result = {}
        for parkId in df_prepared[df_prepared['city'].str.lower() == city.lower()]['SystemCodeNumber'].unique():
            df_group = df_prepared[(df_prepared['city'].str.lower() == city.lower()) & (df_prepared['SystemCodeNumber'] == parkId)]
            if df_group.empty:
                result[parkId] = None
                continue
            model = ParkingModel(city=city, parkId=parkId)
            if not model.load_model():
                result[parkId] = None
                continue
            dt = pd.to_datetime(f"{date} {time}")
            weekday = dt.dayofweek
            hour = dt.hour
            model.prepare_data(df_group, train_frac=0.8)
            last_lags = model.get_last_lags()
            x_input = pd.DataFrame([{
                'hour': hour,
                'weekday': weekday,
                'lag_1': last_lags['lag_1'],
                'lag_2': last_lags['lag_2'],
                'lag_3': last_lags['lag_3']
            }])
            pred = model.model.predict(x_input)[0]
            capacity = df_group['Capacity'].max()
            pred = max(0, min(pred, capacity))
            result[parkId] = int(round(pred))
        return jsonify(result)
    except Exception as e:
        import traceback
        print("Errore forecast_map:", e)
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Route per la previsione settimanale da inserire nel grafico 
@app.route('/forecast_week/<city>/<park_id>', methods=['GET'])
def forecast_week(city, park_id):
    try:
        df_prepared = pd.read_csv("dati_preprocessati.csv")
        df_group = df_prepared[
            (df_prepared['city'].str.lower() == city.lower()) &
            (df_prepared['SystemCodeNumber'].astype(str) == str(park_id))
        ]
        if df_group.empty:
            return jsonify({'error': 'Nessun dato per questo parcheggio'}), 404

        model = ParkingModel(city=city, parkId=park_id)
        if not model.load_model():
            return jsonify({'error': 'Modello non trovato'}), 404

        last_data_time = pd.to_datetime(df_group['LastUpdated']).max()
        model.prepare_data(df_group, train_frac=0.8)
        capacity = df_group['Capacity'].max()
        n_steps = 24 * 15  # 7 giorni futuri, 1 previsione per ora

        forecast_df = model.forecast_future(n_steps=n_steps, capacity=capacity)
        # Prepara la risposta per il grafico
        result = [
            {
                "data": row["LastUpdated"].strftime("%Y-%m-%dT%H:%M:%S"),
                "forecast": int(round(row["Availability"]))
            }
            for _, row in forecast_df.iterrows()
        ]
        return jsonify({"forecast": result})
    except Exception as e:
        import traceback
        print("Errore forecast_week:", e)
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
# --------------------- MAIN ---------------------

if __name__ == '__main__':
    app.run(debug=True)





'''
MODEL_DIR = os.path.join( "Regressione", "models")



@app.route('/sensors/<city>/<park_id>/with_forecast', methods=['GET'])
def get_park_data_with_forecast(city, park_id):
    try:
        # Recupera dati da Firestore
        docs = db.collection('occupazione_parcheggio') \
            .where('city', '==', city.lower()) \
            .where('parcheggio_id', '==', park_id) \
            .order_by('timestamp') \
            .stream()
        print(docs)
        raw_data = []
        for d in docs:
            data = d.to_dict()
            raw_data.append({
                'data': data['timestamp'],
                'availability': data['availability'],
                'occupancy': data['occupancy'],
                'capacity': data['capacity']
            })

        if not raw_data:
            print('ERRORE')
            return jsonify({'error': 'Nessun dato trovato'}), 404
        

        # Carica il modello
        model_filename = f"model_{slugify(city)}_{slugify(park_id)}.joblib"
        model_path = os.path.join(MODEL_DIR, model_filename)
        if not os.path.exists(model_path):
            print('ERRORE MODELLO')
            return jsonify({"error": f"Modello non trovato per {city} - {park_id}"}), 404

        model = load(model_path)

        enriched = []
        last3_avail = []
        
        for entry in raw_data:
            try:
                dt = datetime.strptime(entry['data'], "%Y-%m-%dT%H:%M:%S")
            except Exception as e:
             
                return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

                continue

            hour = dt.hour
            weekday = dt.weekday()
            availability = entry['availability']
            print(f'availability {availability}')
            last3_avail.append(availability)
            if len(last3_avail) > 3:
                last3_avail.pop(0)

            # Previsione solo se ci sono almeno 3 valori precedenti
            if len(last3_avail) == 3:
                features = [hour, weekday] + last3_avail
                X = np.array(features).reshape(1, -1)
                print(features)
                print()
                print(X)
                predicted = model.predict(X)[0]
                print(predicted)
            else:
                predicted = None

            enriched.append({
                'data': entry['data'],
                'availability': availability,
                'occupancy': entry['occupancy'],
                'capacity': entry['capacity'],
                'forecast': round(predicted, 0) if predicted is not None else None
            })
        print(enriched)
        return jsonify(enriched)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


MODEL_DIR = "../Regressione/models"
lag_df = pd.read_csv("latest_lags.csv")
#controllare da qui 

@app.route('/forecast_extended/<city>/<park_id>', methods=['GET'])

def predict(city, parkId):
    try:
        datetime_str = request.args.get('datetime')  # '2025-06-04T15:00:00'
        if not datetime_str:
            return jsonify({'error': 'Parametro datetime mancante'}), 400
        
        dt = datetime.fromisoformat(datetime_str)

        if not all([city, parkId, dt]):
            return jsonify({'error': 'Parametri mancanti'}), 400

        # Parsing data/ora
        hour = dt.hour
        weekday = dt.weekday()

        # Costruisci il nome del file modello
        model_filename = f"model_{slugify(city)}_{slugify(parkId)}.joblib"
        model_path = os.path.join(MODEL_DIR, model_filename)

        if not os.path.exists(model_path):
            return jsonify({'error': 'Modello non trovato'}), 404

        model = load(model_path)

        # Cerca le ultime lag salvate nel CSV per questa città e parcheggio
        lag_row = lag_df[(lag_df['city'] == city) & (lag_df['parking'] == int(parkId))]
        if lag_row.empty:
            return jsonify({'error': 'Lag iniziali non trovati'}), 404

        lag_1 = float(lag_row['lag_1'].values[0])
        lag_2 = float(lag_row['lag_2'].values[0])
        lag_3 = float(lag_row['lag_3'].values[0])

        # Predizione
        X = pd.DataFrame([{
            'hour': hour,
            'weekday': weekday,
            'lag_1': lag_1,
            'lag_2': lag_2,
            'lag_3': lag_3
        }])

        y_pred = model.predict(X)[0]

        return jsonify({
            'forecast': round(y_pred),
            'datetime': dt.isoformat(),
            'parkId': parkId,
            'city': city
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    '''