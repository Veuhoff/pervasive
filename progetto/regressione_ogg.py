# Import delle librerie necessarie
from sklearn.linear_model import LinearRegression           # Modello di regressione lineare
from sklearn.metrics import mean_absolute_error            # Per calcolare l'errore assoluto medio
from joblib import dump, load                              # Per salvare e caricare i modelli
import os                                                  # Per operazioni sul filesystem
import matplotlib.pyplot as plt                            # Per visualizzazioni (non usato qui)
import pandas as pd                                        # Per la manipolazione dei dati
import numpy as np                                         # Per operazioni numeriche
from slugify import slugify                                # Per rendere nomi file "safe" con city/parkId

# Classe per il preprocessing dei dati di parcheggio
class ParkingDataPreprocessor:
    def __init__(self, df_raw, resample_interval='1h'):
        self.df_raw = df_raw.copy()                        # Copia del DataFrame originale
        self.resample_interval = resample_interval         # Intervallo di campionamento
        self.df_prepared = None                            # Variabile per salvare il risultato preprocessato

    def preprocess(self):
        # Imposta l'indice temporale
        self.df_raw.set_index('LastUpdated', inplace=True)

        # Lista per i gruppi di dati ricampionati
        resampled = []

        # Gruppo per città e parcheggio
        for (city, park_id), group in self.df_raw.groupby(['city', 'SystemCodeNumber']):
            # Ricampionamento su base oraria, media su Availability, massimo su Capacity
            group_resampled = group[['Availability', 'Capacity']].resample(self.resample_interval).agg({
                'Availability': 'mean',
                'Capacity': 'max'
            }).interpolate()  # Interpolazione per riempire buchi temporali

            # Reinserisce le colonne identificative
            group_resampled['city'] = city
            group_resampled['SystemCodeNumber'] = park_id
            resampled.append(group_resampled)

        # Unisce tutti i gruppi
        df_resampled = pd.concat(resampled).reset_index()

        # Crea feature temporali
        df_resampled['weekday'] = df_resampled['LastUpdated'].dt.dayofweek
        df_resampled['hour'] = df_resampled['LastUpdated'].dt.hour

        # Crea lag features
        for lag in [1, 2, 3]:
            df_resampled[f'lag_{lag}'] = df_resampled.groupby(['city', 'SystemCodeNumber'])['Availability'].shift(lag)

        # Rimuove righe con valori mancanti nelle lag
        df_resampled.dropna(subset=['lag_1', 'lag_2', 'lag_3'], inplace=True)

        # Salva il risultato preprocessato
        self.df_prepared = df_resampled
        return self.df_prepared

    def get_data(self):
        # Restituisce i dati preprocessati (o li calcola)
        if self.df_prepared is None:
            return self.preprocess()
        return self.df_prepared

# Import necessario per la classe del modello
from datetime import datetime

