import psycopg2

conn = psycopg2.connect(
    host="10.188.20.3",
    port=5432,
    database="thingsboard",
    user="postgres",
    password="ToJe!Tabla=Stvari1912"
)

cursor = conn.cursor()

device_id = "23a78dc0-8fd7-11f1-b714-4132923502b2"

data = [
    (device_id, "2026-09-03 00:00:00+02", 0),
    (device_id, "2026-09-03 00:15:00+02", 1),
    (device_id, "2026-09-03 00:30:00+02", 1),
    (device_id, "2026-09-03 00:45:00+02", 0)
]

cursor.executemany("""
    INSERT INTO device_energy_schedule
        (device_id, timestamp, value)
    VALUES (%s, %s, %s)
""", data)

conn.commit()

cursor.close()
conn.close()