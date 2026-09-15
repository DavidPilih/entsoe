NO_PARAMS – načrt brez pošiljanja parametrov
==========================================

Ta način samodejno osveži načrt ob zagonu in nato enkrat na uro.
Naprava v ThingsBoardu dobi podatke pod ključem "schedule_auto".
Na MQTT temo no_params/res se urni načrt ne pošilja samodejno.

Če želiš načrt za današnji dan dobiti takoj prek MQTT:

Pošlji na: controllers/IQFleks/Entsoe/energy_prices/no_params/req
Odgovor na: controllers/IQFleks/Entsoe/energy_prices/no_params/res

Pred pošiljanjem se naroči na temo za odgovor. Pošlji JSON objekt:

{"unique_id": "06-enerArkAgg"}

"unique_id" je točno ime naprave, ki jo želiš vprašati. Drugih
parametrov ni treba poslati.

Primer odgovora na no_params/res:

{
  "success": true,
  "unique_id": "06-enerArkAgg",
  "data": [
    {"ts": 1789466400000, "value": 0.6},
    {"ts": 1789467300000, "value": 0},
    {"ts": 1789468200000, "value": -1}
  ]
}

"data" je načrt za današnji dan po 15-minutnih intervalih. "ts" je
Unix čas v milisekundah (ms) in pomeni začetek intervala. Za prikaz
lokalne ure ga pretvori v Europe/Ljubljana. "value" nima enote:
od 0 do 1 pomeni delež moči polnjenja, 0 mirovanje, -1 pa praznjenje
s polno močjo. Primer: 0.6 pomeni 60 % predvidene moči polnjenja.
"success": true pomeni uspešen odgovor, "unique_id" pa ime naprave.

Samodejna osvežitev ob zagonu in nato enkrat na uro objavi te vrednosti
v ThingsBoardu kot časovno serijo "schedule_auto". Čas točke je začetek
15-minutnega intervala, vrednost pa je delež moči od -1 do 1.
Nova osvežitev lahko spremeni prihodnje intervale.

Če zahtevek ne uspe, dobiš na no_params/res na primer:

{
  "success": false,
  "unique_id": "06-enerArkAgg",
  "error": "ValueError",
  "message": "Napaka pri pripravi načrta."
}

"error" je vrsta napake, "message" pa opis. Če JSON sporočila ni
mogoče prebrati, je "unique_id" lahko null. Pred uporabo preveri
"success".
