IZRAČUN INTERVALOV POLNJENJA IN PRAZNJENJA PREK MQTT
=================================================

1. MQTT TEMI

Zahtevek pošlji na:
controllers/IQFleks/Entsoe/energy_prices/params/req

Na odgovor se naroči na:
controllers/IQFleks/Entsoe/energy_prices/params/res

Sporočila so JSON objekti. Na odzivno temo se naroči pred pošiljanjem.
Vsakemu zahtevku določi svoj unique_id; v odgovoru se vrne isti unique_id,
da lahko prepoznaš svoj odgovor na skupni temi.


2. NAJMANJŠI ZAHTEVEK

Na params/req pošlji na primer:

{
    "unique_id": "moja-naprava-zahtevek-001",
    "capacity": 10,
    "power": 5
}

OBVEZNI PODATKI:

unique_id  - Identifikator zahtevka (niz). Ne preverja se v bazi naprav.
capacity   - Kapaciteta baterije v kWh, večja od 0.
power      - Moč v kW, večja od 0.

Podatke naprave mora poslati pošiljatelj. Storitev jih ne pridobiva iz baze.


3. NEOBVEZNI PODATKI

minimum_profit
    Prag dobička v EUR/MWh, v istih enotah kot cene v algoritmu.
    Privzeto: 0.8 EUR/MWh. To ni skupni dobiček posla v EUR.

date
    Datum izračuna v obliki "YYYY-MM-DD". Privzeto: trenutni lokalni datum.

start_time
    Od katere ure naprej naj se načrtuje, v obliki "HH:MM".
    Privzeto: trenutni lokalni čas, zaokrožen navzgor na 15 minut.
    Za celoten izbrani dan pošlji "00:00".
    Če pošlješ date, priporočamo, da pošlješ tudi start_time.
    Privzeti datum in čas se določita skupaj; pri zaokrožitvi čez polnoč
    se privzeti datum premakne na naslednji dan.

soc
    Začetna napolnjenost baterije: delež brez enote od 0 do 1. Privzeto: 0.
    Primer: 0.5 pomeni 50 %, 1 pomeni polno baterijo.

next_day
    Logična vrednost brez enote (true/false).
    true: poskusi vključiti še naslednji dan.
    false: izračun samo za izbrani dan. Privzeto: false.
    Če jutrišnjih cen ni, se lahko izračun izvede samo za izbrani dan.
    Če jutrišnjih cen ni, odgovor vsebuje le intervale izbranega dneva.

latitude
    Geografska širina v decimalnih stopinjah (°). Privzeto: 46.0569°.
    Pozitivno pomeni sever, negativno jug.

longitude
    Geografska dolžina v decimalnih stopinjah (°). Privzeto: 14.5058°.
    Pozitivno pomeni vzhod, negativno zahod.

power_factor
    Množilnik podane moči brez enote. Privzeto: 1.
    Primer: power = 5 kW in power_factor = 0.8 pomenita moč 4 kW.
    Končna moč mora biti večja od 0.

use_sun_data
    Logična vrednost brez enote (true/false).
    true: polnjenje upošteva napoved razpoložljive sončne moči.
    false: polnjenje predpostavlja polno moč. Privzeto: false.
    Omejitev polnjenja glede sončnega vzhoda in zahoda velja v obeh primerih.
    Pri 50 % ocenjene sončne moči sta za enako energijo potrebna približno
    dva intervala namesto enega. Če zahtevana napoved ni dostopna,
    lahko storitev vrne napako.

sun_factor
    Nenegativen končen množilnik sončne napovedi. Privzeto: 1.2.
    Vrednost polnjenja je min(1, sun_percent / 100 × sun_factor).
    Brez sončne napovedi je sun_percent = 100.

Števila pošiljaj brez narekovajev. Logične vrednosti pošiljaj kot true ali
false, brez narekovajev. Datume, ure in unique_id pošiljaj kot nize.
Enote so navedene samo v navodilih: v JSON pošlji npr. "power": 5,
ne "power": "5 kW". Za decimalna števila uporabljaj piko.


4. PRIMER ZAHTEVKA Z VSEMI PARAMETRI

Datum zamenjaj z želenim dnevom, za katerega so podatki dostopni.

{
    "unique_id": "moja-naprava-zahtevek-002",
    "capacity": 14.56,
    "power": 2.44,
    "minimum_profit": 1.6,
    "date": "2026-09-10",
    "start_time": "00:00",
    "soc": 0.5,
    "next_day": false,
    "latitude": 46.0569,
    "longitude": 14.5058,
    "power_factor": 1,
    "use_sun_data": true,
    "sun_factor": 1.2
}


5. USPEŠEN ODGOVOR NA params/res

Primer oblike odgovora; navedeni intervali so samo ilustracija:

{
    "charging": [
        {"time": "2026-09-10 12:00", "value": 0.6},
        {"time": "2026-09-10 12:15", "value": 1}
    ],
    "discharging": [
        {"time": "2026-09-10 18:00", "value": -1}
    ],
    "success": true,
    "unique_id": "moja-naprava-zahtevek-002"
}

charging                - Seznam intervalov polnjenja z relativno močjo od 0 do 1.
discharging             - Seznam intervalov praznjenja z vrednostjo -1.
success                 - true pomeni uspešno izveden izračun.
unique_id               - Isti identifikator kot v zahtevku.

Vsak interval traja 15 minut. "2026-09-10 12:00" pomeni interval
od 12:00 do 12:15. Časi so lokalni za Europe/Ljubljana in imajo obliko
"YYYY-MM-DD HH:MM"; niso Unix časovni žigi.

Seznama sta lahko prazna. Intervali mirovanja niso navedeni.
Odgovor vsebuje izračunan načrt; ta vmesnik načrta ne shranjuje v bazo
in ne izvaja ukazov na napravi.


6. ODGOVOR OB NAPAKI

Napaka se vrne na isto temo params/res. Primer:

{
    "success": false,
    "unique_id": "moja-naprava-zahtevek-003",
    "error": "ValueError",
    "message": "Manjkajoči podatki: capacity."
}

error   - Vrsta napake.
message - Opis težave.

Pri neberljivem JSON sporočilu je unique_id lahko null.
Pred uporabo rezultata vedno preveri success in unique_id.


7. POMOČ PREK MQTT

Na params/req lahko pošlješ:

{
    "unique_id": "pomoc-001",
    "help": true
}

Na params/res prejmeš seznam obveznih in neobveznih parametrov.
Za zahtevek za pomoč capacity in power nista potrebna.
