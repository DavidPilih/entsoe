zaženi samo client.py 
zagon zaradi cache naj bodo cache files mountani z -v
PS C:\Users\Uporabnik\Documents\entsoe_prices\Weintek> docker run -v "C:\Users\Uporabnik\Documents\entsoe_prices\Weintek\cache\prices_data:/dockApp/cache/prices_data" -v "C:\Users\Uporabnik\Documents\entsoe_prices\Weintek\cache\intervals_json:/dockApp/cache/intervals_json" -v "C:\Users\Uporabnik\Documents\entsoe_prices\Weintek\cache\sun_data:/dockApp/cache/sun_data" -v "C:\DockerData\entsoe\graph_imgs:/dockApp/graph_imgs" --env-file .env entsoe 




podatki morajo biti poslani na controllers/IQFleks/Entsoe/energy_prices/req
    vhodni podatki:
        OBVEZNI:
            unique_id
            capacity
            power
            minimum_profit
            latitude 
            longitude

        NEOBVEZNI:
            date (privzeto danasnji)
            start_time (od katere ure dalje - format "hh:mm" => "20:30")
            soc (0-1 napolnjenost 0.2 = 20%)
            next_day (true, false => ali vkljuci naslednji dan.)   ne vrže napake če daš naslednji dan in ga še ni (zračuna samo za trenutni dan) lahko se preveri v odgovoru combined_with_tomorrow = true/false 

        PRIMER: 
    {
        "capacity": 14.56,
        "power": 2.44,
        "minimum_profit": 1.6,
        "date": "2026-08-20",
        "latitude": 45.4745,
        "longitude": 13.6888,
        "start_time": "00:00",
        "soc": 0.93,
        "next_day": true,
        "unique_id": 158
    }


odgovor je poslan na controllers/IQFleks/Entsoe/energy_prices/res
    odgovor:
        charging_intervals
        discharging_intervals,
        combined_with_tomorrow,
        success,
        unique_id

    {
    "charging_intervals": ["08-20 10:30", "08-20 10:45", "08-20 11:00", "08-20 11:15", "08-20 11:30", "08-20 11:45", "08-20 12:00", "08-20 12:15", "08-20 12:30", "08-20 12:45", "08-20 13:00", "08-20 13:15", "08-20 13:30", "08-20 13:45", "08-20 14:00", "08-20 14:15", "08-20 14:30", "08-20 14:45", "08-20 15:00", "08-20 15:15"], 
    "discharging_intervals": ["08-20 00:00", "08-20 00:15", "08-20 00:30", "08-20 01:00", "08-20 05:45", "08-20 06:00", "08-20 06:15", "08-20 06:30", "08-20 06:45", "08-20 07:00", "08-20 07:15", "08-20 07:30", "08-20 07:45", "08-20 08:00", "08-20 08:15", "08-20 08:30", "08-20 09:00", "08-20 09:15", "08-20 17:15", "08-20 17:30", "08-20 17:45", "08-20 18:00", "08-20 18:15", "08-20 18:30", "08-20 18:45", "08-20 19:00", "08-20 19:15", "08-20 19:30", "08-20 19:45", "08-20 20:00", "08-20 20:15", "08-20 20:30", "08-20 20:45", "08-20 21:00", "08-20 21:15", "08-20 21:30", "08-20 21:45", "08-20 22:00", "08-20 22:15", "08-20 22:30", "08-20 23:00"], 
    "combined_with_tomorrow": false,  
    "success": true, 
    "unique_id": 158
    }

    next_day: true, a combined_with_tomorrow: false !