# Classe per la gestione e il training dei modelli di previsione
class ParkingModel:
    def __init__(self, city, parkId, model_dir="models"):
        self.city = city
        self.parkId = parkId
        self.model_dir = model_dir
        self.model_filename = f"model_{slugify(city)}_{slugify(parkId)}.joblib"  # Nome file safe
        self.model_path = os.path.join(model_dir, self.model_filename)
        self.model = None
        self.train_data = None
        self.test_data = None
        self.features = ['hour', 'weekday', 'lag_1', 'lag_2', 'lag_3']
        os.makedirs(model_dir, exist_ok=True)  # Crea la cartella modelli se non esiste

    def prepare_data(self, df_group, train_frac=0.8):
        df_group = df_group.copy()
        df_group['LastUpdated'] = pd.to_datetime(df_group['LastUpdated'])
        df_group.set_index('LastUpdated', inplace=True)

        # Controlla che il parcheggio esista nei dati
        if self.parkId not in df_group['SystemCodeNumber'].unique():
            raise ValueError(f"Park ID {self.parkId} not found in the data for city {self.city}")

        # Ordina per data
        df_group = df_group.sort_index()
        n = len(df_group)
        split_idx = int(n * train_frac)

        # Divide in training e test
        train_data = df_group.iloc[:split_idx]
        test_data = df_group.iloc[split_idx:]

        # Estrae X e y
        X_train = train_data[self.features]
        y_train = train_data['Availability']
        X_test = test_data[self.features]
        y_test = test_data['Availability']

        self.train_data = (X_train, y_train)
        self.test_data = (X_test, y_test)

    def train(self):
        X_train, y_train = self.train_data
        self.model = LinearRegression()
        self.model.fit(X_train, y_train)  # Allenamento
        print(f"Modello allenato per {self.city} - {self.parkId}")
    
    def save_model(self):
        dump(self.model, self.model_path)  # Salva su disco
        print(f"Modello salvato in {self.model_path}")
    
    def load_model(self):
        if os.path.exists(self.model_path):
            self.model = load(self.model_path)
            print(f"Modello caricato da {self.model_path}")
            return True
        print("Modello non trovato.")
        return False

    def predict(self, X):
        return self.model.predict(X)
    
    def evaluate(self):
        X_test, y_test = self.test_data
        yp = self.predict(X_test)
        mae = mean_absolute_error(y_test, yp)  # Calcola MAE
        print(f"MAE {self.city} - {self.parkId}: {mae:.2f}")
        return yp, mae

    def forecast_future(self, n_steps=24,  capacity=None):
        X_test, _ = self.test_data

        # Verifica presenza capacity
        if capacity is None:
            raise ValueError("Capacity deve essere fornita a forecast_future.")
            
        last_row = X_test.iloc[-1]                        # Ultima riga di test
        current_time = X_test.index[-1]                   # Ultima ora
        lag_values = list(last_row[['lag_1', 'lag_2', 'lag_3']].values)
        predictions = []

        # Previsioni future
        for _ in range(n_steps):
            current_time += pd.Timedelta(hours=1)
            x_input = pd.DataFrame([{
                'hour': current_time.hour,
                'weekday': current_time.weekday(),
                'lag_1': lag_values[-3],
                'lag_2': lag_values[-2],
                'lag_3': lag_values[-1]
            }])
            y_pred = self.model.predict(x_input)[0]

            # Clipping tra 0 e capacità
            y_pred = max(0, min(y_pred, capacity))
            predictions.append((current_time, y_pred))

            # Smorzamento tra previsione e lag
            alpha = 0.35
            smoothed = alpha * y_pred + (1 - alpha) * np.mean(lag_values[-3:])
            lag_values.append(smoothed)

        return pd.DataFrame(predictions, columns=["LastUpdated", "Availability"])

    def get_last_lags(self):
        X_test, _ = self.test_data
        last_row = X_test.iloc[-1]
        last_updated = X_test.index[-1]
        return {
            'LastUpdated': last_updated,
            'lag_1': last_row['lag_1'],
            'lag_2': last_row['lag_2'],
            'lag_3': last_row['lag_3']
        }

    def save_last_lags(self, csv_path="latest_lags.csv"):
        last_lags = self.get_last_lags()
        df_lag = pd.DataFrame([{
            'city': self.city,
            'parking': self.parkId,  
            **last_lags
        }])

        # Aggiorna o crea il file CSV
        if os.path.exists(csv_path):
            df_all = pd.read_csv(csv_path)
            df_all = df_all[~((df_all['city'] == self.city) & (df_all['parking'] == self.parkId))]
            df_all = pd.concat([df_all, df_lag], ignore_index=True)
        else:
            df_all = df_lag
        df_all.to_csv(csv_path, index=False)    

# Punto di ingresso dello script
if __name__ == "__main__":
    print("Inizio esecuzione script")
    try:
        # Caricamento dati raw
        df_raw = pd.read_csv("Parking.csv", index_col=0)
        df_raw['LastUpdated'] = pd.to_datetime(df_raw['LastUpdated'], format='%Y-%m-%d %H:%M:%S')

        # Preprocessing
        preproc = ParkingDataPreprocessor(df_raw)
        df_prepared = preproc.get_data()

        # Salvataggio dati preprocessati
        df_prepared.to_csv("dati_preprocessati.csv", index=False)
        print("Dati preprocessati salvati!")

        # Allenamento e salvataggio modelli
        for city in df_prepared['city'].unique():
            df_city = df_prepared[df_prepared['city'] == city]
            for parkId in df_city['SystemCodeNumber'].unique():
                df_group = df_city[df_city['SystemCodeNumber'] == parkId]
                if df_group.empty:
                    continue
                model = ParkingModel(city, parkId)
                model.prepare_data(df_group, train_frac=0.8)
                X_train, y_train = model.train_data
                if X_train.empty or y_train.empty:
                    print(f"{city} - {parkId}: nessun dato di training, modello non creato.")
                    continue
                model.train()
                model.save_model()
        print("Modelli allenati e salvati!")
    except Exception as e:
        print("Errore:", e)