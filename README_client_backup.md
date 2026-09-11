# MQTT izračun s podanimi parametri — client_backup.py

`client_backup.py` sprejme podatke naprave iz MQTT zahtevka, pokliče algoritem in vrne intervale polnjenja ter praznjenja. Ne bere ali zapisuje podatkov v podatkovno bazo in ne pošilja telemetrije v ThingsBoard. `unique_id` vrne nespremenjen; naprave ne išče v bazi.

Algoritem še vedno pridobiva cene in sončne podatke prek obstoječih API-jev oziroma lokalnega predpomnilnika ter ustvarja grafe. Podatki naprave pa izvirajo iz zahtevka in spodnjih privzetih vrednosti.

## Zagon

V mapi projekta namesti odvisnosti in zaženi program:

```powershell
python -m pip install -r requirements.txt
python client_backup.py
```

V `.env` mora biti nastavljen `ENTSOE_API_KEY`, ker ga ob uvozu zahteva `api_client.py`. `USER_DB` in `PASSWORD_DB` za ta program nista potrebna.

MQTT broker je trenutno nastavljen na `10.188.20.3:1884`; prijava je določena v `client_backup.py`. Program mora imeti dostop do brokerja in uporabljenih podatkovnih API-jev. Ustaviš ga s `Ctrl+C`.

## MQTT temi

| Namen | Tema |
| --- | --- |
| Zahtevek | `controllers/IQFleks/Entsoe/energy_prices/params/req` |
| Odgovor ali napaka | `controllers/IQFleks/Entsoe/energy_prices/params/res` |

Pred pošiljanjem zahtevka se naroči na odzivno temo. Sporočila so JSON objekti v UTF-8. Odgovor povežeš z zahtevkom prek `unique_id`.

## Vhodni parametri

| Parameter | Obvezen / privzeto | Pomen |
| --- | --- | --- |
| `unique_id` | Obvezen | Identifikator zahtevka; uporabi niz. |
| `capacity` | Obvezen | Kapaciteta baterije v kWh, večja od 0. |
| `power` | Obvezen | Moč v kW, večja od 0. |
| `minimum_profit` | `0.8` | Parameter minimalnega dobička, posredovan algoritmu. |
| `date` | Lokalni datum obdelave | Datum izračuna v obliki `YYYY-MM-DD`. |
| `start_time` | Lokalni čas, zaokrožen navzgor na 15 minut | Začetek trgovanja, oblika `HH:MM`. |
| `soc` | `0` | Začetna napolnjenost od 0 do 1; `0.5` pomeni 50 %. |
| `next_day` | `false` | Poskusi vključiti naslednji dan, če so njegove cene dostopne. |
| `latitude` | `46.0569` | Geografska širina. |
| `longitude` | `14.5058` | Geografska dolžina. |
| `power_factor` | `1` | Množilnik moči; `0.8` pomeni 80 % podane moči. Končna moč mora ostati pozitivna. |
| `use_sun_data` | `false` | Upoštevanje napovedi sončnega obsevanja pri polnjenju. |

Števila pošlji kot JSON števila, logične vrednosti pa kot `true` oziroma `false`, brez narekovajev. Pri izrecno podanem datumu je smiselno podati tudi `start_time`: sicer se uporabi trenutni lokalni čas računalnika. Privzeti datum in čas se izračunata skupaj; zaokroževanje pred polnočjo lahko premakne datum na naslednji dan. Računalnik naj uporablja časovni pas Europe/Ljubljana.

Pri `use_sun_data: false` algoritem predpostavi polno moč znotraj dovoljenega dnevnega okna. Omejitev glede sončnega vzhoda in zahoda ostaja aktivna. Pri `true` uporabi oceno `obsevanje / 1000 × 100`, omejeno na 0–100 %: pri 50 % sta za enako energijo potrebna dva intervala namesto enega. Manjkajoča napoved lahko povzroči napako izračuna.

### Minimalni zahtevek

```json
{
  "unique_id": "test-001",
  "capacity": 10,
  "power": 5
}
```

### Zahtevek z dodatnimi parametri

Datum prilagodi dnevu, za katerega želiš izračun in so podatki dostopni.

```json
{
  "unique_id": "test-002",
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
  "use_sun_data": true
}
```

## Odgovor

Program vrne prvi rezultat algoritma. Drugi rezultat, namenjen zapisovanju v bazo, ignorira. Primer oblike odgovora (intervali so ilustrativni):

```json
{
  "charging_intervals": [
    "2026-09-10 12:00",
    "2026-09-10 12:15"
  ],
  "discharging_intervals": [
    "2026-09-10 18:00"
  ],
  "combined_with_tomorrow": false,
  "use_sun_data": true,
  "success": true,
  "unique_id": "test-002"
}
```

- `charging_intervals`: začetki 15-minutnih intervalov polnjenja.
- `discharging_intervals`: začetki 15-minutnih intervalov praznjenja.
- Časi so lokalni za Europe/Ljubljana v obliki `YYYY-MM-DD HH:MM`, ne Unix časovni žigi. Intervali mirovanja niso posebej navedeni; seznama sta lahko prazna.
- `combined_with_tomorrow`: ali je bil naslednji dan dejansko vključen. Lahko je `false` tudi ob `next_day: true`, če jutrišnje cene niso dostopne.
- `use_sun_data`: ali je izračun upošteval sončno napoved.
- `success: true` pomeni uspešen izračun, ne zapis v bazo.

### Napaka

Napake obdelave se pošljejo na isto odzivno temo:

```json
{
  "success": false,
  "unique_id": "test-003",
  "error": "ValueError",
  "message": "Manjkajoči podatki: capacity."
}
```

`error` vsebuje ime izjeme, `message` pa opis težave. Pri napaki dekodiranja ali branja JSON je `unique_id` lahko `null`.

## Pomoč prek MQTT

Na `params/req` pošlji:

```json
{
  "unique_id": "help-001",
  "help": true
}
```

Program na `params/res` vrne seznam obveznih in neobveznih parametrov brez zagona izračuna.
