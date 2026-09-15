PARAMS – načrt iz podatkov, ki jih pošlješ
========================================

Pošlješ podatke o bateriji, nazaj pa dobiš intervale polnjenja in
praznjenja. Sporočila so JSON. Pred pošiljanjem se naroči na temo za odgovor.

Pošlji na: controllers/IQFleks/Entsoe/energy_prices/params/req
Odgovor na: controllers/IQFleks/Entsoe/energy_prices/params/res

Najkrajši zahtevek:

{
  "unique_id": "zahtevek-001",
  "capacity": 10,
  "power": 5
}

Obvezna polja:

unique_id – tvoja oznaka zahtevka; isti niz dobiš v odgovoru.
capacity  – kapaciteta baterije v kWh; mora biti večja od 0.
power     – moč baterije v kW; mora biti večja od 0.

Neobvezna polja (v oklepaju je privzeta vrednost):

minimum_profit – najmanjša razlika v ceni, EUR/MWh (0.8).
date           – dan izračuna, "YYYY-MM-DD" (današnji lokalni dan).
start_time     – začetek načrta, "HH:MM" (trenutni lokalni čas,
                 zaokrožen navzgor na 15 minut).
soc            – začetna napolnjenost, delež od 0 do 1 (0).
                 Primer: 0.5 pomeni 50 %.
next_day       – true/false: vključi tudi naslednji dan, če so
                 podatki zanj že na voljo (false).
latitude       – zemljepisna širina v stopinjah (46.0569°).
longitude      – zemljepisna dolžina v stopinjah (14.5058°).
power_factor   – množilnik moči brez enote (1).
                 Primer: 5 kW × 0.8 = 4 kW.
use_sun_data   – true/false: moč polnjenja prilagodi sončni napovedi (false).
sun_factor     – množilnik sončne napovedi brez enote (trenutno 100).
                 Uporabi se pri use_sun_data=true. Za občutnejše
                 zmanjšanje polnjenja ob manj sonca lahko pošlješ 1.2.
margin         – delež prilagoditve cene od 0 do 1 (0.1 oziroma 10 %).

Enot ne dodajaj v JSON vrednosti: "power": 5 pomeni 5 kW. Za cel izbrani
dan pošlji "start_time": "00:00". Datum in ura sta po Europe/Ljubljana.

Primer zahtevka s sončno napovedjo:

{
  "unique_id": "zahtevek-002",
  "capacity": 10,
  "power": 5,
  "date": "2026-09-15",
  "start_time": "00:00",
  "soc": 0.5,
  "next_day": false,
  "use_sun_data": true,
  "sun_factor": 1.2
}

Primer uspešnega odgovora na params/res:

{
  "charging": [
    {"time": "2026-09-15 12:00", "value": 0.6}
  ],
  "discharging": [
    {"time": "2026-09-15 18:00", "value": -1}
  ],
  "success": true,
  "unique_id": "zahtevek-002"
}

charging in discharging sta seznama 15-minutnih intervalov. "time" je
začetek intervala po Europe/Ljubljana, v obliki "YYYY-MM-DD HH:MM".
"value" je delež moči brez enote: pri polnjenju od 0 do 1 (1 je polna
moč), pri praznjenju vedno -1 (polna moč praznjenja). Mirovanje ni
navedeno; oba seznama sta lahko prazna. "success": true pomeni uspešen
izračun. "unique_id" pove, na kateri zahtevek se odgovor nanaša.

Primer napake na isti temi:

{
  "success": false,
  "unique_id": "zahtevek-002",
  "error": "ValueError",
  "message": "Manjkajoči podatki: power."
}

"error" je vrsta napake, "message" pa opis. Če JSON sporočila ni mogoče
prebrati, je "unique_id" lahko null. Pred uporabo preveri "success"
in "unique_id".

Za seznam polj lahko pošlješ tudi:
{"unique_id": "pomoc-001", "help": true}
Odgovor vsebuje polje "help" z obveznimi in neobveznimi polji.
