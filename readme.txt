MQTT VMESNIK: IZRAČUN ZA NAPRAVO IZ BAZE
======================================

Ta vmesnik sprejme ime naprave, pridobi njene podatke iz baze in vrne
razpored z vrednostmi -1, 0 in 1. Če želiš vse parametre poslati sam in
prejeti charging_intervals/discharging_intervals, uporabi params vmesnik,
opisan v readme_params.txt.


1. MQTT TEME

Zahtevke pošiljaj na:
controllers/IQFleks/Entsoe/energy_prices/req

Odgovore in napake spremljaj na:
controllers/IQFleks/Entsoe/energy_prices/res

Pridobljene podatke naprave in napake spremljaj na:
controllers/IQFleks/Entsoe/energy_prices/debug

Sporočila so JSON objekti v UTF-8. Na odzivno temo se naroči pred
pošiljanjem. Na skupni odzivni temi preverjaj unique_id.


2. KAJ POSLATI NA /req

{
    "unique_id": "06-enerArkAgg"
}

unique_id je obvezen neprazen niz in pomeni TOČNO IME naprave v bazi.
Ni poljuben identifikator zahtevka in ni UUID naprave. Ne dodajaj novega
časovnega žiga ali predpone, če ta ni del dejanskega imena naprave.

Pošlji samo unique_id. mqtt_server.py dodatnih vhodnih parametrov ne
posreduje clientu. Za lastno kapaciteto, moč, datum, margin in druge
nastavitve uporabi /params/req (glej readme_params.txt).


3. PODATKI, KI JIH MORA IMETI NAPRAVA V BAZI

Ključ                     Enota       Zahteva
------------------------------------------------------------------
total_capacity[kWh]       kWh         Kapaciteta, večja od 0.
max_charge_power[kW]       kW          Moč, večja od 0.
SOC[%]                    %           Napolnjenost od 0 do 100.

Storitev prebere najnovejše vrednosti teh ključev za zahtevano ime.
Uporabi long_v, če obstaja, sicer dbl_v. Manjkajoča polja, NULL,
neveljavna števila in vrednosti zunaj dovoljenega območja povzročijo napako.
SOC = 0 je veljaven. SOC iz baze se deli s 100: 50 % postane delež 0.5.

Pri tem vmesniku se uporabijo privzete nastavitve izračuna:
- minimum_profit: 0.8 EUR/MWh po trenutnem izračunu; ni skupni znesek EUR.
- margin: 0.1, delež brez enote (10 % na vsaki strani).
  Nakupna cena × 1.1, prodajna cena × 0.9.
- power_factor: 1, množilnik moči brez enote.
- latitude: 46.0569 decimalne stopinje (° severno).
- longitude: 14.5058 decimalne stopinje (° vzhodno).
- next_day: false, brez enote; naslednji dan se ne vključuje.
- use_sun_data: false, brez enote; vremenska napoved ne prilagaja moči.
  Dnevno okno glede sončnega vzhoda in zahoda ostane v veljavi.
- Datum in začetni čas: trenutni lokalni čas obdelave, zaokrožen navzgor
  na 15 minut. Zaokroževanje čez polnoč premakne tudi datum.


4. USPEŠEN ODGOVOR NA /res

Primer oblike odgovora (razpored je ilustrativen):

{
    "success": true,
    "unique_id": "06-enerArkAgg",
    "data": [
        {"timestamp": 1789034400000, "value": 1},
        {"timestamp": 1789035300000, "value": 0},
        {"timestamp": 1789036200000, "value": -1}
    ]
}

success     - Logična vrednost brez enote; true pomeni uspešen izračun.
unique_id   - Ime naprave iz zahtevka.
data        - Razpored po intervalih; lahko je prazen seznam.
timestamp   - Unix časovni žig v MILISEKUNDAH od 1970-01-01 UTC.
              Predstavlja začetek 15-minutnega intervala.
value       - Ukaz brez enote, ne moč v kW in ne odstotek:
               1 = polnjenje
               0 = mirovanje
              -1 = praznjenje

Zaporedna 15-minutna intervala sta oddaljena 900000 ms. Za prikaz lokalne
ure pretvori timestamp v časovni pas Europe/Ljubljana.

Odgovor vsebuje iste časovne žige in vrednosti, ki se predajo zapisovanju
v bazo. Client razpored preda tudi ThingsBoardu kot schedule_auto.
Odgovor success se pošlje pred zapisovanjem; ni potrdilo, da je baza zapis
že uspešno shranila. Zapisovanje v bazo poteka v ozadju.
Ta odgovor ne vsebuje charging_intervals ali discharging_intervals.


5. DEBUG NA /debug

Pred izračunom se pošljejo pridobljeni podatki naprave, na primer:

{
    "unique_id": "06-enerArkAgg",
    "capacity": 10,
    "power": 5,
    "soc": 0.5
}

capacity je v kWh, power v kW, soc pa delež brez enote od 0 do 1.
To pomeni 50 % napolnjenosti, čeprav je v izvorni bazi vrednost 50.

Če del podatkov manjka ali je neveljaven, debug najprej pokaže podatke,
ki jih je baza vrnila, z izvirnimi ključi in enotami:

{
    "unique_id": "06-enerArkAgg",
    "database_data": {
        "total_capacity[kWh]": 10,
        "SOC[%]": 50
    }
}

V tem primeru manjka max_charge_power[kW]. Nato sledi opis napake.


6. NAPAKE NA /res IN /debug

Primer:

{
    "success": false,
    "unique_id": "06-enerArkAgg",
    "error": "ValueError",
    "message": "Naprava '06-enerArkAgg': max_charge_power[kW]: podatek manjka v bazi"
}

error vsebuje vrsto napake, message pa opis in konkretna problematična
polja. Če poizvedba ne vrne nobenega od zahtevanih podatkov, so navedeni
vsi manjkajoči ključi; preveri tudi pravilnost imena naprave.

Napake sprejema in pridobivanja podatkov se pošljejo na /res in /debug.
Napake obdelave, ki jih client vrne na /res, se zrcalijo še na /debug.
Pri neberljivem JSON je unique_id lahko null. Težave pri poznejšem
zapisovanju v ozadju se lahko pojavijo samo v izpisu programa.


7. KATERA SKRIPTA IMA KATERO VLOGO

mqtt_server.py posluša /req, pridobi podatke in neposredno pokliče
client.process_request v istem procesu. Za ta način se zažene
mqtt_server.py; client.py se ne zaganja še posebej kot drugi poslušalec.

server.py je pošiljatelj za ta vmesnik: bere req_data.json, prvič pošlje
po 3 sekundah in nato vsakih 10 sekund znova prebere ter pošlje datoteko.
V req_data.json zato nastavi unique_id z dejanskim imenom naprave.

server_params.py in client_params.py uporabljata ločeni temi params/req
in params/res. Njuni vhodni podatki in odgovor so opisani v readme_params.txt.
