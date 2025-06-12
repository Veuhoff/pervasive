import subprocess  # Importa il modulo subprocess per eseguire processi esterni (altri script Python)

# Avvia lo script "sensorb.py" in background, senza attendere la sua terminazione
subprocess.Popen(["python", "progetto/sensorb.py"])

# Avvia lo script "sensorg.py" in background
subprocess.Popen(["python", "progetto/sensorg.py"])

# Avvia lo script "sensornk.py" in background
subprocess.Popen(["python", "progetto/sensornk.py"])

# Avvia lo script "sensornm.py" in background
subprocess.Popen(["python", "progetto/sensornm.py"])