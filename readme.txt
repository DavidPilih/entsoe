unique_id je sestavljen iz treh delov, ločenih z pomišljajem("-"):
    1.  2 mesti kdo posilja
            00 (simon)
            01 (andraz)
            02 (david)
    2.  13 mest timestamp (ms)
    3.  ime naprave 

primer: "02-1756723456789-Vilion EnerArk2.1"

podatki morajo biti poslani na controllers/IQFleks/Entsoe/energy_prices/req
    vhodni podatki:
        OBVEZNI:
            unique_id
            capacity
            power
            minimum_profit

        NEOBVEZNI:
            date (privzeto danasnji)(format "LLLL-MM-DD" => 2026-08-20)
            start_time (privzeto trenutna ura)(od katere ure dalje - format "HH:MM" => "20:30")
            soc (privzeto 0)(0-1 napolnjenost 0.2 = 20%)
            next_day (true, false => ali vkljuci naslednji dan.)   ne vrže napake če daš naslednji dan in ga še ni (zračuna samo za trenutni dan) lahko se preveri v odgovoru combined_with_tomorrow = true/false 
            latitude (privzeto LJ)
            longitude (privzeto LJ)
            power_factor (privzeto 1)(0-1 dejanska zmogljivost polnjenja 0.8 = 80%)
            
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
        "unique_id": "02-1756723456789-Vilion EnerArk2.1"
    }
        PRIMER 2:
    {
        "unique_id": "02-1756723456789-Vilion EnerArk2.1",
        "capacity": 14.56,
        "power": 2.44,
        "minimum_profit": 1.6
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
    "unique_id": "02-1756723456789-Vilion EnerArk2.1"
    }

    next_day: true, a combined_with_tomorrow: false !
